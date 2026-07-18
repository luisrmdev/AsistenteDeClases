from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import os
import shutil
from dotenv import load_dotenv
from google import genai

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

def process_audio_with_gemini(filepath: str, timestamp: str):
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
        
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=[file, prompt]
        )
        
        # Guardar en archivo .md
        md_filename = f"resumen_meet_{timestamp}.md"
        md_filepath = os.path.join(RESUMENES_DIR, md_filename)
        
        with open(md_filepath, "w", encoding="utf-8") as f:
            f.write(response.text)
            
        print(f"Resumen guardado exitosamente en {md_filepath}")
        
        # Borrar el archivo de audio original
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"Archivo de audio original {filepath} eliminado para ahorrar espacio.")
            
    except Exception as e:
        print(f"Error procesando el audio con Gemini: {e}")

@app.post("/upload")
async def upload_audio(background_tasks: BackgroundTasks, audio: UploadFile = File(...)):
    # Generar nombre único basado en fecha y hora (ej. meet_20260718_1030.webm)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Extraer extensión o usar .webm por defecto
    ext = os.path.splitext(audio.filename)[1] if audio.filename else ".webm"
    if not ext:
        ext = ".webm"
        
    filename = f"meet_{timestamp}{ext}"
    filepath = os.path.join(AUDIOS_DIR, filename)
    
    # Guardar el archivo en disco
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(audio.file, buffer)
        
    # Iniciar la tarea en segundo plano
    background_tasks.add_task(process_audio_with_gemini, filepath, timestamp)
        
    return {"message": "Audio subido correctamente y procesamiento en segundo plano iniciado", "filename": filename, "path": filepath}
