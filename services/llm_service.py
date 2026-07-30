"""
services/llm_service.py — Toda la lógica del SDK de Google GenAI:
prompts, llamadas a Gemini, RAG y generación de resúmenes.
Absorbe también la funcionalidad de nlp_engine.py.
"""
import os
import re
import time
from datetime import datetime

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

import nlp_engine
from database import MEMORIA_DIR, RESUMENES_DIR, meta_store, settings_store, stats_store


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

async def update_stats(model: str, tokens: int) -> dict:
    """Incrementa contadores de uso diario para el modelo dado."""
    today = datetime.now().strftime("%Y-%m-%d")
    default_stats = {
        "fecha": today,
        "gemini-3.5-flash": {"peticiones": 0, "tokens": 0},
        "gemini-3.1-flash-lite": {"peticiones": 0, "tokens": 0},
    }

    def _updater(stats: dict) -> dict:
        if stats.get("fecha") != today:
            stats = default_stats.copy()
        if model not in stats:
            stats[model] = {"peticiones": 0, "tokens": 0}
        stats[model]["peticiones"] += 1
        stats[model]["tokens"] += tokens
        return stats

    return await stats_store.update(_updater)


async def get_or_reset_stats() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    default_stats = {
        "fecha": today,
        "gemini-3.5-flash": {"peticiones": 0, "tokens": 0},
        "gemini-3.1-flash-lite": {"peticiones": 0, "tokens": 0},
    }
    stats = await stats_store.read()
    if stats.get("fecha") != today:
        return default_stats
    return stats


# ---------------------------------------------------------------------------
# Models list
# ---------------------------------------------------------------------------

async def list_available_models() -> list:
    try:
        client = genai.Client()
        modelos = client.models.list()
        valid_models = []
        for model in modelos:
            if (
                hasattr(model, "supported_actions")
                and model.supported_actions
                and "generateContent" in model.supported_actions
            ):
                model_name = (
                    model.name.replace("models/", "")
                    if model.name.startswith("models/")
                    else model.name
                )
                if "gemini" in model_name and "1.0" not in model_name:
                    valid_models.append(
                        {
                            "id": model_name,
                            "name": model_name,
                            "description": getattr(model, "description", "Modelo de IA"),
                        }
                    )
        valid_models.sort(key=lambda x: ("flash" not in x["id"].lower(), x["id"]))
        return valid_models
    except Exception as e:
        print("Error fetching models:", e)
        return [
            {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "description": "Modelo rápido."},
            {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash Lite", "description": "Modelo ligero."},
        ]


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

async def generate_prompt_for_materia(descripcion: str, modelo: str) -> tuple[str, dict]:
    """Genera un prompt estructurado a partir de una descripción natural."""
    client = genai.Client()
    sys_prompt = (
        "Eres un ingeniero de prompts experto. Tu objetivo es convertir la petición natural "
        "del usuario en un prompt de sistema robusto, estructurado con [Rol], [Contexto], "
        "[Tarea] y [Formato de Salida] listos para dárselo a otro LLM. Responde ÚNICAMENTE "
        "con el prompt generado final, sin introducciones, saludos ni comillas extra. Hazlo directo."
    )
    res = client.models.generate_content(
        model=modelo,
        contents=[f"Petición natural del usuario: {descripcion}", sys_prompt],
    )
    tokens = res.usage_metadata.total_token_count if res.usage_metadata else 0
    stats = await update_stats(modelo, tokens)
    return res.text.strip(), stats


# ---------------------------------------------------------------------------
# Summary generation (audio → Gemini → Markdown + JSON)
# ---------------------------------------------------------------------------

def _build_summary_prompt(prompt_usar: str, fecha_actual: str) -> str:
    """
    Prompt Maestro Multi-Modal (v2).
    El sistema SIEMPRE recibe audio + capturas de pantalla.
    La IA debe correlacionar ambas fuentes y usar la sintaxis Obsidian ![[imagen.jpg]].
    """
    return f"""Eres un experto en Personal Knowledge Management (PKM) y un ingeniero de software senior. La fecha de hoy es {fecha_actual}.
Contexto de la clase: {prompt_usar}

Se te han proporcionado el audio de la clase Y capturas de pantalla tomadas cronológicamente.
Tu tarea es crear una nota de estudio de calidad profesional que cumpla ESTRICTAMENTE las siguientes reglas:

1. CORRELACIÓN AUDIO-IMAGEN (OBLIGATORIO): Relaciona el contenido del audio con las imágenes.
   Cuando el profesor explique algo que coincida con lo que se ve en una captura (diagrama, fórmula, código, diapositiva), descríbelo en detalle en el apunte E INSERTA LA IMAGEN con la sintaxis Obsidian:
   ![[nombre_exacto_del_archivo.jpg]]
   El nombre del archivo es exactamente el que se te proporcionó (ej. captura_000_t0s.jpg).
   Solo inserta imágenes que sean realmente relevantes para el concepto que se está explicando.

2. ESTRUCTURA DE CARPETAS: Mi bóveda usa PARA (01 Proyectos, 02 Recursos, 03 Areas, 04 Archivo).

3. REGLA DE TITULACIÓN (Googleability): Una nota = Un solo concepto. Títulos directos.
   Ej Teórico: "Costo de Oportunidad". Ej Técnico: "Cómo configurar Git con SSH en Linux".

4. ARQUETIPOS DE NOTA:
   A. Técnico / Cheat Sheet: Inicia con > [!warning] o > [!info]. Pasos directos sin relleno. Bloques de código especificados.
   B. Teórico: Inicia con > [!summary] (Resumen Feynman). Desarrollo en viñetas. Ejemplos con > [!example].

5. CERO NOTAS HUÉRFANAS: Al final incluye un enlace de Obsidian a un concepto relacionado (ej. [[Índice - Semestre actual]]).

6. EXHAUSTIVIDAD OBLIGATORIA (PROPORCIONALIDAD): ¡NO COMPRIMAS EN EXCESO! El nivel de detalle debe ser directamente proporcional a la duración del audio y la cantidad de imágenes. Muestra todo el contenido relevante estructurado a profundidad.

Además, debes extraer reglas:
Si en el audio el profesor explica un método de resolución específico, una fórmula propia, o exige explícitamente que los problemas se resuelvan de una manera particular (diferente a los libros), extráelo detalladamente en el array 'nuevas_reglas_profesor' del bloque JSON. Si no hay reglas nuevas en esta clase, deja el array vacío [].

Finalmente, extrae cualquier anuncio importante, tarea, fecha de examen o cambio logístico en el array 'tarjetas_informativas'. Respeta cómo el profesor refirió el tiempo en 'referencia_temporal'.

Genera tu respuesta en el siguiente formato ESTRICTO:
---
tipo: [teoria o cheatsheet]
estado: borrador
tags: [tag1, tag2]
---

[Contenido de la nota usando Callouts de Obsidian, imágenes ![[...]] y estructura requerida]

[Enlace MOC o Concepto Relacionado]

$$AL FINAL DEL ARCHIVO, INCLUYE ESTRICTAMENTE ESTE BLOQUE JSON$$
```json
{{
  "filename": "Titulo Exacto Googleable.md",
  "folder": "02 Recursos/Tema",
  "tarjetas_informativas": [
    {{
      "tipo": "tarea|examen|aviso|otro",
      "contenido": "Información detallada...",
      "referencia_temporal": "ej. próxima clase, 15 de mayo, la otra semana"
    }}
  ],
  "nuevas_reglas_profesor": [
    {{
      "tema": "El tema del que habla",
      "metodo_paso_a_paso": "La explicación detallada o fórmula estricta que el profesor exige usar, extraída textualmente del audio."
    }}
  ]
}}
```"""


async def generate_summary_from_audio(
    upload_path: str,
    prompt_usar: str,
    modelo: str,
    image_paths: list = None,
) -> tuple[str, dict]:
    """
    Sube el audio + imágenes (SIEMPRE requeridas) a la Files API de Gemini,
    espera procesamiento y genera el resumen multi-modal.

    Returns:
        (texto_generado, stats_dict)
    """
    image_paths = image_paths or []
    client = genai.Client()
    fecha_actual = datetime.now().strftime("%Y-%m-%d")
    # Single unified prompt — always multimodal
    prompt_completo = _build_summary_prompt(prompt_usar, fecha_actual)


    # --- Upload audio ---
    print(f"Subiendo audio a Gemini Files API...", flush=True)
    audio_file = client.files.upload(file=upload_path, config={"mime_type": "audio/webm"})

    print("Esperando a que Gemini procese el audio...", flush=True)
    audio_info = client.files.get(name=audio_file.name)
    while audio_info.state.name == "PROCESSING":
        time.sleep(3)
        audio_info = client.files.get(name=audio_file.name)

    if audio_info.state.name == "FAILED":
        raise RuntimeError("Gemini falló al procesar el archivo de audio.")

    # --- Upload images (if any) ---
    uploaded_images = []
    for img_path in image_paths:
        if not os.path.exists(img_path):
            continue
        ext = os.path.splitext(img_path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/jpeg")
        try:
            print(f"Subiendo imagen {os.path.basename(img_path)}...", flush=True)
            img_file = client.files.upload(file=img_path, config={"mime_type": mime_type})
            # Images process fast — brief wait
            img_info = client.files.get(name=img_file.name)
            retries = 0
            while img_info.state.name == "PROCESSING" and retries < 10:
                time.sleep(2)
                img_info = client.files.get(name=img_file.name)
                retries += 1
            if img_info.state.name != "FAILED":
                uploaded_images.append(img_info)
        except Exception as e:
            print(f"[LLM] Error subiendo imagen {img_path}: {e}", flush=True)

    print(f"Archivos listos: 1 audio + {len(uploaded_images)} imágenes. Generando resumen...", flush=True)

    # Build contents: [audio_file, img1, img2, ..., prompt]
    contents = [audio_info] + uploaded_images + [prompt_completo]

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
    def _call_gemini():
        print("Solicitando generación a Gemini...", flush=True)
        res = client.models.generate_content(
            model=modelo,
            contents=contents,
            config={"max_output_tokens": 8192},
        )
        if not res.text or len(res.text.strip()) < 50:
            raise ValueError("Respuesta vacía o corta. Forzando reintento.")
        return res

    response = _call_gemini()
    tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0
    stats = await update_stats(modelo, tokens)
    return response.text, stats


# ---------------------------------------------------------------------------
# JSON extraction helper (compartido entre save y extract-task)
# ---------------------------------------------------------------------------

def extract_json_block(texto: str) -> tuple[dict, str]:
    """
    Extrae el bloque ```json ... ``` del texto de Gemini.

    Returns:
        (json_data dict, texto_limpio sin el bloque JSON)
    """
    json_data = {}
    texto_limpio = texto

    json_match = re.search(r"```json\s*(\{.*?\})\s*```", texto, re.DOTALL | re.IGNORECASE)
    if not json_match:
        json_match = re.search(r'(\{[\s\n]*"filename".*?\})', texto, re.DOTALL | re.IGNORECASE)

    if json_match:
        json_str = json_match.group(1)
        texto_limpio = texto.replace(json_match.group(0), "").strip()
        try:
            json_data = __import__("json").loads(json_str)
        except Exception as e:
            print("Error parseando JSON de Gemini:", e)

    return json_data, texto_limpio


# ---------------------------------------------------------------------------
# Chat / RAG
# ---------------------------------------------------------------------------

async def chat_with_rag(
    mensaje: str,
    materia_id: str,
    modelo: str,
) -> tuple[str, dict]:
    """
    Implementa el RAG ligero:
    1. Filtra resúmenes por materia.
    2. Aplica filtro temporal con nlp_engine.
    3. Scoring de relevancia con nlp_engine.
    4. Inyecta reglas del profesor si existen.
    5. Llama a Gemini con el contexto ensamblado.

    Returns:
        (respuesta_texto, stats_dict)
    """
    import os as _os

    meta_data = await meta_store.read()
    settings = await settings_store.read()
    rag_max_docs = settings.get("rag_max_docs", 8)

    # 1. Filtrado por materia
    all_files = list(meta_data.keys())
    if materia_id == "todas":
        valid_files = all_files
    elif materia_id == "default":
        valid_files = [f for f in all_files if "__default__" in f or "__" not in f]
    else:
        valid_files = [f for f in all_files if f"__{materia_id}__" in f]

    valid_files.sort()

    # 2. Filtro temporal
    date_range = nlp_engine.parse_temporal_filter(mensaje)

    filtered_summaries = []
    for f in valid_files:
        f_meta = meta_data.get(f)
        if not f_meta:
            continue
        f_fecha_str = f_meta.get("fecha", "")
        if date_range and f_fecha_str:
            try:
                f_fecha = datetime.strptime(f_fecha_str, "%Y-%m-%d")
                if not (date_range[0] <= f_fecha <= date_range[1]):
                    continue
            except Exception:
                pass
        filtered_summaries.append(f_meta)

    # 3. Índice condensado cronológico
    filtered_summaries.sort(key=lambda x: x.get("fecha", ""))
    indice_condensado = "\n".join(
        f"- {m.get('fecha', 'N/A')}: {m.get('condensado', '')}"
        for m in filtered_summaries
    )

    # 4. Scoring de relevancia
    relevant_summaries = nlp_engine.score_relevance(mensaje, filtered_summaries, max_results=rag_max_docs)
    relevant_filenames = {m.get("filename") for m in relevant_summaries if m.get("filename")}
    relevant_list = [m for m in filtered_summaries if m.get("filename") in relevant_filenames]
    resumenes_completos = "".join(
        f"\n\n--- Documento: {m.get('filename')} ---\n{m.get('resumen', '')}"
        for m in relevant_list
    )

    # 5. System instruction + reglas del profesor
    sys_instruction = (
        "Eres mi tutor universitario experto. Basa tus respuestas ESTRICTAMENTE en mis "
        "documentos de estudio proporcionados. Si la información no está explícitamente en "
        "los apuntes, indica claramente que 'no se menciona en los apuntes de clase'."
    )
    materia_name = materia_id if materia_id and materia_id != "default" else "general"
    reglas_filepath = _os.path.join(MEMORIA_DIR, f"reglas_{materia_name}.md")
    if _os.path.exists(reglas_filepath):
        with open(reglas_filepath, "r", encoding="utf-8") as rf:
            reglas_adicionales = rf.read()
            if reglas_adicionales.strip():
                sys_instruction += (
                    "\n\nERES UN ASISTENTE ESTUDIANTIL. DEBES OBEDECER ESTRICTAMENTE LAS "
                    "SIGUIENTES REGLAS Y MÉTODOS DEL PROFESOR AL RESOLVER PROBLEMAS:\n"
                    + reglas_adicionales
                )

    # 6. Llamada a Gemini
    client = genai.Client()
    res = client.models.generate_content(
        model=modelo,
        contents=[
            "ÍNDICE CONDENSADO (Filtrado temporalmente):\n" + indice_condensado,
            "APUNTES COMPLETOS (Más relevantes):\n" + resumenes_completos,
            mensaje,
        ],
        config=types.GenerateContentConfig(system_instruction=sys_instruction),
    )
    tokens = res.usage_metadata.total_token_count if res.usage_metadata else 0
    stats = await update_stats(modelo, tokens)
    return res.text.strip(), stats


# ---------------------------------------------------------------------------
# Tutor Socrático
# ---------------------------------------------------------------------------

async def tutor_chat_with_rag(
    historial_mensajes: list,
    pregunta_actual: str,
    materia_id: str,
    modelo: str,
) -> tuple[str, dict]:
    """
    Tutor Socrático: Reutiliza el motor RAG para recuperar contexto, pero 
    cambia el System Instruction para evaluar al alumno interactivamente.
    Mantiene el historial de la conversación.
    """
    import os as _os

    meta_data = await meta_store.read()
    settings = await settings_store.read()
    rag_max_docs = settings.get("rag_max_docs", 8)

    # 1. Filtrado por materia
    all_files = list(meta_data.keys())
    if materia_id == "todas":
        valid_files = all_files
    elif materia_id == "default":
        valid_files = [f for f in all_files if "__default__" in f or "__" not in f]
    else:
        valid_files = [f for f in all_files if f"__{materia_id}__" in f]

    valid_files.sort()

    # 2. Filtro temporal
    date_range = nlp_engine.parse_temporal_filter(pregunta_actual)

    filtered_summaries = []
    for f in valid_files:
        f_meta = meta_data.get(f)
        if not f_meta:
            continue
        f_fecha_str = f_meta.get("fecha", "")
        if date_range and f_fecha_str:
            try:
                f_fecha = datetime.strptime(f_fecha_str, "%Y-%m-%d")
                if not (date_range[0] <= f_fecha <= date_range[1]):
                    continue
            except Exception:
                pass
        filtered_summaries.append(f_meta)

    # 3. Índice condensado cronológico
    filtered_summaries.sort(key=lambda x: x.get("fecha", ""))
    indice_condensado = "\n".join(
        f"- {m.get('fecha', 'N/A')}: {m.get('condensado', '')}"
        for m in filtered_summaries
    )

    # 4. Scoring de relevancia
    relevant_summaries = nlp_engine.score_relevance(pregunta_actual, filtered_summaries, max_results=rag_max_docs)
    relevant_filenames = {m.get("filename") for m in relevant_summaries if m.get("filename")}
    relevant_list = [m for m in filtered_summaries if m.get("filename") in relevant_filenames]
    resumenes_completos = "".join(
        f"\n\n--- Documento: {m.get('filename')} ---\n{m.get('resumen', '')}"
        for m in relevant_list
    )

    # 5. System instruction para el Tutor Socrático
    sys_instruction = (
        "Eres un profesor universitario riguroso evaluando a tu alumno. "
        "Tienes acceso a sus apuntes, los cuales pueden contener referencias a imágenes. "
        "Tu objetivo NO es darle las respuestas directas, sino hacerle preguntas de "
        "razonamiento basadas en el material para comprobar que estudió.\n"
        "Reglas:\n"
        "Haz UNA pregunta a la vez.\n"
        "Evalúa la respuesta del alumno. Si acierta, felicítalo brevemente y sube la dificultad con otra pregunta del material.\n"
        "Si se equivoca, no le des la respuesta; guíalo socráticamente con pistas hasta que lo entienda."
    )

    materia_name = materia_id if materia_id and materia_id != "default" else "general"
    reglas_filepath = _os.path.join(MEMORIA_DIR, f"reglas_{materia_name}.md")
    if _os.path.exists(reglas_filepath):
        with open(reglas_filepath, "r", encoding="utf-8") as rf:
            reglas_adicionales = rf.read()
            if reglas_adicionales.strip():
                sys_instruction += (
                    "\n\nTEN EN CUENTA ESTAS REGLAS/MÉTODOS DEL PROFESOR AL EVALUAR:\n"
                    + reglas_adicionales
                )

    # 6. Llamada a Gemini con historial
    client = genai.Client()

    # Formatear el historial para pasarlo a contents
    contents = []
    
    # Añadimos el RAG como contexto en el primer mensaje de usuario o lo simulamos
    rag_context = f"ÍNDICE CONDENSADO:\n{indice_condensado}\nAPUNTES:\n{resumenes_completos}"
    historial_limpio = [
        msg for msg in historial_mensajes
        if msg.get("role") in {"user", "model"} and msg.get("text")
    ]

    # El mensaje inicial de la UI es solo decorativo; no debe contaminar el historial
    while historial_limpio and historial_limpio[0].get("role") != "user":
        historial_limpio.pop(0)

    contexto_inyectado = False

    if not historial_limpio:
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=f"Contexto: {rag_context}\n\nPregunta: {pregunta_actual}")],
            )
        )
    else:
        for msg in historial_limpio:
            role = msg.get("role", "user")
            text = msg.get("text", "")

            if not contexto_inyectado and role == "user":
                text = f"Contexto: {rag_context}\n\nPregunta: {text}"
                contexto_inyectado = True

            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))

        if not contexto_inyectado:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=f"Contexto: {rag_context}\n\nPregunta: {pregunta_actual}")],
                )
            )
        else:
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=pregunta_actual)],
                )
            )

    res = client.models.generate_content(
        model=modelo,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=sys_instruction),
    )
    tokens = res.usage_metadata.total_token_count if res.usage_metadata else 0
    stats = await update_stats(modelo, tokens)
    return res.text.strip(), stats


# ---------------------------------------------------------------------------
# Task extraction from image
# ---------------------------------------------------------------------------

async def extract_task_from_image(img_bytes: bytes, modelo: str) -> tuple[str, dict]:
    """Envía una imagen a Gemini y extrae la información de la tarea académica."""
    prompt = """Eres un experto en Personal Knowledge Management (PKM). A partir de esta captura de pantalla de una plataforma educativa, extrae la información de la tarea.
Debes crear una nota que cumpla ESTRICTAMENTE las siguientes reglas de mi bóveda de Obsidian:
1. ESTRUCTURA DE CARPETAS: Usa PARA (01 Proyectos/Tareas).
2. Títulos directos. Ej: "Tarea de Cálculo II - Integrales".

Genera tu respuesta en el siguiente formato ESTRICTO:
---
tipo: tarea
estado: pendiente
tags: [tarea]
---

[Contenido de la nota con detalles de la tarea o aviso]

$$AL FINAL DEL ARCHIVO, INCLUYE ESTRICTAMENTE ESTE BLOQUE JSON$$
```json
{
  "filename": "Titulo Tarea o Aviso.md",
  "folder": "01 Proyectos/Tareas",
  "tarjetas_informativas": [
    {
      "tipo": "tarea|examen|aviso|otro",
      "contenido": "...",
      "referencia_temporal": "..."
    }
  ]
}
```"""
    client = genai.Client()
    response = client.models.generate_content(
        model=modelo,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            prompt,
        ],
    )
    tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0
    stats = await update_stats(modelo, tokens)
    return response.text, stats
