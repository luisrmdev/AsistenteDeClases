from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime
import os
import shutil
import json
import uuid
from dotenv import load_dotenv
from google import genai
from google.genai import types
import yt_dlp
from tenacity import retry, stop_after_attempt, wait_exponential
import re
import nlp_engine
import subprocess

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIOS_DIR = "audios"
os.makedirs(AUDIOS_DIR, exist_ok=True)
RESUMENES_DIR = "resumenes"
os.makedirs(RESUMENES_DIR, exist_ok=True)
PAPELERA_DIR = "papelera_audios"
os.makedirs(PAPELERA_DIR, exist_ok=True)
EXPORTACIONES_DIR = "exportaciones"
os.makedirs(EXPORTACIONES_DIR, exist_ok=True)
MATERIAS_FILE = "materias.json"
STATS_FILE = "stats.json"

# Models
class MateriaCreate(BaseModel):
    nombre: str
    prompt_personalizado: str

class MateriaUpdate(BaseModel):
    nombre: str
    prompt_personalizado: str

class GenerateRequest(BaseModel):
    filename: str
    materia_id: str
    modelo_elegido: str = "gemini-3.1-flash-lite"

class SaveRequest(BaseModel):
    filename: str
    content: str
    materia_id: str = None

class PromptGenRequest(BaseModel):
    descripcion: str
    modelo_elegido: str = "gemini-3.1-flash-lite"

class ChatRequest(BaseModel):
    mensaje: str
    materia_id: str
    modelo_elegido: str = "gemini-3.1-flash-lite"

class SummaryUpdate(BaseModel):
    content: str

class SettingsUpdate(BaseModel):
    obsidian_vault_path: str
    enable_anki: bool = True
    browser_cookie_source: str = "brave"

class TaskExtractRequest(BaseModel):
    image_base64: str

class DriveDownloadRequest(BaseModel):
    url: str

# Helper functions for Materias
def load_app_settings():
    if not os.path.exists("settings.json"):
        return {"obsidian_vault_path": os.getenv("OBSIDIAN_VAULT_PATH", "")}
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"obsidian_vault_path": os.getenv("OBSIDIAN_VAULT_PATH", "")}

def save_app_settings(data):
    with open("settings.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
def load_materias():
    if not os.path.exists(MATERIAS_FILE):
        return []
    try:
        with open(MATERIAS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_materias(materias):
    with open(MATERIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(materias, f, ensure_ascii=False, indent=4)

def get_or_reset_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    default_stats = {
        "fecha": today,
        "gemini-3.5-flash": {"peticiones": 0, "tokens": 0},
        "gemini-3.1-flash-lite": {"peticiones": 0, "tokens": 0}
    }
    
    if not os.path.exists(STATS_FILE):
        return default_stats
        
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            stats = json.load(f)
            if stats.get("fecha") != today:
                return default_stats
            return stats
    except Exception:
        return default_stats

def update_stats(model: str, tokens: int):
    stats = get_or_reset_stats()
    if model not in stats:
        stats[model] = {"peticiones": 0, "tokens": 0}
    
    stats[model]["peticiones"] += 1
    stats[model]["tokens"] += tokens
    
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=4)
    return stats

@app.get("/api/stats")
async def get_stats_endpoint():
    return get_or_reset_stats()

# Mount media to serve audio files for HTML <audio>
app.mount("/media", StaticFiles(directory=AUDIOS_DIR), name="media")

# --- Materias Endpoints ---
@app.get("/api/materias")
async def get_materias():
    return load_materias()

@app.post("/api/materias")
async def create_materia(materia: MateriaCreate):
    materias = load_materias()
    new_materia = {
        "id": str(uuid.uuid4()),
        "nombre": materia.nombre,
        "prompt_personalizado": materia.prompt_personalizado
    }
    materias.append(new_materia)
    save_materias(materias)
    return new_materia

@app.put("/api/materias/{materia_id}")
async def update_materia(materia_id: str, materia: MateriaUpdate):
    materias = load_materias()
    for m in materias:
        if m["id"] == materia_id:
            m["nombre"] = materia.nombre
            m["prompt_personalizado"] = materia.prompt_personalizado
            save_materias(materias)
            return m
    raise HTTPException(status_code=404, detail="Materia no encontrada")

@app.delete("/api/materias/{materia_id}")
async def delete_materia(materia_id: str):
    materias = load_materias()
    new_materias = [m for m in materias if m["id"] != materia_id]
    if len(materias) == len(new_materias):
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    save_materias(new_materias)
    return {"message": "Materia eliminada"}

# --- Settings Endpoints ---
@app.get("/api/settings")
async def get_settings_endpoint():
    return load_app_settings()

@app.put("/api/settings")
async def update_settings_endpoint(req: SettingsUpdate):
    settings = load_app_settings()
    settings["obsidian_vault_path"] = req.obsidian_vault_path
    settings["enable_anki"] = req.enable_anki
    settings["browser_cookie_source"] = req.browser_cookie_source
    save_app_settings(settings)
    return {"message": "Configuración actualizada"}

# --- Models Endpoint ---
@app.get("/api/models")
async def get_available_models():
    try:
        client = genai.Client()
        modelos = client.models.list()
        
        valid_models = []
        for model in modelos:
            if hasattr(model, 'supported_actions') and model.supported_actions and "generateContent" in model.supported_actions:
                model_name = model.name.replace("models/", "") if model.name.startswith("models/") else model.name
                # Filtrar modelos legacy (bison) y gemini 1.0 (que no soportan audio)
                if "gemini" in model_name and "1.0" not in model_name:
                    valid_models.append({
                        "id": model_name,
                        "name": model_name,
                        "description": getattr(model, 'description', 'Modelo de IA')
                    })
        
        # Sort so that flash models appear first if possible
        valid_models.sort(key=lambda x: ("flash" not in x["id"].lower(), x["id"]))
        return {"models": valid_models}
    except Exception as e:
        print("Error fetching models:", e)
        return {"models": [
            {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "description": "Modelo rápido por defecto."},
            {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash Lite", "description": "Modelo ligero por defecto."}
        ]}

# --- Audios Pendientes Endpoints ---
@app.get("/api/audios")
async def list_pending_audios():
    if not os.path.exists(AUDIOS_DIR):
        return {"audios": []}
    files = [f for f in os.listdir(AUDIOS_DIR) if f.endswith('.webm')]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(AUDIOS_DIR, x)), reverse=True)
    
    audios = []
    for f in files:
        name_parts = f.replace(".webm", "").split("_")
        display_name = f
        if len(name_parts) >= 4:
            date_str = name_parts[2]
            if len(date_str) == 8:
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            
            custom_name = ""
            if len(name_parts) >= 5:
                custom_name = " ".join(name_parts[4:])
                
            if custom_name:
                display_name = f"{custom_name} ({date_str})"
            else:
                display_name = f"Grabación {date_str}"
        else:
            display_name = f
        audios.append({"filename": f, "display_name": display_name})
    return {"audios": audios}

@app.delete("/api/audios/{filename}")
async def delete_audio(filename: str):
    if not filename.endswith('.webm'):
        raise HTTPException(status_code=400, detail="Formato inválido")
    filepath = os.path.join(AUDIOS_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return {"message": "Audio eliminado exitosamente"}
    raise HTTPException(status_code=404, detail="Archivo de audio no encontrado")

# --- Original Upload ---
@app.post("/upload")
async def upload_audio(audio: UploadFile = File(...), custom_name: str = Form(None)):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(audio.filename)[1] if audio.filename else ".webm"
    if not ext:
        ext = ".webm"
        
    if custom_name:
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '', custom_name.replace(" ", "_"))
        filename = f"meet_{timestamp}_{safe_name}{ext}"
    else:
        filename = f"meet_{timestamp}{ext}"
        
    filepath = os.path.join(AUDIOS_DIR, filename)
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(audio.file, buffer)
        
    return {"message": "Audio subido correctamente", "filename": filename, "path": filepath}

# --- AI Prompt Gen ---
@app.post("/api/generate-prompt")
async def generate_prompt(req: PromptGenRequest):
    client = genai.Client()
    sys_prompt = "Eres un ingeniero de prompts experto. Tu objetivo es convertir la petición natural del usuario en un prompt de sistema robusto, estructurado con [Rol], [Contexto], [Tarea] y [Formato de Salida] listos para dárselo a otro LLM. Responde ÚNICAMENTE con el prompt generado final, sin introducciones, saludos ni comillas extra. Hazlo directo."
    try:
        res = client.models.generate_content(
            model=req.modelo_elegido,
            contents=[f"Petición natural del usuario: {req.descripcion}", sys_prompt]
        )
        tokens = res.usage_metadata.total_token_count if res.usage_metadata else 0
        stats = update_stats(req.modelo_elegido, tokens)
        return {"prompt_generado": res.text.strip(), "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Chat de Estudio (RAG) ---
@app.post("/api/chat")
async def chat_estudio(req: ChatRequest):
    files = [f for f in os.listdir(RESUMENES_DIR) if f.endswith('.md')]
    valid_files = []
    
    if req.materia_id != "todas":
        if req.materia_id == "default":
            # Viejos y los marcados como default
            valid_files = [f for f in files if "__default__" in f or "__" not in f]
        else:
            valid_files = [f for f in files if f"__{req.materia_id}__" in f]
    else:
        valid_files = files
    # CRÍTICO: Ordenar alfabéticamente para caché inmutable
    valid_files.sort()
    # CRÍTICO: Leer metadata
    meta_filepath = os.path.join(RESUMENES_DIR, "resumenes_meta.json")
    meta_data = {}
    if os.path.exists(meta_filepath):
        with open(meta_filepath, "r", encoding="utf-8") as fm:
            meta_data = json.load(fm)

    # 1. Filtro Temporal
    date_range = nlp_engine.parse_temporal_filter(req.mensaje)
    
    filtered_summaries = []
    for f in valid_files:
        if f in meta_data:
            f_meta = meta_data[f]
            f_fecha_str = f_meta.get("fecha", "")
            if date_range and f_fecha_str:
                try:
                    f_fecha = datetime.strptime(f_fecha_str, "%Y-%m-%d")
                    if not (date_range[0] <= f_fecha <= date_range[1]):
                        continue
                except:
                    pass
            filtered_summaries.append(f_meta)

    # 2. Índice Condensado cronológico
    filtered_summaries.sort(key=lambda x: x.get("fecha", ""))
    indice_condensado_textos = []
    for m in filtered_summaries:
        indice_condensado_textos.append(f"- {m.get('fecha', 'N/A')}: {m.get('condensado', '')}")

    # 3. Scoring de Relevancia
    relevant_summaries = nlp_engine.score_relevance(req.mensaje, filtered_summaries)
    relevant_filenames = {m.get("filename") for m in relevant_summaries if m.get("filename")}

    # 4. Resúmenes Completos
    relevant_list = [m for m in filtered_summaries if m.get("filename") in relevant_filenames]
    resumenes_completos_textos = []
    for m in relevant_list:
        resumenes_completos_textos.append(f"\n\n--- Documento: {m.get('filename')} ---\n{m.get('resumen', '')}")
            
    client = genai.Client()
    sys_instruction = "Eres mi tutor universitario experto. Basa tus respuestas ESTRICTAMENTE en mis documentos de estudio proporcionados. Si la información no está explícitamente en los apuntes, indica claramente que 'no se menciona en los apuntes de clase'."
    
    try:
        res = client.models.generate_content(
            model=req.modelo_elegido,
            contents=[
                "ÍNDICE CONDENSADO (Filtrado temporalmente):\n" + "\n".join(indice_condensado_textos),
                "APUNTES COMPLETOS (Más relevantes):\n" + "".join(resumenes_completos_textos),
                req.mensaje
            ],
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction
            )
        )
        tokens = res.usage_metadata.total_token_count if res.usage_metadata else 0
        stats = update_stats(req.modelo_elegido, tokens)
        return {"respuesta": res.text.strip(), "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Human-in-the-Loop AI Endpoints ---
@app.post("/api/generate")
async def generate_summary(req: GenerateRequest):
    filepath = os.path.join(AUDIOS_DIR, req.filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo de audio no encontrado")
        
    prompt_usar = ""
    if req.materia_id and req.materia_id != "default":
        materias = load_materias()
        materia = next((m for m in materias if m["id"] == req.materia_id), None)
        if materia:
            prompt_usar = materia["prompt_personalizado"]
            
    fecha_actual = datetime.now().strftime("%Y-%m-%d")
    
    settings = load_app_settings()
    anki_json = '  "anki_cards": [{"pregunta": "...", "respuesta": "..."}],' if settings.get("enable_anki", True) else ''
    
    prompt_completo = f"""Eres un experto en Personal Knowledge Management (PKM) y un ingeniero de software senior. La fecha de hoy es {fecha_actual}. 
Contexto de la clase: {prompt_usar}

A partir de la transcripción, debes crear una nota que cumpla ESTRICTAMENTE las siguientes reglas de mi bóveda de Obsidian:
1. ESTRUCTURA DE CARPETAS: Mi bóveda usa PARA (01 Proyectos, 02 Recursos, 03 Areas, 04 Archivo).
2. REGLA DE TITULACIÓN (Googleability): Una nota = Un solo concepto. Títulos directos. Ej Teórico: "Costo de Oportunidad". Ej Técnico: "Cómo configurar Git con SSH en Linux".
3. ARQUETIPOS DE NOTA:
   A. Técnico / Cheat Sheet: Inicia con > [!warning] o > [!info]. Pasos directos sin relleno. Bloques de código especificados.
   B. Teórico: Inicia con > [!summary] (Resumen Feynman). Desarrollo en viñetas. Ejemplos con > [!example].
4. CERO NOTAS HUÉRFANAS: Al final de la nota, incluye un enlace de Obsidian a un concepto relacionado (ej. [[Índice - Semestre actual]]).

Genera tu respuesta en el siguiente formato ESTRICTO:
---
tipo: [teoria o cheatsheet]
estado: borrador
tags: [tag1, tag2]
---

[Contenido de la nota usando Callouts de Obsidian y estructura requerida]

[Enlace MOC o Concepto Relacionado]

$$AL FINAL DEL ARCHIVO, INCLUYE ESTRICTAMENTE ESTE BLOQUE JSON$$
```json
{{
  "filename": "Titulo Exacto Googleable.md",
  "folder": "02 Recursos/Tema",
{anki_json}
  "calendario": [{{"titulo": "...", "fecha_YYYY_MM_DD": "...", "descripcion": "..."}}]
}}
```"""
    
    try:
        print(f"Procesando {filepath} con Gemini...")
        client = genai.Client()
        
        # FFmpeg: Remover silencios
        temp_filepath = os.path.join(AUDIOS_DIR, "temp_" + req.filename)
        upload_path = filepath
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", filepath,
                "-af", "silenceremove=stop_periods=-1:stop_duration=2:stop_threshold=-30dB",
                temp_filepath
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(temp_filepath):
                upload_path = temp_filepath
                print("Audio optimizado con FFmpeg.")
        except Exception as e:
            print(f"Fallo FFmpeg (usando original): {e}")

        file = client.files.upload(file=upload_path, config={'mime_type': 'audio/webm'})
        
        if upload_path == temp_filepath and os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        
        import time
        print("Esperando a que Gemini procese el archivo...", flush=True)
        file_info = client.files.get(name=file.name)
        while file_info.state.name == "PROCESSING":
            time.sleep(3)
            file_info = client.files.get(name=file.name)
            
        if file_info.state.name == "FAILED":
            raise Exception("Gemini falló al procesar el archivo.")
        
        print("Archivo listo. Generando resumen...", flush=True)
        
        @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
        def call_gemini():
            print("Solicitando generación a Gemini...", flush=True)
            res = client.models.generate_content(
                model=req.modelo_elegido,
                contents=[file, prompt_completo]
            )
            if not res.text or len(res.text.strip()) < 50:
                raise ValueError("Respuesta vacía o corta. Forzando reintento.")
            return res
            
        response = call_gemini()
        tokens = response.usage_metadata.total_token_count if response.usage_metadata else 0
        stats = update_stats(req.modelo_elegido, tokens)
        
        return {"content": response.text, "stats": stats}
        
    except Exception as e:
        print(f"Error procesando el audio con Gemini: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/save")
async def save_summary(req: SaveRequest):
    filepath = os.path.join(AUDIOS_DIR, req.filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo de audio no encontrado")
        
    fecha_str = datetime.now().strftime("%Y-%m-%d")

    md_filename = req.filename.replace(".webm", ".md")
    if md_filename.startswith("meet_"):
        mat_id = req.materia_id if (req.materia_id and req.materia_id != "default") else "default"
        md_filename = md_filename.replace("meet_", f"resumen__{mat_id}__meet_")
    
    md_filepath = os.path.join(RESUMENES_DIR, md_filename)
    
    # Parseo de Tags y Condensado
    texto_limpio = req.content
    json_data = {}
    anki_file = None
    ics_file = None
    
    # Extraer el bloque JSON
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', texto_limpio, re.DOTALL | re.IGNORECASE)
    if not json_match:
        # Fallback sin markdown ticks
        json_match = re.search(r'(\{[\s\n]*"filename".*?\})', texto_limpio, re.DOTALL | re.IGNORECASE)

    if json_match:
        json_str = json_match.group(1)
        texto_limpio = texto_limpio.replace(json_match.group(0), "").strip()
        try:
            json_data = json.loads(json_str)
        except Exception as e:
            print("Error parseando JSON de Gemini:", e)
            
    suggested_filename = json_data.get("filename", md_filename).replace("/", "-")
    if not suggested_filename.endswith(".md"):
        suggested_filename += ".md"
        
    suggested_folder = json_data.get("folder", "")
            
    # Guardar en Obsidian si existe la ruta
    settings = load_app_settings()
    obsidian_path = settings.get("obsidian_vault_path", "")
    if obsidian_path and os.path.exists(obsidian_path):
        target_dir = os.path.join(obsidian_path, suggested_folder) if suggested_folder else obsidian_path
        os.makedirs(target_dir, exist_ok=True)
        obs_file = os.path.join(target_dir, suggested_filename)
        try:
            with open(obs_file, "w", encoding="utf-8") as f:
                f.write(texto_limpio)
        except Exception as e:
            print("Error guardando en Obsidian:", e)

    # 1. Extraer tags manuales si aplican (Frontmatter YAML)
    tags_match = re.search(r"tags:\s*\[(.*?)\]", texto_limpio, re.IGNORECASE)
    if not tags_match:
        tags_match = re.search(r"tags:\s*(.*)", texto_limpio, re.IGNORECASE)
    tags = [t.strip().strip('"').strip("'") for t in tags_match.group(1).split(',')] if tags_match else []
    
    meta_filepath = os.path.join(RESUMENES_DIR, "resumenes_meta.json")
    meta_data = {}
    if os.path.exists(meta_filepath):
        with open(meta_filepath, "r", encoding="utf-8") as fm:
            try:
                meta_data = json.load(fm)
            except:
                meta_data = {}
                
    meta_data[md_filename] = {
        "filename": suggested_filename,
        "folder": suggested_folder,
        "tags": tags,
        "condensado": "Resumen autogenerado de la clase.",
        "fecha": fecha_str,
        "resumen": texto_limpio
    }
    
    with open(meta_filepath, "w", encoding="utf-8") as fm:
        json.dump(meta_data, fm, ensure_ascii=False, indent=2)

    with open(os.path.join(RESUMENES_DIR, suggested_filename), "w", encoding="utf-8") as f:
        f.write(texto_limpio)
        
    # 2. Generar Anki (.apkg)
    if json_data and "anki_cards" in json_data and json_data["anki_cards"]:
        try:
            import genanki
            import random
            deck_id = random.randrange(1 << 30, 1 << 31)
            model_id = random.randrange(1 << 30, 1 << 31)
            
            my_model = genanki.Model(
                model_id,
                'Modelo Asistente Clases',
                fields=[
                    {'name': 'Pregunta'},
                    {'name': 'Respuesta'},
                ],
                templates=[
                    {
                        'name': 'Tarjeta 1',
                        'qfmt': '{{Pregunta}}',
                        'afmt': '{{FrontSide}}<hr id="answer">{{Respuesta}}',
                    },
                ])
            
            my_deck = genanki.Deck(deck_id, f'Asistente::{md_filename.replace(".md", "")}')
            
            for card in json_data["anki_cards"]:
                pregunta = card.get("pregunta", "")
                respuesta = card.get("respuesta", "")
                if pregunta and respuesta:
                    note = genanki.Note(model=my_model, fields=[pregunta, respuesta])
                    my_deck.add_note(note)
                    
            anki_file = f"{md_filename.replace('.md', '')}.apkg"
            genanki.Package(my_deck).write_to_file(os.path.join(EXPORTACIONES_DIR, anki_file))
        except Exception as e:
            print("Error generando Anki:", e)

    # 3. Generar Calendario (.ics) y Guardar en Tareas
    if json_data and "calendario" in json_data and json_data["calendario"]:
        # Guardar en Base de Datos de Tareas (JSON)
        tareas_meta_filepath = os.path.join(RESUMENES_DIR, "tareas_meta.json")
        tareas_data = []
        if os.path.exists(tareas_meta_filepath):
            with open(tareas_meta_filepath, "r", encoding="utf-8") as fm:
                try: tareas_data = json.load(fm)
                except: pass
        
        import uuid
        for t in json_data["calendario"]:
            t["id"] = str(uuid.uuid4())
            t["origen"] = suggested_filename
            t["completada"] = False
            tareas_data.append(t)
            
        with open(tareas_meta_filepath, "w", encoding="utf-8") as fm:
            json.dump(tareas_data, fm, ensure_ascii=False, indent=2)

        # Generar ICS File
        try:
            from icalendar import Calendar, Event
            cal = Calendar()
            for evento in json_data["calendario"]:
                fecha_evt_str = evento.get("fecha_YYYY_MM_DD")
                if fecha_evt_str:
                    try:
                        dt = datetime.strptime(fecha_evt_str, "%Y-%m-%d").date()
                        ievent = Event()
                        ievent.add('summary', evento.get("titulo", "Sin título"))
                        ievent.add('dtstart', dt)
                        ievent.add('description', evento.get("descripcion", ""))
                        cal.add_component(ievent)
                    except Exception as e:
                        print("Error parseando fecha para ICS:", e)
                        
            ics_file = f"{md_filename.replace('.md', '')}.ics"
            with open(os.path.join(EXPORTACIONES_DIR, ics_file), 'wb') as f:
                f.write(cal.to_ical())
        except Exception as e:
            print("Error generando Calendario:", e)
        
    # Mover a papelera
    filename = os.path.basename(filepath)
    papelera_path = os.path.join(PAPELERA_DIR, filename)
    shutil.move(filepath, papelera_path)
    
    papelera_files = [os.path.join(PAPELERA_DIR, f) for f in os.listdir(PAPELERA_DIR) if os.path.isfile(os.path.join(PAPELERA_DIR, f))]
    papelera_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    for old_file in papelera_files[10:]:
        try:
            os.remove(old_file)
        except Exception:
            pass
            
    return {
        "message": "Guardado exitoso", 
        "md_filename": md_filename,
        "anki_file": anki_file,
        "ics_file": ics_file
    }

# --- Original Summaries Endpoints (For the UI) ---
@app.get("/api/summaries")
async def list_summaries():
    if not os.path.exists(RESUMENES_DIR):
        return {"summaries": []}
    files = [f for f in os.listdir(RESUMENES_DIR) if f.endswith('.md')]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(RESUMENES_DIR, x)), reverse=True)
    summaries = []
    for f in files:
        # Extraer nombre amigable ocultando los IDs
        import re
        display_name = f
        
        # Eliminar el bloque __id__ si existe
        clean_name = re.sub(r'__.*?__', '', f).replace('resumen_meet_', '').replace('.md', '')
        
        parts = clean_name.split("_")
        if len(parts) >= 2:
            date_str = parts[0]
            if len(date_str) == 8:
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            
            custom_name = ""
            if len(parts) >= 3:
                custom_name = " ".join(parts[2:])
                
            if custom_name:
                display_name = f"{custom_name} ({date_str})"
            else:
                display_name = f"Reunión {date_str}"
        
        summaries.append({"filename": f, "display_name": display_name})
    return {"summaries": summaries}

@app.get("/api/summaries/{filename}")
async def get_summary(filename: str):
    if not filename.endswith('.md'):
        return {"error": "Solo se permiten archivos markdown."}
    filepath = os.path.join(RESUMENES_DIR, filename)
    if not os.path.exists(filepath):
        return {"error": "Archivo no encontrado."}
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return {"content": content}

@app.put("/api/summaries/{filename}")
async def update_summary(filename: str, req: SummaryUpdate):
    if not filename.endswith('.md'):
        raise HTTPException(status_code=400, detail="Solo archivos markdown.")
    filepath = os.path.join(RESUMENES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(req.content)
        
    meta_filepath = os.path.join(RESUMENES_DIR, "resumenes_meta.json")
    if os.path.exists(meta_filepath):
        with open(meta_filepath, "r", encoding="utf-8") as fm:
            meta_data = json.load(fm)
        if filename in meta_data:
            meta_data[filename]["resumen"] = req.content
            with open(meta_filepath, "w", encoding="utf-8") as fm:
                json.dump(meta_data, fm, ensure_ascii=False, indent=2)
                
    return {"message": "Resumen actualizado"}

@app.delete("/api/summaries/{filename}")
async def delete_summary(filename: str):
    if not filename.endswith('.md'):
        raise HTTPException(status_code=400, detail="Solo archivos markdown.")
    filepath = os.path.join(RESUMENES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    os.remove(filepath)
    
    meta_filepath = os.path.join(RESUMENES_DIR, "resumenes_meta.json")
    if os.path.exists(meta_filepath):
        with open(meta_filepath, "r", encoding="utf-8") as fm:
            meta_data = json.load(fm)
        if filename in meta_data:
            del meta_data[filename]
            with open(meta_filepath, "w", encoding="utf-8") as fm:
                json.dump(meta_data, fm, ensure_ascii=False, indent=2)
                
    return {"message": "Resumen eliminado"}

@app.get("/api/exportaciones/{filename}")
async def download_export(filename: str):
    filepath = os.path.join(EXPORTACIONES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(filepath, filename=filename)

@app.get("/info")
async def get_system_info():
    return {"status": "ok", "message": "Asistente de Clases v2 - Human in the Loop Architecture"}

@app.get("/api/tareas")
async def get_tareas():
    tareas_meta_filepath = os.path.join(RESUMENES_DIR, "tareas_meta.json")
    if os.path.exists(tareas_meta_filepath):
        try:
            with open(tareas_meta_filepath, "r", encoding="utf-8") as fm:
                return {"tareas": json.load(fm)}
        except: pass
    return {"tareas": []}

@app.put("/api/tareas/{tarea_id}")
async def update_tarea(tarea_id: str, payload: dict):
    tareas_meta_filepath = os.path.join(RESUMENES_DIR, "tareas_meta.json")
    if os.path.exists(tareas_meta_filepath):
        with open(tareas_meta_filepath, "r", encoding="utf-8") as fm:
            tareas = json.load(fm)
        for t in tareas:
            if t.get("id") == tarea_id:
                if "completada" in payload:
                    t["completada"] = payload["completada"]
        with open(tareas_meta_filepath, "w", encoding="utf-8") as fm:
            json.dump(tareas, fm, ensure_ascii=False, indent=2)
    return {"message": "ok"}
@app.post("/api/extract-task")
async def extract_task_from_image(req: TaskExtractRequest):
    import base64
    b64_str = req.image_base64.split(",")[1] if "," in req.image_base64 else req.image_base64
    img_bytes = base64.b64decode(b64_str)
    
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

[Contenido de la nota con detalles de la tarea]

$$AL FINAL DEL ARCHIVO, INCLUYE ESTRICTAMENTE ESTE BLOQUE JSON$$
```json
{
  "filename": "Titulo Tarea.md",
  "folder": "01 Proyectos/Tareas",
  "calendario": [{"titulo": "...", "fecha_YYYY_MM_DD": "...", "descripcion": "..."}]
}
```"""
    try:
        client = genai.Client()
        # Fallback para asegurarse de que el modelo soporta multimodality
        model_name = 'gemini-3.5-flash'
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'),
                prompt
            ]
        )
        texto_limpio = response.text
        json_data = {}
        
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', texto_limpio, re.DOTALL | re.IGNORECASE)
        if not json_match:
            json_match = re.search(r'(\{[\s\n]*"filename".*?\})', texto_limpio, re.DOTALL | re.IGNORECASE)

        if json_match:
            json_str = json_match.group(1)
            texto_limpio = texto_limpio.replace(json_match.group(0), "").strip()
            try:
                json_data = json.loads(json_str)
            except Exception:
                pass
                
        suggested_filename = json_data.get("filename", f"Tarea_Captura_{datetime.now().strftime('%Y%m%d%H%M%S')}.md").replace("/", "-")
        if not suggested_filename.endswith(".md"):
            suggested_filename += ".md"
            
        suggested_folder = json_data.get("folder", "01 Proyectos/Tareas")
        
        # Save to Obsidian
        settings = load_app_settings()
        obsidian_path = settings.get("obsidian_vault_path", "")
        if obsidian_path and os.path.exists(obsidian_path):
            target_dir = os.path.join(obsidian_path, suggested_folder)
            os.makedirs(target_dir, exist_ok=True)
            obs_file = os.path.join(target_dir, suggested_filename)
            try:
                with open(obs_file, "w", encoding="utf-8") as f:
                    f.write(texto_limpio)
            except Exception: pass
            
        with open(os.path.join(RESUMENES_DIR, suggested_filename), "w", encoding="utf-8") as f:
            f.write(texto_limpio)
            
        if "calendario" in json_data and json_data["calendario"]:
            tareas_meta_filepath = os.path.join(RESUMENES_DIR, "tareas_meta.json")
            tareas_data = []
            if os.path.exists(tareas_meta_filepath):
                with open(tareas_meta_filepath, "r", encoding="utf-8") as fm:
                    try: tareas_data = json.load(fm)
                    except: pass
            
            import uuid
            for t in json_data["calendario"]:
                t["id"] = str(uuid.uuid4())
                t["origen"] = suggested_filename
                t["completada"] = False
                tareas_data.append(t)
                
            with open(tareas_meta_filepath, "w", encoding="utf-8") as fm:
                json.dump(tareas_data, fm, ensure_ascii=False, indent=2)
                
        return {"message": "Tarea extraída y guardada correctamente", "filename": suggested_filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/download-drive")
async def download_drive(req: DriveDownloadRequest, background_tasks: BackgroundTasks):
    settings = load_app_settings()
    browser_choice = settings.get("browser_cookie_source", "brave")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{AUDIOS_DIR}/%(title)s.%(ext)s',
        'cookiesfrombrowser': (browser_choice,),
        'quiet': False
    }
    
    # Intentar con authuser=0, 1 y 2 (por si tiene múltiples cuentas de Google, e.g. Personal y Universidad)
    success = False
    last_error = ""
    
    for authuser in [0, 1, 2]:
        try:
            # Modificamos la URL para forzar el authuser
            test_url = req.url
            if "?" in test_url:
                test_url = test_url + f"&authuser={authuser}"
            else:
                test_url = test_url + f"?authuser={authuser}"
                
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(test_url, download=True)
                filename = ydl.prepare_filename(info)
                basename = os.path.basename(filename)
                
                gen_req = GenerateRequest(
                    filename=basename,
                    materia_id="default",
                    modelo_elegido="gemini-3.5-flash"
                )
                background_tasks.add_task(generate_summary, gen_req)
                
            success = True
            break
        except Exception as e:
            last_error = str(e)
            if "403: Forbidden" not in last_error:
                # Si no es un 403, probablemente el link es inválido o no funciona yt-dlp, rompemos igual
                break
                
    if not success:
        if "403: Forbidden" in last_error:
            raise HTTPException(
                status_code=403, 
                detail=f"Google Drive bloqueó la descarga (403 Forbidden). Esto sucede si la clase es de una cuenta institucional que bloquea extracciones externas o si no tienes permiso. SOLUCIÓN: Reproduce el video en Drive y usa la pestaña 'Grabar Audio' de la extensión de Chrome."
            )
        else:
            raise HTTPException(status_code=400, detail=f"Error en yt-dlp usando cookies de {browser_choice}: {last_error}")
            
    return {"status": "success", "message": "Audio extraído y procesado en segundo plano."}

# Servir frontend estáticamente (Debe ir siempre al final)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
