"""
services/llm_service.py — Toda la lógica del SDK de Google GenAI:
prompts, llamadas a Gemini, RAG y generación de resúmenes.
Absorbe también la funcionalidad de nlp_engine.py.
"""
import os
import re
import time
import asyncio
from datetime import datetime

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from services import vector_store
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
Tu tarea es crear un archivo Markdown de estudio extremadamente detallado que cumpla ESTRICTAMENTE las siguientes reglas:

1. CORRELACIÓN AUDIO-IMAGEN Y CONCIENCIA TEMPORAL (OBLIGATORIO): 
   El nombre de cada imagen contiene el momento exacto en el que fue tomada. El formato incluye `_t[SEGUNDOS]s`.
   IMPORTANTE: NO INVENTES NOMBRES DE IMÁGENES. Solo usa los nombres EXACTOS de las capturas adjuntas.
   Cuando el profesor explique algo que coincida con una captura, descríbelo en extremo detalle e INSERTA LA IMAGEN con sintaxis Obsidian: `![[nombre_exacto_del_archivo.jpg]]`.
   REGLA DE CONTEXTO: SIEMPRE que insertes una imagen, escribe una referencia de tiempo en minutos y segundos. Ejemplo: *"En el minuto 2:00, se mostró el siguiente diagrama: ![[captura_000_t120s.jpg]]"*.

2. ESTRUCTURA DE CARPETAS: Usa PARA (01 Proyectos, 02 Recursos, 03 Areas, 04 Archivo).

3. REGLA DE TITULACIÓN (Googleability): Títulos directos. (Ej: "Costo de Oportunidad").

4. ARQUETIPOS DE NOTA:
   A. Técnico / Cheat Sheet: Inicia con > [!warning] o > [!info].
   B. Teórico: Inicia con > [!summary] (Resumen Feynman). Desarrollo en múltiples sub-secciones y viñetas exhaustivas. Ejemplos con > [!example].

5. EXHAUSTIVIDAD Y LONGITUD (ANTI-COMPRESIÓN) [PRIORIDAD MÁXIMA]:
   - ACTÚAS COMO UN TRANSCRIPTOR ANALÍTICO, NO COMO UN RESUMIDOR. Tu objetivo es DOCUMENTAR ESTRUCTURALMENTE EL 100% DE LA CLASE.
   - Prohibido resumir. Prohibido saltar temas. Prohibido omitir ejemplos o anécdotas del profesor.
   - Por cada tema abordado en el audio, DEBES generar un análisis profundo, sin importar la redundancia.
   - Si la clase dura 2 horas, tu documento final debe ser masivo. Una salida corta (menor a 1500 palabras) es un fracaso absoluto.
   - NO uses atajos como "se explicó brevemente", "entre otros temas", "en conclusión". Desarrolla el tema de principio a fin.

6. CERO NOTAS HUÉRFANAS: El documento DEBE finalizar con un enlace a la asignatura en la ÚLTIMA LÍNEA (con dos saltos de línea previos).
   Ejemplo:
   
   ... último párrafo exhaustivo del apunte.

   [[Índice - {{materia_name}}]]

   ```json
   { "filename": "...", "folder": "..." }
   ```

7. FORMATO MATEMÁTICO ESTRICTO: Usa sintaxis LaTeX nativa ($fórmula$ o $$fórmula$$). Fracciones SIEMPRE como `\\frac{{a}}{{b}}`.

METADATOS JSON AL FINAL:
Extrae nuevas reglas (fórmulas propias o métodos del profesor) en `nuevas_reglas_profesor`.
Extrae anuncios/tareas (fuera del horario normal) en `tarjetas_informativas` (¡sin falsos positivos!).
Extrae temas atómicos tratados en `temario_atomico` (superficial, intermedio, profundo) con id único "tema_X" y dominio 0.

Genera tu respuesta en este formato ESTRICTO:
---
tipo: [teoria o cheatsheet]
estado: borrador
tags: [tag1, tag2]
---

[Contenido masivo y ultra-detallado de la nota usando Callouts, imágenes ![[...]] y la estructura requerida]

[[Índice - {{materia_name}}]]

$$AL FINAL DEL ARCHIVO, INCLUYE ESTRICTAMENTE ESTE BLOQUE JSON$$
```json
{
  "filename": "Titulo Exacto Googleable.md",
  "folder": "02 Recursos/Tema",
  "tarjetas_informativas": [ ... ],
  "nuevas_reglas_profesor": [ ... ],
  "temario_atomico": [ ... ]
}
```
"""

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
        def _sync_list():
            return list(client.models.list())
            
        modelos = await asyncio.to_thread(_sync_list)
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
        return []


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
    audio_file = await asyncio.to_thread(client.files.upload, file=upload_path, config={"mime_type": "audio/webm"})

    print("Esperando a que Gemini procese el audio...", flush=True)
    audio_info = await asyncio.to_thread(client.files.get, name=audio_file.name)
    while audio_info.state.name == "PROCESSING":
        await asyncio.sleep(3)
        audio_info = await asyncio.to_thread(client.files.get, name=audio_file.name)

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
            img_file = await asyncio.to_thread(client.files.upload, file=img_path, config={"mime_type": mime_type})
            # Images process fast — brief wait
            img_info = await asyncio.to_thread(client.files.get, name=img_file.name)
            retries = 0
            while img_info.state.name == "PROCESSING" and retries < 10:
                await asyncio.sleep(2)
                img_info = await asyncio.to_thread(client.files.get, name=img_file.name)
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
    
    if image_paths:
        user_trigger += "\\n\\nSe han adjuntado las siguientes imágenes (capturas de pantalla) en el mismo orden en que las recibiste:\\n"
        for idx, img_path in enumerate(image_paths):
            user_trigger += f"- {os.path.basename(img_path)}\n"
        user_trigger += "\\nREGLA CRÍTICA: Cuando debas insertar una de estas imágenes en tus apuntes, DEBES usar EXACTAMENTE el nombre de archivo listado arriba. Ejemplo: ![[{os.path.basename(image_paths[0])}]]"
    
    # Build contents: [audio_file, img1, img2, ..., user_trigger]
    contents = [audio_info] + uploaded_images + [user_trigger]

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
    async def _call_gemini():
        print(f"Solicitando generación a Gemini (Modelo: {modelo})...", flush=True)
        def _sync_generate():
            return client.models.generate_content(
                model=modelo,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=sys_instruction,
                    max_output_tokens=8192, 
                    temperature=temperatura
                ),
            )
        res = await asyncio.to_thread(_sync_generate)
        if not res.text or len(res.text.strip()) < 50:
            raise ValueError("Respuesta vacía o corta. Forzando reintento.")
        return res

    response = await _call_gemini()
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
    import json
    json_data = {}
    texto_limpio = texto

    # Attempt 1: Standard markdown json block
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", texto, re.DOTALL | re.IGNORECASE)
    
    # Attempt 2: Any markdown block with braces
    if not json_match:
        json_match = re.search(r"```\s*(\{.*?\})\s*```", texto, re.DOTALL)
        
    # Attempt 3: Just the braces (heuristics for raw JSON output)
    if not json_match:
        # Search for first { and last }
        start = texto.find("{")
        end = texto.rfind("}")
        if start != -1 and end != -1 and end > start:
            json_str = texto[start:end+1]
            try:
                # Validate it's parseable before accepting it as the match
                json.loads(json_str)
                # Create a mock match object-like structure for the replacement logic
                class MockMatch:
                    def group(self, i): return json_str if i == 1 else json_str
                json_match = MockMatch()
            except Exception:
                pass

    if json_match:
        json_str = json_match.group(1)
        texto_limpio = texto.replace(json_match.group(0), "").strip()
        try:
            json_data = json.loads(json_str)
        except Exception as e:
            print("Error parseando JSON de Gemini:", e)

    return json_data, texto_limpio


async def force_extract_metadata_from_markdown(markdown_text: str, modelo: str = "gemini-1.5-flash") -> dict:
    """
    Segunda pasada de seguridad (Two-Pass Fallback).
    Toma un Markdown puro y le exige a Gemini que devuelva EXCLUSIVAMENTE el JSON
    de los metadatos de las tarjetas informativas, reglas del profesor y temario.
    """
    client = genai.Client()
    
    sys_prompt = (
        "Eres un analizador de metadatos estricto. "
        "Tu única tarea es leer el documento Markdown provisto y extraer "
        "los avisos/tareas, reglas metodológicas y el temario atómico en formato JSON. "
        "NO devuelvas texto, saludos ni explicaciones. Responde ÚNICAMENTE con este JSON:\n"
        "```json\n"
        "{\n"
        "  \"tarjetas_informativas\": [\n"
        "    {\"tipo\": \"examen|aviso|tarea\", \"contenido\": \"...\", \"referencia_temporal\": \"...\"}\n"
        "  ],\n"
        "  \"nuevas_reglas_profesor\": [\n"
        "    {\"tema\": \"...\", \"metodo_paso_a_paso\": \"...\"}\n"
        "  ],\n"
        "  \"temario_atomico\": [\n"
        "    {\"id\": \"tema_X\", \"nombre\": \"...\", \"profundidad_sesion\": \"superficial|intermedio|profundo\"}\n"
        "  ]\n"
        "}\n"
        "```\n"
        "Si no encuentras algo, deja la lista correspondiente vacía `[]`."
    )
    
    try:
        def _sync_extract():
            return client.models.generate_content(
                model=modelo,
                contents=[f"Extrae el JSON de este documento:\n\n{markdown_text[:15000]}"], # Cap a 15k chars por si es gigante
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt,
                    temperature=0.1 # Muy bajo para asegurar formato
                ),
            )
        res = await asyncio.to_thread(_sync_extract)
        
        fallback_data, _ = extract_json_block(res.text)
        return fallback_data
    except Exception as e:
        print(f"Error en fallback de extracción JSON: {e}")
        return {}


# ---------------------------------------------------------------------------
# Chat / RAG
# ---------------------------------------------------------------------------

async def chat_with_rag(
    mensaje: str,
    materia_id: str,
    modelo: str,
    image_data: str = None
) -> tuple[str, dict]:
    """
    Implementa el RAG con Base de Datos Vectorial (ChromaDB):
    1. Busca los chunks más relevantes usando embeddings semánticos.
    2. Inyecta reglas del profesor si existen.
    3. Llama a Gemini con el contexto ensamblado.

    Returns:
        respuesta_texto
    """
    import os as _os
    import base64

    settings = await settings_store.read()
    rag_max_docs = settings.get("rag_max_docs", 8)

    # 1. Búsqueda Vectorial
    relevant_chunks = vector_store.semantic_search(
        query=mensaje, 
        materia_id=materia_id, 
        max_results=rag_max_docs
    )

    if not relevant_chunks:
        contexto_recuperado = "No se encontraron apuntes relevantes en la base de datos."
    else:
        contexto_recuperado = "FRAGMENTOS DE CLASES RECUPERADOS (RAG SEMÁNTICO):\n\n"
        for idx, chunk in enumerate(relevant_chunks):
            meta = chunk['metadata']
            contexto_recuperado += f"--- Fragmento {idx+1} | Archivo: {meta.get('filename')} | Fecha: {meta.get('fecha')} ---\n"
            contexto_recuperado += f"{chunk['chunk_text']}\n\n"

    # 2. System instruction + reglas del profesor
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

    # 3. Temperatura y Llamada a Gemini
    from server import materias_store
    materias = await materias_store.read()
    m_data = next((m for m in materias if m["id"] == materia_id), None)
    temperatura = m_data.get("temperatura", 0.3) if m_data else 0.3

    client = genai.Client()

    user_parts = []
    if image_data:
        try:
            mime = "image/jpeg"
            b64_str = image_data
            if "," in image_data:
                mime = image_data.split(";")[0].split(":")[1]
                b64_str = image_data.split(",")[1]
            img_bytes = base64.b64decode(b64_str)
            user_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
        except Exception as e:
            print(f"Error decodificando imagen Chat General: {e}")
            
    user_parts.append(types.Part.from_text(text=f"CONTEXTO INTERNO DE BÚSQUEDA VECTORIAL:\n{contexto_recuperado}\n\nPREGUNTA DEL ESTUDIANTE:\n{mensaje}"))

    res = client.models.generate_content(
        model=modelo,
        contents=[types.Content(role="user", parts=user_parts)],
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
    image_data: str = None
) -> tuple[str, dict]:
    """
    Tutor Socrático: Usa Vector RAG para recuperar contexto de ChromaDB.
    """
    import os as _os
    import base64

    settings = await settings_store.read()
    rag_max_docs = settings.get("rag_max_docs", 8)

    # 1. Búsqueda Vectorial
    relevant_chunks = vector_store.semantic_search(
        query=pregunta_actual, 
        materia_id=materia_id, 
        max_results=rag_max_docs
    )

    if not relevant_chunks:
        contexto_recuperado = "No se encontraron apuntes relevantes en la base de datos."
    else:
        contexto_recuperado = "FRAGMENTOS DE CLASES RECUPERADOS (RAG SEMÁNTICO):\n\n"
        for idx, chunk in enumerate(relevant_chunks):
            meta = chunk['metadata']
            contexto_recuperado += f"--- Fragmento {idx+1} | Archivo: {meta.get('filename')} | Fecha: {meta.get('fecha')} ---\n"
            contexto_recuperado += f"{chunk['chunk_text']}\n\n"

    # 2. System instruction para el Tutor Socrático
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

    # 3. Llamada a Gemini con historial
    client = genai.Client()

    # Formatear el historial para pasarlo a contents
    contents = []
    
    # Inyectar el contexto recuperado en el primer mensaje de sistema o de usuario
    contents.append(
        types.Content(
            role="user", 
            parts=[types.Part.from_text(f"CONTEXTO INTERNO DE BÚSQUEDA VECTORIAL (No lo menciones a menos que sea relevante):\n{contexto_recuperado}")]
        )
    )
    contents.append(
        types.Content(
            role="model", 
            parts=[types.Part.from_text("Entendido. Usaré este contexto para guiar mi tutoría socrática.")]
        )
    )

    for msg in historial_mensajes:
        role = "user" if msg.get("role") == "user" else "model"
        text = msg.get("text", "")
        if text:
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text)]))

    user_parts = []
    if image_data:
        try:
            mime = "image/jpeg"
            b64_str = image_data
            if "," in image_data:
                mime = image_data.split(";")[0].split(":")[1]
                b64_str = image_data.split(",")[1]
            img_bytes = base64.b64decode(b64_str)
            user_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
        except Exception as e:
            print(f"Error decodificando imagen Sala General: {e}")
            
    user_parts.append(types.Part.from_text(text=pregunta_actual))
    contents.append(types.Content(role="user", parts=user_parts))

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

# ---------------------------------------------------------------------------
# Tutor V2 Agentic Chat
# ---------------------------------------------------------------------------

async def tutor_v2_agentic_chat(
    slot_id: str,
    historial_mensajes: list,
    pregunta_actual: str,
    modelo: str,
    image_data: str = None
) -> dict:
    from database import progreso_store, meta_store
    import base64
    client = genai.Client()
    
    slots = await progreso_store.read()
    slot = next((s for s in slots if s["id"] == slot_id), None)
    if not slot or "temas" not in slot:
        return {"respuesta": "Este slot no soporta el Tutor V2 (no tiene temario atómico).", "updated": False}
        
    md_vinculado = slot.get("md_vinculado")
    apunte_completo = "No hay apunte vinculado."
    if md_vinculado:
        meta_data = await meta_store.read()
        f_meta = meta_data.get(md_vinculado)
        if f_meta:
            apunte_completo = f_meta.get("resumen", "")
            
    temario_str = "TEMARIO A EVALUAR:\\n"
    for t in slot["temas"]:
        temario_str += f"- ID: {t['id']} | Nombre: {t['nombre']} | Profundidad: {t['profundidad_sesion']} | Dominio actual: {t['dominio']}%\\n"
        
    sys_instruction = f"""Eres el Tutor Cognitivo V2. Tu misión es evaluar al alumno de forma proactiva para llevar el 'Dominio actual' de TODOS los temas al 100%.
    
    Tienes acceso a los apuntes de la clase:
    --- APUNTES DE CLASE ---
    {apunte_completo}
    ------------------------
    
    {temario_str}
    
    REGLAS ESTRICTAS:
    1. Eres proactivo. Tu objetivo es certificar que el alumno entendió la clase.
    2. Haz una sola pregunta o reto a la vez. No abrumees.
    3. Si el alumno demuestra entendimiento claro del concepto, ESTÁS OBLIGADO A USAR LA HERRAMIENTA `actualizar_dominio_tema` para incrementar su progreso.
       - Tema superficial: +100% si responde bien la pregunta conceptual.
       - Tema intermedio: +50% por buena respuesta.
       - Tema profundo: +25% por buena respuesta o +50% por resolución completa de un problema.
    4. NUNCA des la respuesta directamente si se equivoca, guíalo socráticamente.
    7. Para preguntas de opción múltiple, usa SIEMPRE formato de texto plano tradicional. Cada opción debe estar en su propia línea empezando por A), B), C) o D). NO uses XML ni etiquetas HTML.
       Ejemplo:
       A) Primera opción
       B) Segunda opción
       C) Tercera opción
    8. Usa KaTeX estricto para las matemáticas (`$$formula$$`).
    """
    
    actualizar_dominio_func = types.FunctionDeclaration(
        name="actualizar_dominio_tema",
        description="Aumenta el dominio de un tema de la clase tras evaluar que el alumno lo entendió correctamente.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "tema_id": types.Schema(type=types.Type.STRING, description="El ID del tema (ej. tema_1)"),
                "incremento": types.Schema(type=types.Type.INTEGER, description="Porcentaje a sumar (ej. 25, 50, 100)")
            },
            required=["tema_id", "incremento"]
        )
    )
    
    contents = []
    
    historial_limpio = [
        msg for msg in historial_mensajes
        if msg.get("role") in {"user", "model"} and msg.get("text")
    ]
    while historial_limpio and historial_limpio[0].get("role") != "user":
        historial_limpio.pop(0)

    for msg in historial_limpio:
        contents.append(types.Content(role=msg.get("role", "user"), parts=[types.Part.from_text(text=msg.get("text", ""))]))

    user_parts = []
    if image_data:
        try:
            mime = "image/jpeg"
            b64_str = image_data
            if "," in image_data:
                mime = image_data.split(";")[0].split(":")[1]
                b64_str = image_data.split(",")[1]
            img_bytes = base64.b64decode(b64_str)
            user_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
        except Exception as e:
            print(f"Error decodificando imagen V2: {e}")
            
    user_parts.append(types.Part.from_text(text=pregunta_actual))
    contents.append(types.Content(role="user", parts=user_parts))
    
    res = client.models.generate_content(
        model=modelo,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=sys_instruction,
            temperature=0.3,
            tools=[types.Tool(function_declarations=[actualizar_dominio_func])]
        ),
    )
    
    tool_called = False
    respuesta_texto = res.text or ""
    
    if res.function_calls:
        for call in res.function_calls:
            if call.name == "actualizar_dominio_tema":
                tema_id = call.args["tema_id"]
                incremento = int(call.args["incremento"])
                
                async def _update_progreso(s_list: list) -> list:
                    for s in s_list:
                        if s["id"] == slot_id:
                            for t in s.get("temas", []):
                                if t["id"] == tema_id:
                                    t["dominio"] = min(100, t.get("dominio", 0) + incremento)
                            
                            total = sum(t["dominio"] for t in s["temas"])
                            s["progreso_global"] = int(total / len(s["temas"]))
                            if s["progreso_global"] >= 100:
                                s["estado"] = "DOMINADO"
                    return s_list
                await progreso_store.update(_update_progreso)
                tool_called = True
                
        if not respuesta_texto.strip():
            respuesta_texto = "¡Excelente! Has demostrado dominio en este tema y he actualizado tu progreso en el sistema. Continuemos."

    return {
        "respuesta": respuesta_texto.strip(),
        "updated": tool_called
    }
