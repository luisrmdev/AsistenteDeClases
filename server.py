from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import os
import shutil
from dotenv import load_dotenv
from google import genai
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

app = FastAPI()

# Configurar CORS permitiendo todos los orígenes
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Asegurar que el directorio de audios exista
AUDIOS_DIR = "audios"
os.makedirs(AUDIOS_DIR, exist_ok=True)

# Directorio para los resúmenes
RESUMENES_DIR = "resumenes"
os.makedirs(RESUMENES_DIR, exist_ok=True)

# Directorio para papelera
PAPELERA_DIR = "papelera_audios"
os.makedirs(PAPELERA_DIR, exist_ok=True)

def process_audio_with_gemini(filepath: str, timestamp: str, custom_name: str = None):
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
        
        prompt = "Eres un asistente de reuniones. Transcribe detalladamente lo que se dice en este audio. Luego, elabora un resumen estructurado con: 1. Tema principal, 2. Puntos clave y decisiones, 3. Tareas o pendientes asignados."
        
        @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60))
        def call_gemini():
            print("Solicitando generación a Gemini (reintento automático en caso de saturación)...", flush=True)
            res = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=[file, prompt]
            )
            
            # Validación Post-Respuesta (Anti-Fallo Silencioso)
            if not res.text or len(res.text.strip()) < 50:
                raise ValueError("Respuesta vacía o demasiado corta detectada desde Gemini (posible fallo de decoding WebM). Forzando reintento.")
                
            return res
            
        response = call_gemini()
        
        # Guardar en archivo .md
        if custom_name:
            import re
            safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '', custom_name.replace(" ", "_"))
            md_filename = f"resumen_meet_{timestamp}_{safe_name}.md"
        else:
            md_filename = f"resumen_meet_{timestamp}.md"
            
        md_filepath = os.path.join(RESUMENES_DIR, md_filename)
        
        with open(md_filepath, "w", encoding="utf-8") as f:
            f.write(response.text)
            
        print(f"Resumen guardado exitosamente en {md_filepath}")
        
        # Mover a papelera en lugar de borrar para respaldo
        if os.path.exists(filepath):
            filename = os.path.basename(filepath)
            papelera_path = os.path.join(PAPELERA_DIR, filename)
            shutil.move(filepath, papelera_path)
            print(f"Archivo de audio movido a la papelera: {papelera_path}")
            
            # Limpiar papelera para no acumular basura (mantener últimos 10)
            papelera_files = [os.path.join(PAPELERA_DIR, f) for f in os.listdir(PAPELERA_DIR) if os.path.isfile(os.path.join(PAPELERA_DIR, f))]
            # Ordenar por fecha de creación o modificación (los más recientes primero)
            papelera_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            
            for old_file in papelera_files[10:]:
                try:
                    os.remove(old_file)
                    print(f"Archivo viejo {old_file} eliminado de la papelera (límite de 10).")
                except Exception as e:
                    print(f"Error eliminando de papelera: {e}")
            
    except Exception as e:
        print(f"Error procesando el audio con Gemini: {e}")

@app.post("/upload")
async def upload_audio(background_tasks: BackgroundTasks, audio: UploadFile = File(...), custom_name: str = Form(None)):
    # Generar nombre único basado en fecha y hora (ej. meet_20260718_1030.webm)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Extraer extensión o usar .webm por defecto
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
    
    # Guardar el archivo en disco
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(audio.file, buffer)
        
    # Iniciar la tarea en segundo plano
    background_tasks.add_task(process_audio_with_gemini, filepath, timestamp, custom_name)
        
    return {"message": "Audio subido correctamente y procesamiento en segundo plano iniciado", "filename": filename, "path": filepath}

@app.get("/retry/{filename}")
async def retry_processing(filename: str, background_tasks: BackgroundTasks):
    import re
    filepath = os.path.join(AUDIOS_DIR, filename)
    
    if not os.path.exists(filepath):
        return {"error": f"Archivo {filename} no encontrado en la carpeta de audios."}
    
    # Extraer el timestamp original y posible custom_name
    match = re.search(r'meet_(\d{8}_\d{6})_?(.*?)\.', filename)
    if match:
        timestamp = match.group(1)
        custom_name = match.group(2) if match.group(2) else None
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        custom_name = None
    
    background_tasks.add_task(process_audio_with_gemini, filepath, timestamp, custom_name)
    return {"message": f"Se ha iniciado el procesamiento manual de {filename} en segundo plano."}

@app.get("/info")
async def get_system_info():
    # Intentar leer las dependencias del backend de requirements.txt
    backend_libs = {}
    try:
        with open("requirements.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split("==")
                    if len(parts) == 2:
                        backend_libs[parts[0]] = parts[1]
                    else:
                        backend_libs[line] = "latest (no anclada)"
    except Exception:
        backend_libs = {"error": "No se pudo leer requirements.txt"}
        
    # Obtener todas las rutas de la API dinámicamente
    rutas = []
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            methods = list(route.methods - {"OPTIONS"}) if route.methods else []
            if methods:  # ignorar rutas internas sin métodos HTTP explícitos
                rutas.append({
                    "path": route.path,
                    "methods": methods
                })
        
    return {
        "proyecto": "Asistente de Clases - Grabador Multi-Pestaña con IA",
        "extension_chrome": {
            "descripcion": "Extensión para captura de audio por pestaña con auto-apagado inteligente",
            "arquitectura": "Manifest V3",
            "gestor_grabacion": "Offscreen Documents (MediaRecorder nativo generando formato webm/opus)",
            "almacenamiento_estado": "chrome.storage.local",
            "automatizacion_inteligente": "chrome.alarms (Auto-apagado), Pausa interactiva y chrome.tabs.onUpdated (Detección de silencio)",
            "soporte_etiquetas": "Permite asignar temas o nombres personalizados a los resúmenes directamente en el UI",
            "librerias_terceros": "Ninguna (Se usa Vanilla JS y CSS Nativo para máxima compatibilidad CSP)"
        },
        "backend": {
            "lenguaje": "Python 3",
            "framework_api": "FastAPI",
            "servidor_web": "Uvicorn (ASGI)",
            "procesamiento_asincrono": "BackgroundTasks de FastAPI",
            "rutas_disponibles": rutas,
            "librerias_instaladas": backend_libs
        },
        "inteligencia_artificial": {
            "proveedor": "Google Gemini",
            "sdk_utilizado": "google-genai",
            "modelo_asignado": "gemini-3.5-flash",
            "sistema_resiliencia": "Librería 'tenacity' (Exponential Backoff para reintentos ante error 503)",
            "manejo_archivos": "Gemini File API con polling de estado (ESPERA de PROCESSING a ACTIVE)",
            "prompt_base": "Eres un asistente de reuniones. Transcribe detalladamente lo que se dice en este audio. Luego, elabora un resumen estructurado con: 1. Tema principal, 2. Puntos clave y decisiones, 3. Tareas o pendientes asignados."
        }
    }
