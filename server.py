from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
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
from tenacity import retry, stop_after_attempt, wait_exponential
import re
import nlp_engine

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
MATERIAS_FILE = "materias.json"
STATS_FILE = "stats.json"
VALID_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]

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

# Helper functions for Materias
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
            
            # Ensure keys exist
            for model in VALID_MODELS:
                if model not in stats:
                    stats[model] = {"peticiones": 0, "tokens": 0}
            return stats
    except Exception:
        return default_stats

def update_stats(model: str, tokens: int):
    stats = get_or_reset_stats()
    if model in stats:
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
        if not materia:
            raise HTTPException(status_code=404, detail="Materia no encontrada")
        prompt_usar = materia["prompt_personalizado"]
    else:
        prompt_usar = "Eres un asistente experto. Transcribe detalladamente o resume el audio identificando los temas tratados, puntos clave y tareas, sin asumir un contexto específico. Extrae todo el valor posible de forma estructurada en markdown."
        
    prompt_usar += "\n\nFORMATO OBLIGATORIO: Empieza tu respuesta ESTRICTAMENTE con:\nTAGS: [5 a 8 palabras clave separadas por comas]\nCONDENSADO: [resumen de 3 líneas]\n---\n[Luego tu resumen completo estructurado]"
    
    try:
        print(f"Procesando {filepath} con Gemini...")
        client = genai.Client()
        file = client.files.upload(file=filepath, config={'mime_type': 'audio/webm'})
        
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
                contents=[file, prompt_usar]
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
        
    md_filename = req.filename.replace(".webm", ".md")
    if md_filename.startswith("meet_"):
        mat_id = req.materia_id if (req.materia_id and req.materia_id != "default") else "default"
        md_filename = md_filename.replace("meet_", f"resumen__{mat_id}__meet_")
    
    md_filepath = os.path.join(RESUMENES_DIR, md_filename)
    
    # Parseo de Tags y Condensado
    import re
    tags = []
    condensado = ""
    texto_limpio = req.content
    
    parts = req.content.split("---", 1)
    if len(parts) > 1:
        meta_block = parts[0]
        texto_limpio = parts[1].strip()
        tags_match = re.search(r"TAGS:\s*(.*)", meta_block, re.IGNORECASE)
        if tags_match:
            tags = [t.strip() for t in tags_match.group(1).split(',')]
        condensado_match = re.search(r"CONDENSADO:\s*(.*)", meta_block, re.IGNORECASE)
        if condensado_match:
            condensado = condensado_match.group(1).strip()
            
    # Extraer fecha
    fecha_str = ""
    fecha_match = re.search(r"meet_(\d{4})(\d{2})(\d{2})", req.filename)
    if fecha_match:
        fecha_str = f"{fecha_match.group(1)}-{fecha_match.group(2)}-{fecha_match.group(3)}"

    # Guardar metadata
    meta_filepath = os.path.join(RESUMENES_DIR, "resumenes_meta.json")
    meta_data = {}
    if os.path.exists(meta_filepath):
        with open(meta_filepath, "r", encoding="utf-8") as fm:
            try:
                meta_data = json.load(fm)
            except:
                meta_data = {}
                
    meta_data[md_filename] = {
        "filename": md_filename,
        "tags": tags,
        "condensado": condensado,
        "fecha": fecha_str,
        "resumen": texto_limpio
    }
    
    with open(meta_filepath, "w", encoding="utf-8") as fm:
        json.dump(meta_data, fm, ensure_ascii=False, indent=2)

    with open(md_filepath, "w", encoding="utf-8") as f:
        f.write(texto_limpio)
        
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
            
    return {"message": "Guardado exitoso", "md_filename": md_filename}

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

@app.get("/info")
async def get_system_info():
    return {"status": "ok", "message": "Asistente de Clases v2 - Human in the Loop Architecture"}

os.makedirs("frontend", exist_ok=True)
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
