"""
server.py — Punto de entrada de FastAPI.
Responsabilidad única: definir rutas y delegar a los servicios.
"""
import base64
import os
import re
import shutil
import uuid
from datetime import datetime

import asyncio
from contextlib import asynccontextmanager
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
                
            filepath = os.path.join(AUDIOS_DIR, pending["filename"])
            if not os.path.exists(filepath):
                def set_missing(ts):
                    for t in ts:
                        if t["id"] == task_id:
                            t["estado"] = "failed"
                            t["error_msg"] = "Archivo no encontrado"
                    return ts
                await cola_store.update(set_missing)
                continue
                
            # Configurar prompt
            prompt_usar = ""
            if pending["materia_id"] and pending["materia_id"] != "default":
                materias = await materias_store.read()
                m = next((m for m in materias if m["id"] == pending["materia_id"]), None)
                if m:
                    prompt_usar = m["prompt_personalizado"]
                    
            try:
                # LLM Call
                upload_path = audio_service.remove_silences(filepath)
                try:
                    texto, stats = await llm_service.generate_summary_from_audio(
                        upload_path, prompt_usar, pending.get("modelo_elegido", "gemini-3.1-flash-lite")
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
                
                await export_service.save_to_obsidian(suggested_filename, suggested_folder, texto_limpio)
                
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
                
                # Papelera
                papelera_path = os.path.join(PAPELERA_DIR, os.path.basename(filepath))
                shutil.move(filepath, papelera_path)
                
                # Cleanup papelera (10 files)
                papelera_files = sorted(
                    [os.path.join(PAPELERA_DIR, f) for f in os.listdir(PAPELERA_DIR)
                     if os.path.isfile(os.path.join(PAPELERA_DIR, f))],
                    key=os.path.getmtime,
                    reverse=True,
                )
                for old_file in papelera_files[10:]:
                    try:
                        os.remove(old_file)
                    except Exception:
                        pass
                
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
    browser_cookie_source: str = "brave"
    max_audio_upload_mb: int = 500
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
        settings["browser_cookie_source"] = req.browser_cookie_source
        settings["max_audio_upload_mb"] = req.max_audio_upload_mb
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
    files = [f for f in os.listdir(AUDIOS_DIR) if f.endswith(".webm")]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(AUDIOS_DIR, x)), reverse=True)

    audios = []
    for f in files:
        name_parts = f.replace(".webm", "").split("_")
        if len(name_parts) >= 4:
            date_str = name_parts[2]
            if len(date_str) == 8:
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            custom_name = " ".join(name_parts[4:]) if len(name_parts) >= 5 else ""
            display_name = f"{custom_name} ({date_str})" if custom_name else f"Grabación {date_str}"
        else:
            display_name = f
        audios.append({"filename": f, "display_name": display_name})
    return {"audios": audios}


@app.delete("/api/audios/{filename}")
async def delete_audio(filename: str):
    if not filename.endswith(".webm"):
        raise HTTPException(status_code=400, detail="Formato inválido")
    filepath = os.path.join(AUDIOS_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return {"message": "Audio eliminado exitosamente"}
    raise HTTPException(status_code=404, detail="Archivo de audio no encontrado")


# ===========================================================================
# Upload  (validación de tamaño OOM-safe: header + chunks)
# ===========================================================================

CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB por chunk


@app.post("/upload")
async def upload_audio(
    request: Request,
    audio: UploadFile = File(...),
    custom_name: str = Form(None),
):
    settings = await settings_store.read()
    max_mb = settings.get("max_audio_upload_mb", 500)
    max_bytes = max_mb * 1024 * 1024

    # --- Capa 1: Content-Length header (O(1), cero RAM) ---
    # Rechaza antes de leer un solo byte si el cliente declara un tamaño excesivo.
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera el límite de {max_mb} MB "
                   f"({int(content_length) // (1024 * 1024)} MB declarados en Content-Length).",
        )

    # Construir nombre de destino
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(audio.filename)[1] if audio.filename else ".webm"
    if not ext:
        ext = ".webm"

    if custom_name:
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "", custom_name.replace(" ", "_"))
        filename = f"meet_{timestamp}_{safe_name}{ext}"
    else:
        filename = f"meet_{timestamp}{ext}"

    filepath = os.path.join(AUDIOS_DIR, filename)

    # --- Capa 2: streaming por chunks (cubre transferencias chunked sin Content-Length) ---
    # Lee 1 MB a la vez → escribe en disco → aborta temprano si la suma supera el límite.
    # La RAM máxima usada en cualquier momento es exactamente 1 MB (un chunk).
    bytes_written = 0
    try:
        with open(filepath, "wb") as f:
            while True:
                chunk = await audio.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    # Abortar: borrar el archivo parcial antes de responder
                    f.close()
                    os.remove(filepath)
                    raise HTTPException(
                        status_code=413,
                        detail=f"El archivo supera el límite de {max_mb} MB "
                               f"(se interrumpió al superar {max_bytes // (1024 * 1024)} MB).",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        # Limpiar archivo parcial ante cualquier otro error de IO
        if os.path.exists(filepath):
            os.remove(filepath)
        raise HTTPException(status_code=500, detail=f"Error al guardar el audio: {e}")

    return {"message": "Audio subido correctamente", "filename": filename, "path": filepath}


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


# ===========================================================================
# Generate Task (Cola)
# ===========================================================================

@app.post("/api/generate", status_code=202)
async def queue_generate_task(req: GenerateRequest):
    filepath = os.path.join(AUDIOS_DIR, req.filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo de audio no encontrado")
        
    task_id = str(uuid.uuid4())
    
    def enqueue(ts):
        if any(t["filename"] == req.filename and t["estado"] in ["pending", "processing"] for t in ts):
            return ts
        ts.append({
            "id": task_id,
            "filename": req.filename,
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
