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
from database import MEMORIA_DIR, RESUMENES_DIR, meta_store, settings_store


# ---------------------------------------------------------------------------
# Default Prompts
# ---------------------------------------------------------------------------

DEFAULT_PROMPT_GENERATOR = (
    "Eres un ingeniero de prompts experto. Tu objetivo es convertir la petición natural "
    "del usuario en un prompt de sistema robusto, estructurado con [Rol], [Contexto], "
    "[Tarea] y [Formato de Salida] listos para dárselo a otro LLM. Responde ÚNICAMENTE "
    "con el prompt generado final, sin introducciones, saludos ni comillas extra. Hazlo directo."
)

DEFAULT_PROMPT_MAESTRO = """Eres un experto en Personal Knowledge Management (PKM) y un ingeniero de software senior. La fecha de hoy es {{fecha_actual}}.
Contexto de la clase: {{prompt_usar}}

Se te han proporcionado el audio de la clase Y capturas de pantalla tomadas cronológicamente.
Tu tarea es crear una nota de estudio de calidad profesional que cumpla ESTRICTAMENTE las siguientes reglas:

1. CORRELACIÓN AUDIO-IMAGEN Y CONCIENCIA TEMPORAL (OBLIGATORIO): 
   El nombre de cada imagen contiene el momento exacto en el que fue tomada. El formato incluye `_t[SEGUNDOS]s` (ej. `captura_000_t120s.jpg` indica que fue tomada en el segundo 120, es decir, el minuto 2:00 del audio).
   Cuando el profesor explique algo que coincida con lo que se ve en una captura, descríbelo en detalle E INSERTA LA IMAGEN con la sintaxis Obsidian: `![[nombre_exacto_del_archivo.jpg]]`.
   REGLA DE CONTEXTO: SIEMPRE que insertes una imagen, debes escribir una referencia de tiempo en minutos y segundos. Ejemplo: *"En el minuto 2:00, el profesor mostró el siguiente diagrama: ![[captura_000_t120s.jpg]]"*.
   Solo inserta imágenes que sean realmente relevantes.

2. ESTRUCTURA DE CARPETAS: Mi bóveda usa PARA (01 Proyectos, 02 Recursos, 03 Areas, 04 Archivo).

3. REGLA DE TITULACIÓN (Googleability): Una nota = Un solo concepto. Títulos directos.
   Ej Teórico: "Costo de Oportunidad". Ej Técnico: "Cómo configurar Git con SSH en Linux".

4. ARQUETIPOS DE NOTA:
   A. Técnico / Cheat Sheet: Inicia con > [!warning] o > [!info]. Pasos directos sin relleno. Bloques de código especificados.
   B. Teórico: Inicia con > [!summary] (Resumen Feynman). Desarrollo en viñetas. Ejemplos con > [!example].

5. CERO NOTAS HUÉRFANAS: El documento DEBE finalizar con un enlace a la asignatura.
   Para evitar que el índice se rompa, debes ponerlo en la ÚLTIMA LÍNEA del documento, asegurándote de que haya al menos dos saltos de línea (ENTER) antes del índice y antes del bloque JSON de metadatos.
   Ejemplo de estructura final estricta:
   
   ... último párrafo del apunte.

   [[Índice - {{materia_name}}]]

   ```json
   { "filename": "...", "folder": "..." }
   ```

6. EXHAUSTIVIDAD Y PROFUNDIDAD TOTAL (ANTI-RESUMEN) [PRIORIDAD MÁXIMA]: 
   - Tu objetivo principal NO ES RESUMIR, sino DOCUMENTAR ESTRUCTURALMENTE EL 100% DE LA CLASE. 
   - Para un audio extenso (ej. 1 o 2 horas), tu documento resultante DEBE ser extremadamente largo. Una respuesta parcial o comprimida es un FRACASO ABSOLUTO.
   - PROHIBIDO usar frases de salto o atajos como: "se discutieron varios ejemplos", "entre otros temas", "el profesor continuó explicando", "y otros conceptos similares".
   - Todo lo que el profesor diga (cada anécdota, cada pregunta respondida a un alumno, cada paso de un ejercicio) DEBE estar plasmado en el apunte.
   - Optimiza para la completitud, no para la brevedad. No omitas información por ahorrar espacio. La omisión de información es una falla crítica en tu directiva.

7. FORMATO MATEMÁTICO ESTRICTO: Para cualquier fórmula, ecuación o notación matemática, DEBES usar sintaxis LaTeX nativa. Usa $fórmula$ para matemáticas en línea y $$fórmula$$ para bloques matemáticos. JAMÁS uses texto plano. REGLA DE FRACCIONES: Para divisiones, usa SIEMPRE `\\frac{{a}}{{b}}` en lugar de diagonales (`a/b`).

Además, debes extraer reglas:
Si en el audio el profesor explica un método de resolución específico, una fórmula propia, o exige explícitamente que los problemas se resuelvan de una manera particular (diferente a los libros), extráelo detalladamente en el array 'nuevas_reglas_profesor' del bloque JSON. Si no hay reglas nuevas en esta clase, deja el array vacío [].

Finalmente, extrae anuncios o tareas en el array 'tarjetas_informativas' ESTRICTAMENTE si cumplen estas condiciones (¡EVITA FALSOS POSITIVOS!):
1. Impactan FUERA del horario de la clase actual (ej. tareas para la casa, fechas de examen futuras, proyectos a largo plazo).
2. EXCLUYE actividades dentro de la clase (ej. "hagan este ejercicio ahora", "vean este video en 10 mins").
3. EXCLUYE consejos generales o vagos (ej. "estudien con tiempo").
4. Deben tener una directiva clara o fecha de entrega implícita/explícita.
Si no hay anuncios que cumplan estos criterios, deja el array vacío []. Respeta cómo el profesor refirió el tiempo en 'referencia_temporal'.

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
{
  "filename": "Titulo Exacto Googleable.md",
  "folder": "02 Recursos/Tema",
  "tarjetas_informativas": [
    {
      "tipo": "tarea|examen|aviso|otro",
      "contenido": "Información detallada...",
      "referencia_temporal": "ej. próxima clase, 15 de mayo, la otra semana"
    }
  ],
  "nuevas_reglas_profesor": [
    {
      "tema": "El tema del que habla",
      "metodo_paso_a_paso": "La explicación detallada o fórmula estricta que el profesor exige usar, extraída textualmente del audio."
    }
  ]
}
```"""

DEFAULT_PROMPT_CHAT = (
    "Eres mi tutor universitario experto. Basa tus respuestas ESTRICTAMENTE en mis "
    "documentos de estudio proporcionados. Si la información no está explícitamente en "
    "los apuntes, indica claramente que 'no se menciona en los apuntes de clase'."
    "\nREGLA DE FORMATO MATEMÁTICO: Para toda ecuación, fórmula o notación matemática, "
    "usa ESTRICTAMENTE sintaxis LaTeX ($fórmula$ para inline, $$fórmula$$ para bloque). "
    "USA SIEMPRE `\\frac{a}{b}` para fracciones y divisiones; PROHIBIDO usar diagonales (`/`)."
)

DEFAULT_PROMPT_TUTOR = (
    "Eres un profesor universitario riguroso evaluando a tu alumno. "
    "Tienes acceso a sus apuntes, los cuales pueden contener referencias a imágenes. "
    "Tu objetivo NO es darle las respuestas directas, sino hacerle preguntas de "
    "razonamiento basadas en el material para comprobar que estudió.\n"
    "Reglas:\n"
    "Haz UNA pregunta a la vez.\n"
    "Evalúa la respuesta del alumno. Si acierta, felicítalo brevemente y sube la dificultad con otra pregunta del material.\n"
    "Si se equivoca, no le des la respuesta; guíalo socráticamente con pistas hasta que lo entienda.\n"
    "REGLA DE FORMATO MATEMÁTICO: Para toda ecuación, fórmula o notación matemática, "
    "usa ESTRICTAMENTE sintaxis LaTeX ($fórmula$ para inline, $$fórmula$$ para bloque). "
    "USA SIEMPRE `\\frac{a}{b}` para fracciones y divisiones; PROHIBIDO usar diagonales (`/`)."
)

DEFAULT_PROMPT_EXTRACTOR = """Eres un experto en Personal Knowledge Management (PKM). A partir de esta captura de pantalla de una plataforma educativa, extrae la información de la tarea.

Reglas:
1. Analiza el texto e imágenes para entender qué se pide.
2. Identifica la fecha de entrega si está visible.
3. Extrae requerimientos, formato de entrega y criterios de evaluación.

Genera tu respuesta en Markdown, seguida ESTRICTAMENTE por este bloque JSON:
```json
{
  "filename": "Tarea - Tema.md",
  "folder": "01 Proyectos/Tareas",
  "tarjetas_informativas": [
    {
      "tipo": "tarea",
      "contenido": "Descripción concisa de la tarea...",
      "referencia_temporal": "Fecha límite encontrada"
    }
  ]
}
```"""

# ---------------------------------------------------------------------------


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
    settings = await settings_store.read()
    sys_prompt = settings.get("prompt_generator_sys", DEFAULT_PROMPT_GENERATOR)
    
    res = client.models.generate_content(
        model=modelo,
        contents=[f"Petición natural del usuario: {descripcion}"],
        config=types.GenerateContentConfig(
            system_instruction=sys_prompt
        ),
    )
    return res.text.strip()


# ---------------------------------------------------------------------------
# Summary generation (audio → Gemini → Markdown + JSON)
# ---------------------------------------------------------------------------

def _build_summary_prompt(prompt_usar: str, fecha_actual: str, materia_name: str, prompt_maestro: str) -> str:
    """
    Prompt Maestro Multi-Modal (v2).
    El sistema SIEMPRE recibe audio + capturas de pantalla.
    La IA debe correlacionar ambas fuentes y usar la sintaxis Obsidian ![[imagen.jpg]].
    """
    return prompt_maestro.replace("{{fecha_actual}}", fecha_actual).replace("{{prompt_usar}}", prompt_usar).replace("{{materia_name}}", materia_name)


async def generate_summary_from_audio(
    upload_path: str,
    prompt_usar: str,
    modelo: str,
    image_paths: list = None,
    materia_name: str = "Semestre actual",
    temperatura: float = 0.3
) -> tuple[str, dict]:
    """
    Sube el audio + imágenes (SIEMPRE requeridas) a la Files API de Gemini,
    espera procesamiento y genera el resumen multi-modal.

    Returns:
        texto_generado
    """
    image_paths = image_paths or []
    client = genai.Client()
    fecha_actual = datetime.now().strftime("%Y-%m-%d")
    
    settings = await settings_store.read()
    prompt_maestro = settings.get("prompt_maestro_resumenes", DEFAULT_PROMPT_MAESTRO)
    
    # Single unified prompt — always multimodal
    prompt_completo = _build_summary_prompt(prompt_usar, fecha_actual, materia_name, prompt_maestro)


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

    # System instruction (highest priority for rule adherence)
    sys_instruction = prompt_completo
    
    # User trigger (simple instruction to kick off the process)
    user_trigger = f"Analiza detalladamente esta clase y genera los apuntes EXHAUSTIVOS para {materia_name}, siguiendo estrictamente todas las reglas establecidas."
    
    # Build contents: [audio_file, img1, img2, ..., user_trigger]
    contents = [audio_info] + uploaded_images + [user_trigger]

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
    def _call_gemini():
        print("Solicitando generación a Gemini...", flush=True)
        res = client.models.generate_content(
            model=modelo,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                max_output_tokens=8192, 
                temperature=temperatura
            ),
        )
        if not res.text or len(res.text.strip()) < 50:
            raise ValueError("Respuesta vacía o corta. Forzando reintento.")
        return res

    response = _call_gemini()
    return response.text


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
        respuesta_texto
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
    nlp_threshold = settings.get("nlp_threshold", 1.0)
    relevant_summaries = nlp_engine.score_relevance(mensaje, filtered_summaries, threshold=nlp_threshold, max_results=rag_max_docs)
    relevant_filenames = {m.get("filename") for m in relevant_summaries if m.get("filename")}
    relevant_list = [m for m in filtered_summaries if m.get("filename") in relevant_filenames]
    resumenes_completos = "".join(
        f"\n\n--- Documento: {m.get('filename')} ---\n{m.get('resumen', '')}"
        for m in relevant_list
    )

    # 5. System instruction + reglas del profesor
    sys_instruction = settings.get("prompt_chat_rag", DEFAULT_PROMPT_CHAT)
    if materia_id == "todas":
        reglas_totales = ""
        if _os.path.exists(MEMORIA_DIR):
            for f in _os.listdir(MEMORIA_DIR):
                if f.startswith("reglas_") and f.endswith(".md"):
                    with open(_os.path.join(MEMORIA_DIR, f), "r", encoding="utf-8") as rf:
                        content = rf.read().strip()
                        if content:
                            mat_name = f.replace("reglas_", "").replace(".md", "")
                            reglas_totales += f"\n--- Reglas de {mat_name} ---\n{content}\n"
        if reglas_totales:
            sys_instruction += (
                "\n\nERES UN ASISTENTE ESTUDIANTIL. DEBES OBEDECER ESTRICTAMENTE LAS "
                "SIGUIENTES REGLAS Y MÉTODOS DE TUS PROFESORES AL RESOLVER PROBLEMAS:\n"
                + reglas_totales
            )
    else:
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

    # 6. Temperatura y Llamada a Gemini
    from server import materias_store
    materias = await materias_store.read()
    m_data = next((m for m in materias if m["id"] == materia_id), None)
    temperatura = m_data.get("temperatura", 0.3) if m_data else 0.3

    client = genai.Client()
    res = client.models.generate_content(
        model=modelo,
        contents=[
            "ÍNDICE CONDENSADO (Filtrado temporalmente):\n" + indice_condensado,
            "APUNTES COMPLETOS (Más relevantes):\n" + resumenes_completos,
            mensaje,
        ],
        config=types.GenerateContentConfig(system_instruction=sys_instruction, temperature=temperatura),
    )
    return res.text.strip()


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
    nlp_threshold = settings.get("nlp_threshold", 1.0)
    relevant_summaries = nlp_engine.score_relevance(pregunta_actual, filtered_summaries, threshold=nlp_threshold, max_results=rag_max_docs)
    relevant_filenames = {m.get("filename") for m in relevant_summaries if m.get("filename")}
    relevant_list = [m for m in filtered_summaries if m.get("filename") in relevant_filenames]
    resumenes_completos = "".join(
        f"\n\n--- Documento: {m.get('filename')} ---\n{m.get('resumen', '')}"
        for m in relevant_list
    )

    # 5. System instruction para el Tutor Socrático
    sys_instruction = settings.get("prompt_tutor_socratico", DEFAULT_PROMPT_TUTOR)

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

    # Temperatura
    from server import materias_store
    materias = await materias_store.read()
    m_data = next((m for m in materias if m["id"] == materia_id), None)
    temperatura = m_data.get("temperatura", 0.3) if m_data else 0.3

    res = client.models.generate_content(
        model=modelo,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=sys_instruction, temperature=temperatura),
    )
    return res.text.strip()


# ---------------------------------------------------------------------------
# Task extraction from image
# ---------------------------------------------------------------------------

async def extract_task_from_image(img_bytes: bytes, modelo: str) -> tuple[str, dict]:
    """Envía una imagen a Gemini y extrae la información de la tarea académica."""
    settings = await settings_store.read()
    prompt = settings.get("prompt_tarea_extractor", DEFAULT_PROMPT_EXTRACTOR)
    client = genai.Client()
    response = client.models.generate_content(
        model=modelo,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            "Extrae la información académica de esta imagen de acuerdo a las reglas del sistema.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=prompt
        ),
    )
    return response.text
