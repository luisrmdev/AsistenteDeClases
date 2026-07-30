"""
server.py — Punto de entrada de FastAPI.
Responsabilidad única: definir rutas y delegar a los servicios.
"""
import base64
import os
import re
import shutil
import uuid
import zipfile
import subprocess
from datetime import datetime

import asyncio
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import (
    AUDIOS_DIR,
    EXPORTACIONES_DIR,
    PAPELERA_DIR,
    RESUMENES_DIR,
    materias_store,
    meta_store,
    settings_store,
    tarjetas_store,
    cola_store,
)
from services import audio_service, export_service, llm_service

from dotenv import load_dotenv
load_dotenv()


# ===========================================================================
# Worker Task Queue
# ===========================================================================

async def move_session_to_trash(target_path: str):
    if not os.path.exists(target_path):
        return
        
    settings = await settings_store.read()
    max_papelera = settings.get("max_papelera_items", 10)
    
    basename = os.path.basename(target_path.rstrip("/\\"))
    papelera_path = os.path.join(PAPELERA_DIR, basename)
    
    if os.path.exists(papelera_path):
        papelera_path = os.path.join(PAPELERA_DIR, f"{uuid.uuid4().hex[:8]}_{basename}")
        
    shutil.move(target_path, papelera_path)
    
    papelera_items = []
    for f in os.listdir(PAPELERA_DIR):
        item_path = os.path.join(PAPELERA_DIR, f)
        papelera_items.append((item_path, os.path.getmtime(item_path)))
        
    papelera_items.sort(key=lambda x: x[1], reverse=True)
    
    for old_item, _ in papelera_items[max_papelera:]:
        try:
            if os.path.isdir(old_item):
                shutil.rmtree(old_item)
            else:
                os.remove(old_item)
        except Exception:
            pass

async def worker_loop():
    while True:
        try:
            tareas = await cola_store.read()
            pending = next((t for t in tareas if t.get("estado") == "pending"), None)

            if not pending:
                await asyncio.sleep(5)
                continue

            task_id = pending["id"]

            def set_processing(ts):
                for t in ts:
                    if t.get("id") == task_id and t.get("estado") == "pending":
                        t["estado"] = "processing"
                        return ts
                return None

            updated = await cola_store.update(set_processing)
            if not updated:
                continue

            # --- Resolve paths from session_dir (new) or legacy filename (old) ---
            session_dir = pending.get("session_dir")  # e.g. "audios/session_20260727_1530"
            if session_dir:
                audio_filename = pending["filename"]
                filepath = os.path.join(session_dir, audio_filename)
                image_filenames = pending.get("image_filenames", [])
                image_paths = [os.path.join(session_dir, img) for img in image_filenames
                               if os.path.exists(os.path.join(session_dir, img))]
            else:
                # Legacy: single flat file in audios/
                filepath = os.path.join(AUDIOS_DIR, pending["filename"])
                image_paths = []
                session_dir = None

            if not os.path.exists(filepath):
                def set_missing(ts):
                    for t in ts:
                        if t["id"] == task_id:
                            t["estado"] = "failed"
                            t["error_msg"] = "Archivo de audio no encontrado"
                    return ts
                await cola_store.update(set_missing)
                continue

            # Configurar prompt
            prompt_usar = ""
            materia_name = "Semestre actual"
            if pending["materia_id"] and pending["materia_id"] != "default":
                materias = await materias_store.read()
                m = next((m for m in materias if m["id"] == pending["materia_id"]), None)
                if m:
                    prompt_usar = m["prompt_personalizado"]
                    materia_name = m["nombre"]

            try:
                # LLM Call — now passes image_paths for multi-modal
                upload_path = audio_service.remove_silences(filepath)
                try:
                    texto, stats = await llm_service.generate_summary_from_audio(
                        upload_path,
                        prompt_usar,
                        pending.get("modelo_elegido", "gemini-3.1-flash-lite"),
                        image_paths=image_paths,
                        materia_name=materia_name,
                    )
                finally:
                    audio_service.cleanup_temp(upload_path, filepath)

                # Guardado automático (Pipeline Completo)
                json_data, texto_limpio = llm_service.extract_json_block(texto)
                fecha_str = datetime.now().strftime("%Y-%m-%d")

                md_filename = pending["filename"].replace(".webm", ".md")
                if md_filename.startswith("meet_"):
                    mat_id = pending["materia_id"] if (pending["materia_id"] and pending["materia_id"] != "default") else "default"
                    md_filename = md_filename.replace("meet_", f"resumen__{mat_id}__meet_")

                suggested_filename = json_data.get("filename", md_filename).replace("/", "-")
                if not suggested_filename.endswith(".md"):
                    suggested_filename += ".md"
                suggested_folder = json_data.get("folder", "")

                tags_match = re.search(r"tags:\s*\[(.*?)\]", texto_limpio, re.IGNORECASE)
                if not tags_match:
                    tags_match = re.search(r"tags:\s*(.*)", texto_limpio, re.IGNORECASE)
                tags = [t.strip().strip('"').strip("'") for t in tags_match.group(1).split(",")] if tags_match else []

                await export_service.save_markdown_and_metadata(
                    md_filename, suggested_filename, suggested_folder, texto_limpio, tags, fecha_str
                )

                # Pass image_paths so export can copy them to Obsidian Adjuntos/
                await export_service.save_to_obsidian(
                    suggested_filename, suggested_folder, texto_limpio,
                    image_paths=image_paths,
                )

                if json_data.get("tarjetas_informativas"):
                    mat_id = pending["materia_id"] if (pending["materia_id"] and pending["materia_id"] != "default") else "default"
                    await export_service.save_tarjetas_informativas(
                        json_data["tarjetas_informativas"], md_filename, mat_id, fecha_str
                    )

                if json_data.get("nuevas_reglas_profesor"):
                    await export_service.save_teacher_rules(
                        json_data["nuevas_reglas_profesor"], pending["materia_id"], fecha_str
                    )

                # Marcar completado
                def set_completed(ts):
                    for t in ts:
                        if t["id"] == task_id:
                            t["estado"] = "completed"
                            t["error_msg"] = ""
                    return ts
                await cola_store.update(set_completed)

                # --- Cleanup session directory (move entire session to papelera) ---
                if session_dir and os.path.isdir(session_dir):
                    await move_session_to_trash(session_dir)
                else:
                    # Legacy: move single audio file
                    await move_session_to_trash(filepath)

            except Exception as e:
                def set_failed(ts):
                    for t in ts:
                        if t["id"] == task_id:
                            t["estado"] = "failed"
                            t["error_msg"] = str(e)
                            t["intentos"] = t.get("intentos", 0) + 1
                    return ts
                await cola_store.update(set_failed)

        except Exception as e:
            print(f"Worker queue error: {e}")
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    def revert_processing(ts):
        for t in ts:
            if t.get("estado") == "processing":
                t["estado"] = "pending"
        return ts
    await cola_store.update(revert_processing)
    
    worker_task = asyncio.create_task(worker_loop())
    yield
    worker_task.cancel()

app = FastAPI(lifespan=lifespan)


def _public_session_dir(path: str | None) -> str | None:
    if not path:
        return None
    return path.replace(os.sep, "/")


def _session_name_from_dir(session_dir: str | None) -> str | None:
    if not session_dir:
        return None
    return os.path.basename(session_dir.rstrip("/\\"))


def _media_url(*parts: str) -> str:
    clean_parts = [part.strip("/\\") for part in parts if part]
    return "/media/" + "/".join(clean_parts)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir archivos de audio via /media
app.mount("/media", StaticFiles(directory=AUDIOS_DIR), name="media")


# ===========================================================================
# Pydantic Models
# ===========================================================================

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
    session_name: str = None
    session_dir: str = None
    image_filenames: list = []


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

class TutorChatRequest(BaseModel):
    materia_id: str
    historial: list = []
    pregunta: str
    modelo_elegido: str = "gemini-3.1-flash-lite"


class SummaryUpdate(BaseModel):
    content: str


class SettingsUpdate(BaseModel):
    obsidian_vault_path: str
    extension_backup_dir: str = "Backups_Clases/"
    max_audio_upload_mb: int = 500
    max_papelera_items: int = 10
    default_model: str = "gemini-3.1-flash-lite"
    rag_max_docs: int = 8


class TaskExtractRequest(BaseModel):
    image_base64: str
    modelo_elegido: str = None


# ===========================================================================
# Stats
# ===========================================================================

@app.get("/api/stats")
async def get_stats_endpoint():
    return await llm_service.get_or_reset_stats()


# ===========================================================================
# Materias
# ===========================================================================

@app.get("/api/materias")
async def get_materias():
    return await materias_store.read()


@app.post("/api/materias")
async def create_materia(materia: MateriaCreate):
    new_materia = {
        "id": str(uuid.uuid4()),
        "nombre": materia.nombre,
        "prompt_personalizado": materia.prompt_personalizado,
    }

    async def _append(materias: list) -> list:
        materias.append(new_materia)
        return materias

    await materias_store.update(_append)
    return new_materia


@app.put("/api/materias/{materia_id}")
async def update_materia(materia_id: str, materia: MateriaUpdate):
    found = {}

    async def _update(materias: list) -> list:
        for m in materias:
            if m["id"] == materia_id:
                m["nombre"] = materia.nombre
                m["prompt_personalizado"] = materia.prompt_personalizado
                found.update(m)
        return materias

    await materias_store.update(_update)
    if not found:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return found


@app.delete("/api/materias/{materia_id}")
async def delete_materia(materia_id: str):
    original_len = [0]

    async def _delete(materias: list) -> list:
        original_len[0] = len(materias)
        return [m for m in materias if m["id"] != materia_id]

    result = await materias_store.update(_delete)
    if len(result) == original_len[0]:
        raise HTTPException(status_code=404, detail="Materia no encontrada")
    return {"message": "Materia eliminada"}


# ===========================================================================
# Settings
# ===========================================================================

@app.get("/api/settings")
async def get_settings_endpoint():
    return await settings_store.read()


@app.put("/api/settings")
async def update_settings_endpoint(req: SettingsUpdate):
    async def _update(settings: dict) -> dict:
        settings["obsidian_vault_path"] = req.obsidian_vault_path
        settings["extension_backup_dir"] = req.extension_backup_dir
        settings["max_audio_upload_mb"] = req.max_audio_upload_mb
        settings["max_papelera_items"] = req.max_papelera_items
        settings["default_model"] = req.default_model
        settings["rag_max_docs"] = req.rag_max_docs
        return settings

    await settings_store.update(_update)
    return {"message": "Configuración actualizada"}


# ===========================================================================
# Models
# ===========================================================================

@app.get("/api/models")
async def get_available_models():
    models = await llm_service.list_available_models()
    return {"models": models}


# ===========================================================================
# Audios pendientes
# ===========================================================================

@app.get("/api/audios")
async def list_pending_audios():
    if not os.path.exists(AUDIOS_DIR):
        return {"audios": []}

    audios = []

    for entry in os.scandir(AUDIOS_DIR):
        # --- New: session subdirectory ---
        if entry.is_dir() and entry.name.startswith("session_"):
            audio_files = [f for f in os.listdir(entry.path) if f.endswith((".webm", ".m4a", ".opus", ".mp3", ".ogg"))]
            img_files = [f for f in os.listdir(entry.path)
                         if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
            for f in audio_files:
                name_parts = os.path.splitext(f)[0].split("_")
                if len(name_parts) >= 4:
                    date_str = name_parts[2]
                    if len(date_str) == 8:
                        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                    custom_name = " ".join(name_parts[4:]) if len(name_parts) >= 5 else ""
                    display_name = f"{custom_name} ({date_str})" if custom_name else f"Grabación {date_str}"
                else:
                    display_name = f
                audios.append({
                    "filename": f,
                    "display_name": display_name,
                    "session_name": entry.name,
                    "session_dir": _public_session_dir(os.path.join(AUDIOS_DIR, entry.name)),
                    "audio_url": _media_url(entry.name, f),
                    "image_urls": [_media_url(entry.name, img) for img in img_files],
                    "image_count": len(img_files),
                    "image_filenames": img_files,
                })

        # --- Legacy: flat .webm directly in audios/ ---
        elif entry.is_file() and entry.name.endswith(".webm"):
            f = entry.name
            name_parts = f.replace(".webm", "").split("_")
            if len(name_parts) >= 4:
                date_str = name_parts[2]
                if len(date_str) == 8:
                    date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                custom_name = " ".join(name_parts[4:]) if len(name_parts) >= 5 else ""
                display_name = f"{custom_name} ({date_str})" if custom_name else f"Grabación {date_str}"
            else:
                display_name = f
            audios.append({
                "filename": f,
                "display_name": display_name,
                "session_name": None,
                "session_dir": None,
                "audio_url": _media_url(f),
                "image_urls": [],
                "image_count": 0,
                "image_filenames": [],
            })

    audios.sort(key=lambda x: x["filename"], reverse=True)
    return {"audios": audios}


@app.delete("/api/audios/{path:path}")
async def delete_audio_or_image(path: str):
    target_path = os.path.join(AUDIOS_DIR, path)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Archivo o carpeta no encontrado")
    
    if os.path.isdir(target_path):
        await move_session_to_trash(target_path)
        return {"message": "Sesión movida a la papelera exitosamente"}
    
    if os.path.isfile(target_path):
        # Prevent deleting the last image
        if target_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            session_dir = os.path.dirname(target_path)
            img_files = [f for f in os.listdir(session_dir) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
            if len(img_files) <= 1:
                raise HTTPException(status_code=400, detail="No puedes eliminar la única imagen de la sesión. Se requiere al menos una.")
        
        # If deleting the webm inside a session folder, delete the whole folder
        if target_path.lower().endswith(".webm"):
            session_dir = os.path.dirname(target_path)
            if session_dir != AUDIOS_DIR and os.path.basename(session_dir).startswith("session_"):
                await move_session_to_trash(session_dir)
                return {"message": "Sesión movida a la papelera exitosamente"}
                
        # If it's just a single image being deleted manually (not the last one), we can just remove it
        if target_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            os.remove(target_path)
            return {"message": "Imagen eliminada exitosamente"}
            
        # Legacy single webm
        await move_session_to_trash(target_path)
        return {"message": "Archivo movido a la papelera exitosamente"}


# ===========================================================================
# Upload  (validación de tamaño OOM-safe: header + chunks)
# ===========================================================================

CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB por chunk


@app.post("/upload")
async def upload_audio(
    request: Request,
    audio: UploadFile = File(...),
    custom_name: str = Form(None),
    imagenes: List[UploadFile] = File(default=[]),
):
    """
    Recibe el audio .webm + N capturas de pantalla opcionales.
    Guarda todo en un subdirectorio único: audios/session_YYYYMMDD_HHmm/
    Retorna el directorio de sesión y los nombres de archivos para que la cola
    pueda encontrarlos.
    """
    settings = await settings_store.read()
    max_mb = settings.get("max_audio_upload_mb", 500)
    max_bytes = max_mb * 1024 * 1024

    # --- Capa 1: Content-Length header (O(1), cero RAM) ---
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el límite de {max_mb} MB "
                   f"({int(content_length) // (1024 * 1024)} MB declarados en Content-Length).",
        )

    # --- Crear subdirectorio de sesión único ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if custom_name:
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "", custom_name.replace(" ", "_"))
        session_name = f"session_{timestamp}_{safe_name}"
        audio_filename = f"meet_{timestamp}_{safe_name}.webm"
    else:
        session_name = f"session_{timestamp}"
        audio_filename = f"meet_{timestamp}.webm"

    session_dir = os.path.join(AUDIOS_DIR, session_name)
    os.makedirs(session_dir, exist_ok=True)
    
    is_zip = audio.filename.lower().endswith(".zip")
    if is_zip:
        audio_filepath = os.path.join(session_dir, "temp.zip")
    else:
        audio_filepath = os.path.join(session_dir, audio_filename)

    # --- Capa 2: Streaming del audio (o zip) por chunks (OOM-safe) ---
    bytes_written = 0
    try:
        with open(audio_filepath, "wb") as f:
            while True:
                chunk = await audio.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    f.close()
                    shutil.rmtree(session_dir, ignore_errors=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"El archivo supera el límite de {max_mb} MB.",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Error al guardar el archivo principal: {e}")

    image_filenames = []

    if is_zip:
        # Extraer el ZIP
        try:
            with zipfile.ZipFile(audio_filepath, 'r') as zip_ref:
                zip_ref.extractall(session_dir)
            os.remove(audio_filepath) # Borramos el zip temporal
            
            # Buscar el archivo de audio y las imágenes
            extracted_files = os.listdir(session_dir)
            found_audio = False
            for f in extracted_files:
                f_lower = f.lower()
                if not found_audio and f_lower.endswith((".webm", ".m4a", ".mp3", ".ogg", ".wav", ".aac", ".mp4")):
                    # Renombrar al formato estándar
                    os.rename(os.path.join(session_dir, f), os.path.join(session_dir, audio_filename))
                    found_audio = True
                elif f_lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    # Normalizar nombre
                    safe_img_name = re.sub(r"[^a-zA-Z0-9_.\-]", "_", f)
                    if safe_img_name != f:
                        os.rename(os.path.join(session_dir, f), os.path.join(session_dir, safe_img_name))
                    image_filenames.append(safe_img_name)
                else:
                    # Otros archivos no deseados (ej. basura de macOS)
                    pass
            
            if not found_audio:
                shutil.rmtree(session_dir, ignore_errors=True)
                raise HTTPException(status_code=400, detail="No se encontró ningún archivo de audio válido dentro del ZIP.")
                
        except HTTPException:
            raise
        except Exception as e:
            shutil.rmtree(session_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Error al procesar el archivo ZIP: {e}")
            
    else:
        # --- Guardar imágenes adjuntas ---
        for img_file in (imagenes or []):
            if not img_file.filename:
                continue
            safe_img_name = re.sub(r"[^a-zA-Z0-9_.\-]", "_", img_file.filename)
            img_path = os.path.join(session_dir, safe_img_name)
            try:
                with open(img_path, "wb") as f:
                    while True:
                        chunk = await img_file.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                image_filenames.append(safe_img_name)
            except Exception as e:
                print(f"[Upload] Error guardando imagen {safe_img_name}: {e}")

    # --- REGLA ESTRICTA: el sistema EXIGE al menos 1 imagen ---
    if not image_filenames:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos una captura de pantalla junto al audio. "
                   "Usa Alt+S durante la clase para capturar momentos clave antes de enviar."
        )

    # --- REPARAR DURACIÓN DEL WEBM ---
    # MediaRecorder no escribe la duración ni los Cues (índice) en el archivo WebM.
    # FFmpeg -c copy reescribe el contenedor de forma ultrarrápida, calculando la duración.
    audio_full_path = os.path.join(session_dir, audio_filename)
    if audio_filename.lower().endswith(".webm") and os.path.exists(audio_full_path):
        temp_audio = audio_full_path + ".temp.webm"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_full_path, "-c", "copy", temp_audio],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if os.path.exists(temp_audio):
                os.replace(temp_audio, audio_full_path)
        except Exception as e:
            print(f"[Upload] Advertencia: No se pudo reparar metadatos WebM: {e}")

    print(f"[Upload] Sesión {session_name}: audio={audio_filename}, imágenes={image_filenames}")

    return {
        "message": "Sesión subida correctamente",
        "filename": audio_filename,
        "session_name": session_name,
        "session_dir": _public_session_dir(session_dir),
        "audio_url": _media_url(session_name, audio_filename),
        "image_urls": [_media_url(session_name, img) for img in image_filenames],
        "image_filenames": image_filenames,
    }


# ===========================================================================
# AI Prompt Gen
# ===========================================================================

@app.post("/api/generate-prompt")
async def generate_prompt(req: PromptGenRequest):
    try:
        prompt_generado, stats = await llm_service.generate_prompt_for_materia(
            req.descripcion, req.modelo_elegido
        )
        return {"prompt_generado": prompt_generado, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Chat RAG
# ===========================================================================

@app.post("/api/chat")
async def chat_estudio(req: ChatRequest):
    try:
        respuesta, stats = await llm_service.chat_with_rag(
            req.mensaje, req.materia_id, req.modelo_elegido
        )
        return {"respuesta": respuesta, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tutor/chat")
async def tutor_chat(req: TutorChatRequest):
    try:
        respuesta, stats = await llm_service.tutor_chat_with_rag(
            historial_mensajes=req.historial,
            pregunta_actual=req.pregunta,
            materia_id=req.materia_id,
            modelo=req.modelo_elegido
        )
        return {"respuesta": respuesta, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Generate Task (Cola)
# ===========================================================================

@app.post("/api/generate", status_code=202)
async def queue_generate_task(req: GenerateRequest):
    """
    Encola una tarea de procesamiento. Soporta tanto el nuevo esquema de sesión
    (session_dir + image_filenames) como el legado (filename en audios/ plano).
    """
    # Intentar localizar el audio: primero en session_dir (nuevo), luego plano (legado)
    session_name = getattr(req, "session_name", None)
    session_dir = getattr(req, "session_dir", None)
    image_filenames = getattr(req, "image_filenames", []) or []

    if session_name and not session_dir:
        session_dir = os.path.join(AUDIOS_DIR, session_name)

    if session_dir:
        filepath = os.path.join(session_dir, req.filename)
    else:
        # Buscar en subdirectorios de sesión si no se encontró directamente
        flat_path = os.path.join(AUDIOS_DIR, req.filename)
        if os.path.exists(flat_path):
            filepath = flat_path
        else:
            # Buscar en sesiones existentes
            filepath = None
            for entry in os.scandir(AUDIOS_DIR):
                if entry.is_dir():
                    candidate = os.path.join(entry.path, req.filename)
                    if os.path.exists(candidate):
                        filepath = candidate
                        session_dir = os.path.join(AUDIOS_DIR, entry.name)
                        session_name = entry.name
                        # Collect images in that session dir
                        image_filenames = [
                            f for f in os.listdir(session_dir)
                            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                        ]
                        break

    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo de audio no encontrado")

    task_id = str(uuid.uuid4())

    def enqueue(ts):
        if any(t["filename"] == req.filename and t["estado"] in ["pending", "processing"] for t in ts):
            return ts
        ts.append({
            "id": task_id,
            "filename": req.filename,
            "session_name": session_name,
            "session_dir": session_dir,
            "image_filenames": image_filenames,
            "materia_id": req.materia_id,
            "modelo_elegido": req.modelo_elegido,
            "estado": "pending",
            "intentos": 0,
            "error_msg": "",
            "fecha_creacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        return ts

    await cola_store.update(enqueue)
    return {"message": "Audio encolado", "task_id": task_id}

@app.get("/api/cola")
async def get_queue():
    tareas = await cola_store.read()
    return {"cola": tareas}

@app.post("/api/cola/{task_id}/retry")
async def retry_queue_task(task_id: str):
    def retry(ts):
        for t in ts:
            if t["id"] == task_id and t["estado"] == "failed":
                t["estado"] = "pending"
                t["error_msg"] = ""
        return ts
    await cola_store.update(retry)
    return {"message": "Reintentando tarea"}

@app.delete("/api/cola/{task_id}")
async def delete_queue_task(task_id: str):
    def remove(ts):
        return [t for t in ts if t["id"] != task_id]
    await cola_store.update(remove)
    return {"message": "Tarea eliminada"}

# ===========================================================================
# Summaries
# ===========================================================================

@app.get("/api/summaries")
async def list_summaries():
    if not os.path.exists(RESUMENES_DIR):
        return {"summaries": []}
    files = [f for f in os.listdir(RESUMENES_DIR) if f.endswith(".md")]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(RESUMENES_DIR, x)), reverse=True)

    summaries = []
    for f in files:
        clean_name = re.sub(r"__.*?__", "", f).replace("resumen_meet_", "").replace(".md", "")
        parts = clean_name.split("_")
        if len(parts) >= 2:
            date_str = parts[0]
            if len(date_str) == 8:
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            custom_name = " ".join(parts[2:]) if len(parts) >= 3 else ""
            display_name = f"{custom_name} ({date_str})" if custom_name else f"Reunión {date_str}"
        else:
            display_name = f

        try:
            mtime = os.path.getmtime(os.path.join(RESUMENES_DIR, f))
            created_at = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M")
        except Exception:
            created_at = "Fecha desconocida"

        summaries.append({"filename": f, "display_name": display_name, "created_at": created_at})
    return {"summaries": summaries}


@app.get("/api/summaries/{filename}")
async def get_summary(filename: str):
    if not filename.endswith(".md"):
        return {"error": "Solo se permiten archivos markdown."}
    filepath = os.path.join(RESUMENES_DIR, filename)
    if not os.path.exists(filepath):
        return {"error": "Archivo no encontrado."}
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return {"content": content}


@app.put("/api/summaries/{filename}")
async def update_summary(filename: str, req: SummaryUpdate):
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Solo archivos markdown.")
    filepath = os.path.join(RESUMENES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(req.content)

    async def _update_meta(meta_data: dict) -> dict:
        if filename in meta_data:
            meta_data[filename]["resumen"] = req.content
        return meta_data

    await meta_store.update(_update_meta)
    return {"message": "Resumen actualizado"}


@app.delete("/api/summaries/{filename}")
async def delete_summary(filename: str):
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Solo archivos markdown.")
    filepath = os.path.join(RESUMENES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    os.remove(filepath)

    async def _remove_meta(meta_data: dict) -> dict:
        meta_data.pop(filename, None)
        return meta_data

    await meta_store.update(_remove_meta)
    return {"message": "Resumen eliminado"}


# ===========================================================================
# Exportaciones
# ===========================================================================

@app.get("/api/exportaciones/{filename}")
async def download_export(filename: str):
    filepath = os.path.join(EXPORTACIONES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(filepath, filename=filename)


# ===========================================================================
# Info
# ===========================================================================

@app.get("/info")
async def get_system_info():
    return {"status": "ok", "message": "Asistente de Clases v3 - Modular Architecture"}


# ===========================================================================
# Tarjetas Informativas
# ===========================================================================

@app.get("/api/tarjetas")
async def get_tarjetas(materia_id: str = None):
    tarjetas = await tarjetas_store.read()
    if materia_id and materia_id != "default" and materia_id != "todas":
        tarjetas = [t for t in tarjetas if t.get("materia_id") == materia_id]
    return {"tarjetas": tarjetas}


@app.put("/api/tarjetas/{tarjeta_id}")
async def update_tarjeta(tarjeta_id: str, payload: dict):
    if "nota_personal" not in payload:
        raise HTTPException(status_code=400, detail="Solo se permite actualizar nota_personal")
        
    async def _update(tarjetas: list) -> list:
        for t in tarjetas:
            if t.get("id") == tarjeta_id:
                t["nota_personal"] = payload["nota_personal"]
        return tarjetas

    await tarjetas_store.update(_update)
    return {"message": "ok"}


@app.delete("/api/tarjetas/{tarjeta_id}")
async def delete_tarjeta(tarjeta_id: str):
    async def _delete(tarjetas: list) -> list:
        return [t for t in tarjetas if t.get("id") != tarjeta_id]

    await tarjetas_store.update(_delete)
    return {"message": "ok"}



# ===========================================================================
# Extract task from image
# ===========================================================================

@app.post("/api/extract-task")
async def extract_task_from_image(req: TaskExtractRequest):
    b64_str = req.image_base64.split(",")[1] if "," in req.image_base64 else req.image_base64
    img_bytes = base64.b64decode(b64_str)

    modelo = req.modelo_elegido
    if not modelo:
        settings = await settings_store.read()
        modelo = settings.get("default_model", "gemini-3.1-flash-lite")

    try:
        texto, _ = await llm_service.extract_task_from_image(img_bytes, modelo)
        json_data, texto_limpio = llm_service.extract_json_block(texto)

        suggested_filename = json_data.get(
            "filename",
            f"Tarea_Captura_{datetime.now().strftime('%Y%m%d%H%M%S')}.md",
        ).replace("/", "-")
        if not suggested_filename.endswith(".md"):
            suggested_filename += ".md"
        suggested_folder = json_data.get("folder", "01 Proyectos/Tareas")

        # Guardar .md local
        with open(os.path.join(RESUMENES_DIR, suggested_filename), "w", encoding="utf-8") as f:
            f.write(texto_limpio)

        # Guardar en Obsidian
        await export_service.save_to_obsidian(suggested_filename, suggested_folder, texto_limpio)

        # Guardar tarjetas informativas
        if json_data.get("tarjetas_informativas"):
            fecha_str = datetime.now().strftime("%Y-%m-%d")
            await export_service.save_tarjetas_informativas(
                json_data["tarjetas_informativas"], "Captura_Pantalla", "default", fecha_str
            )

        return {"message": "Tarea extraída y guardada correctamente", "filename": suggested_filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Frontend estático (SIEMPRE al final)
# ===========================================================================
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
