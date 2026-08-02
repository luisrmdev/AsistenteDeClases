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
from datetime import datetime, timedelta

import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional
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
    progreso_store,
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
                # Blindaje Multi-OS: Asegura que las barras coincidan con el sistema operativo actual (Linux/Windows)
                session_dir = os.path.normpath(session_dir)
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
            temperatura = 0.3
            if pending["materia_id"] and pending["materia_id"] != "default":
                materias = await materias_store.read()
                m = next((m for m in materias if m["id"] == pending["materia_id"]), None)
                if m:
                    prompt_usar = m["prompt_personalizado"]
                    materia_name = m["nombre"]
                    temperatura = m.get("temperatura", 0.3)

            try:
                settings = await settings_store.read()
                silence_db = settings.get("audio_silence_db", -30)
                
                # --- Preparar Imágenes (Renombrar y Subir a GridFS) ---
                from database import get_fs
                fs = get_fs()
                renamed_image_paths = []
                image_name_map = {}
                if image_paths and session_dir:
                    session_id = os.path.basename(session_dir.rstrip("/\\")).replace("session_", "")
                    for img in image_paths:
                        basename = os.path.basename(img)
                        if not basename.startswith(session_id):
                            new_basename = f"{session_id}_{basename}"
                            new_path = os.path.join(session_dir, new_basename)
                            os.rename(img, new_path)
                            image_name_map[basename] = new_basename
                        else:
                            new_path = img
                            new_basename = basename
                            image_name_map[basename] = new_basename
                            
                        # Subir a GridFS permanentemente
                        with open(new_path, "rb") as f_img:
                            grid_in = fs.open_upload_stream(new_basename)
                            await grid_in.write(f_img.read())
                            await grid_in.close()
                            
                        renamed_image_paths.append(new_path)
                    image_paths = renamed_image_paths

                # LLM Call — now passes image_paths for multi-modal
                upload_path = audio_service.remove_silences(filepath, silence_threshold_db=silence_db)
                try:
                    texto = await llm_service.generate_summary_from_audio(
                        upload_path,
                        prompt_usar,
                        pending.get("modelo_elegido", "gemini-3.1-flash-lite"),
                        image_paths=image_paths,
                        materia_name=materia_name,
                        temperatura=temperatura
                    )
                finally:
                    audio_service.cleanup_temp(upload_path, filepath)

                # Guardado automático (Pipeline Completo)
                json_data, texto_limpio = llm_service.extract_json_block(texto)
                
                # --- TWO-PASS EXTRACTION (FALLBACK) ---
                # Si el modelo olvidó las tarjetas o el temario, forzamos una segunda pasada rápida
                needs_fallback = False
                if not json_data:
                    needs_fallback = True
                elif not json_data.get("tarjetas_informativas") and not json_data.get("temario_atomico"):
                    needs_fallback = True
                    
                if needs_fallback:
                    print("[Worker] JSON incompleto o ausente. Ejecutando fallback (Two-Pass Extraction)...")
                    fallback_data = await llm_service.force_extract_metadata_from_markdown(
                        texto_limpio, 
                        modelo=pending.get("modelo_elegido", "gemini-3.1-flash-lite")
                    )
                    
                    if not json_data:
                        json_data = fallback_data
                    else:
                        if "tarjetas_informativas" in fallback_data:
                            json_data["tarjetas_informativas"] = fallback_data["tarjetas_informativas"]
                        if "nuevas_reglas_profesor" in fallback_data:
                            json_data["nuevas_reglas_profesor"] = fallback_data["nuevas_reglas_profesor"]
                        if "temario_atomico" in fallback_data:
                            json_data["temario_atomico"] = fallback_data["temario_atomico"]
                # --------------------------------------
                
                # Reemplazar los nombres cortos de imagen por los nombres reales con prefijo de sesión
                for short_name, long_name in image_name_map.items():
                    texto_limpio = texto_limpio.replace(f"![[{short_name}]]", f"![[{long_name}]]")

                fecha_str = datetime.now().strftime("%Y-%m-%d")

                md_filename = pending["filename"].replace(".webm", ".md")
                mat_id = pending["materia_id"] if (pending.get("materia_id") and pending["materia_id"] != "default") else "default"
                
                if md_filename.startswith("meet_"):
                    md_filename = md_filename.replace("meet_", f"resumen__{mat_id}__meet_")

                suggested_filename = json_data.get("filename", md_filename).replace("/", "-")
                if not suggested_filename.endswith(".md"):
                    suggested_filename += ".md"
                    
                if f"__{mat_id}__" not in suggested_filename:
                    suggested_filename = f"resumen__{mat_id}__{suggested_filename}"

                suggested_folder = json_data.get("folder", "")

                tags_match = re.search(r"tags:\s*\[(.*?)\]", texto_limpio, re.IGNORECASE)
                if not tags_match:
                    tags_match = re.search(r"tags:\s*(.*)", texto_limpio, re.IGNORECASE)
                tags = [t.strip().strip('"').strip("'") for t in tags_match.group(1).split(",")] if tags_match else []

                # === TRANSACCIÓN ATÓMICA (ALL-OR-NOTHING) ===
                try:
                    await export_service.save_markdown_and_metadata(
                        md_filename, 
                        suggested_filename, 
                        suggested_folder, 
                        texto_limpio, 
                        tags, 
                        fecha_str, 
                        pending.get("slot_id"),
                        temario_atomico=json_data.get("temario_atomico")
                    )

                    # Pass image_paths so export can copy them to Obsidian Adjuntos/
                    await export_service.save_to_obsidian(
                        suggested_filename, suggested_folder, texto_limpio,
                        image_paths=image_paths,
                    )

                    if json_data.get("tarjetas_informativas"):
                        mat_id = pending["materia_id"] if (pending["materia_id"] and pending["materia_id"] != "default") else "default"
                        await export_service.save_tarjetas_informativas(
                            json_data["tarjetas_informativas"], md_filename, mat_id, fecha_str, pending.get("slot_id")
                        )

                    if json_data.get("nuevas_reglas_profesor"):
                        await export_service.save_teacher_rules(
                            json_data["nuevas_reglas_profesor"], pending["materia_id"], fecha_str
                        )
                except Exception as commit_err:
                    print(f"[Worker] Error crítico durante el guardado (Commit Phase). Iniciando Rollback: {commit_err}")
                    await export_service.rollback_export(md_filename, suggested_filename, pending.get("slot_id"))
                    raise commit_err  # Re-lanzar para que el sistema marque la tarea como FAILED
                # ============================================

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
                error_msg = str(e)
                def set_failed(ts):
                    for t in ts:
                        if t["id"] == task_id:
                            t["estado"] = "failed"
                            t["error_msg"] = error_msg
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


from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends
from services.auth_service import verify_password, create_access_token, decode_token

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Only protect /api/ routes, excluding /api/login and /info
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/login"):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "No autenticado"})
        token = auth_header.split(" ")[1]
        try:
            decode_token(token)
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Token inválido o expirado"})
            
    response = await call_next(request)
    return response

@app.post("/api/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    from database import usuarios_store
    users = await usuarios_store.read()
    
    user = next((u for u in users if u["username"] == form_data.username), None)
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=400,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    access_token = create_access_token(data={"sub": user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir archivos de audio y adjuntos
app.mount("/media", StaticFiles(directory=AUDIOS_DIR), name="media")
from database import get_fs
from fastapi.responses import StreamingResponse
import mimetypes

@app.get("/adjuntos/{filename}")
async def get_adjunto(filename: str):
    try:
        fs = get_fs()
        grid_out = await fs.open_download_stream_by_name(filename)
        mime_type, _ = mimetypes.guess_type(filename)
        
        async def iterfile():
            while True:
                chunk = await grid_out.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
                
        return StreamingResponse(iterfile(), media_type=mime_type or "application/octet-stream")
    except Exception:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")

# ===========================================================================
# Pydantic Models
# ===========================================================================

class MateriaCreate(BaseModel):
    nombre: str
    prompt_personalizado: str
    temperatura: float = 0.3
    dias_imparticion: list[str] = []


class MateriaUpdate(BaseModel):
    nombre: str
    prompt_personalizado: str
    temperatura: float = 0.3
    dias_imparticion: list[str] = []


class GenerateRequest(BaseModel):
    filename: str
    materia_id: str
    modelo_elegido: str = "gemini-3.1-flash-lite"
    session_name: str = None
    session_dir: str = None
    image_filenames: list = []
    slot_id: str = None


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
    image_data: Optional[str] = None

class TutorChatRequest(BaseModel):
    materia_id: str
    historial: list = []
    pregunta: str
    modelo_elegido: str = "gemini-3.1-flash-lite"
    image_data: Optional[str] = None

class TutorV2ChatRequest(BaseModel):
    slot_id: str
    historial: list = []
    pregunta: str
    modelo_elegido: str = "gemini-3.1-flash-lite"
    image_data: Optional[str] = None


class SummaryUpdate(BaseModel):
    content: str


class SettingsUpdate(BaseModel):
    obsidian_vault_path: str
    max_audio_upload_mb: int = 500
    max_papelera_items: int = 10
    default_model: str = "gemini-3.1-flash-lite"
    rag_max_docs: int = 8
    nlp_threshold: float = 1.0
    audio_silence_db: int = -30
    
    # Prompts Core
    prompt_maestro_resumenes: str | None = None
    prompt_chat_rag: str | None = None
    prompt_tutor_socratico: str | None = None
    prompt_generator_sys: str | None = None
    prompt_tarea_extractor: str | None = None


class TaskExtractRequest(BaseModel):
    image_base64: str
    modelo_elegido: str = None


class ProgresoSlotCreate(BaseModel):
    fecha: str
    dia: str
    materia_id: str


class ProgresoSlotUpdate(BaseModel):
    estado: str
    md_vinculado: str = None


class ProgresoGenerarSemanaRequest(BaseModel):
    fecha_base: str  # YYYY-MM-DD to base the week on


# ===========================================================================
# Stats
# ===========================================================================


# ===========================================================================
# Materias
# ===========================================================================

@app.get("/api/materias")
async def get_materias():
    materias = await materias_store.read()
    if not os.path.exists(RESUMENES_DIR):
        for m in materias:
            m["doc_count"] = 0
        return materias
        
    files = [f for f in os.listdir(RESUMENES_DIR) if f.endswith(".md")]
    for m in materias:
        m["doc_count"] = sum(1 for f in files if f"__{m['id']}__" in f)
        
    return materias


@app.post("/api/materias")
async def create_materia(materia: MateriaCreate):
    new_materia = {
        "id": str(uuid.uuid4()),
        "nombre": materia.nombre,
        "prompt_personalizado": materia.prompt_personalizado,
        "temperatura": materia.temperatura,
        "dias_imparticion": materia.dias_imparticion,
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
                m["temperatura"] = materia.temperatura
                m["dias_imparticion"] = materia.dias_imparticion
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
# Progreso (Tracker Semestral)
# ===========================================================================

@app.get("/api/progreso")
async def get_progreso():
    slots = await progreso_store.read()
    return {"slots": slots}


@app.post("/api/progreso/manual")
async def create_progreso_slot(slot: ProgresoSlotCreate):
    new_slot = {
        "id": f"slot_{uuid.uuid4().hex[:8]}",
        "fecha": slot.fecha,
        "dia": slot.dia,
        "materia_id": slot.materia_id,
        "estado": "AUSENTE",
        "md_vinculado": None
    }
    
    async def _append(slots: list) -> list:
        slots.append(new_slot)
        return slots
        
    await progreso_store.update(_append)
    return new_slot


@app.post("/api/progreso/generar_semana")
async def generar_semana(req: ProgresoGenerarSemanaRequest):
    materias = await materias_store.read()
    
    # Parse input date
    try:
        base_date = datetime.strptime(req.fecha_base, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Use YYYY-MM-DD")
        
    # Get Monday of that week
    monday = base_date - timedelta(days=base_date.weekday())
    
    # Generate days map
    days_map = {
        "Lunes": monday,
        "Martes": monday + timedelta(days=1),
        "Miércoles": monday + timedelta(days=2),
        "Miercoles": monday + timedelta(days=2),
        "Jueves": monday + timedelta(days=3),
        "Viernes": monday + timedelta(days=4),
        "Sábado": monday + timedelta(days=5),
        "Sabado": monday + timedelta(days=5),
        "Domingo": monday + timedelta(days=6)
    }
    
    new_slots = []
    
    async def _generate(slots: list) -> list:
        # Prevent duplicates
        existing_keys = {f"{s['materia_id']}_{s['fecha']}" for s in slots}
        
        for mat in materias:
            dias = mat.get("dias_imparticion", [])
            for d in dias:
                if d in days_map:
                    fecha_str = days_map[d].strftime("%Y-%m-%d")
                    key = f"{mat['id']}_{fecha_str}"
                    if key not in existing_keys:
                        slot = {
                            "id": f"slot_{uuid.uuid4().hex[:8]}",
                            "fecha": fecha_str,
                            "dia_semana": d,
                            "materia_id": mat["id"],
                            "estado": "AUSENTE",
                            "md_vinculado": None
                        }
                        new_slots.append(slot)
                        slots.append(slot)
                        existing_keys.add(key)
        return slots
        
    await progreso_store.update(_generate)
    return {"message": "Semana generada", "nuevos_slots": len(new_slots), "status": "success"}

@app.delete("/api/progreso/eliminar_semana")
async def eliminar_semana(fecha_base: str):
    try:
        base_date = datetime.strptime(fecha_base, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Use YYYY-MM-DD")
        
    monday = base_date - timedelta(days=base_date.weekday())
    
    week_dates = set()
    for i in range(7):
        week_dates.add((monday + timedelta(days=i)).strftime("%Y-%m-%d"))
        
    deleted_count = 0
    async def _delete(slots: list) -> list:
        nonlocal deleted_count
        new_slots = []
        for s in slots:
            if s["fecha"] in week_dates:
                deleted_count += 1
            else:
                new_slots.append(s)
        return new_slots
        
    await progreso_store.update(_delete)
    return {"message": "Semana eliminada", "slots_eliminados": deleted_count, "status": "success"}


@app.put("/api/progreso/{slot_id}")
async def update_progreso_slot(slot_id: str, update_data: ProgresoSlotUpdate):
    found = {}

    async def _update(slots: list) -> list:
        for s in slots:
            if s["id"] == slot_id:
                s["estado"] = update_data.estado
                if update_data.md_vinculado is not None:
                    s["md_vinculado"] = update_data.md_vinculado
                found.update(s)
        return slots

    await progreso_store.update(_update)
    if not found:
        raise HTTPException(status_code=404, detail="Slot no encontrado")
    return found


@app.delete("/api/progreso/{slot_id}")
async def delete_progreso_slot(slot_id: str):
    original_len = [0]

    async def _delete(slots: list) -> list:
        original_len[0] = len(slots)
        return [s for s in slots if s["id"] != slot_id]

    result = await progreso_store.update(_delete)
    if len(result) == original_len[0]:
        raise HTTPException(status_code=404, detail="Slot no encontrado")
    return {"message": "Slot eliminado"}


# ===========================================================================
# Settings
# ===========================================================================

@app.get("/api/settings")
async def get_settings_endpoint():
    settings = await settings_store.read()
    
    from services.llm_service import (
        DEFAULT_PROMPT_MAESTRO,
        DEFAULT_PROMPT_CHAT,
        DEFAULT_PROMPT_TUTOR,
        DEFAULT_PROMPT_GENERATOR,
        DEFAULT_PROMPT_EXTRACTOR
    )
    
    # Inyectar los valores por defecto si no existen en el json
    if not settings.get("prompt_maestro_resumenes"):
        settings["prompt_maestro_resumenes"] = DEFAULT_PROMPT_MAESTRO
    if not settings.get("prompt_chat_rag"):
        settings["prompt_chat_rag"] = DEFAULT_PROMPT_CHAT
    if not settings.get("prompt_tutor_socratico"):
        settings["prompt_tutor_socratico"] = DEFAULT_PROMPT_TUTOR
    if not settings.get("prompt_generator_sys"):
        settings["prompt_generator_sys"] = DEFAULT_PROMPT_GENERATOR
    if not settings.get("prompt_tarea_extractor"):
        settings["prompt_tarea_extractor"] = DEFAULT_PROMPT_EXTRACTOR
        
    return settings


@app.put("/api/settings")
async def update_settings_endpoint(req: SettingsUpdate):
    async def _update(settings: dict) -> dict:
        settings["obsidian_vault_path"] = req.obsidian_vault_path
        settings["max_audio_upload_mb"] = req.max_audio_upload_mb
        settings["max_papelera_items"] = req.max_papelera_items
        settings["default_model"] = req.default_model
        settings["rag_max_docs"] = req.rag_max_docs
        settings["nlp_threshold"] = req.nlp_threshold
        settings["audio_silence_db"] = req.audio_silence_db
        
        if req.prompt_maestro_resumenes is not None:
            settings["prompt_maestro_resumenes"] = req.prompt_maestro_resumenes
        if req.prompt_chat_rag is not None:
            settings["prompt_chat_rag"] = req.prompt_chat_rag
        if req.prompt_tutor_socratico is not None:
            settings["prompt_tutor_socratico"] = req.prompt_tutor_socratico
        if req.prompt_generator_sys is not None:
            settings["prompt_generator_sys"] = req.prompt_generator_sys
        if req.prompt_tarea_extractor is not None:
            settings["prompt_tarea_extractor"] = req.prompt_tarea_extractor
            
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

    cola = await cola_store.read()
    archivos_en_cola = {t["filename"] for t in cola if t.get("estado") in ["pending", "processing", "failed"]}

    audios = []

    for entry in os.scandir(AUDIOS_DIR):
        # --- New: session subdirectory ---
        if entry.is_dir() and entry.name.startswith("session_"):
            audio_files = [f for f in os.listdir(entry.path) if f.endswith((".webm", ".m4a", ".opus", ".mp3", ".ogg"))]
            img_files = [f for f in os.listdir(entry.path)
                         if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
            for f in audio_files:
                if f in archivos_en_cola:
                    continue
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
            if f in archivos_en_cola:
                continue
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


class MergeRequest(BaseModel):
    session1: str
    session2: str

@app.post("/api/audios/merge")
async def merge_audios_endpoint(req: MergeRequest):
    # Validar que no estén en la cola
    cola = await cola_store.read()
    for t in cola:
        if t["estado"] in ["pending", "processing", "failed"] and (t.get("session_name") == req.session1 or t.get("session_name") == req.session2):
            raise HTTPException(status_code=400, detail="Una de las sesiones está actualmente en la cola de procesamiento.")
    
    try:
        from services import audio_service
        merged_name = await audio_service.merge_sessions(req.session1, req.session2)
        return {"message": "Sesiones fusionadas con éxito", "merged_session": merged_name}
    except Exception as e:
        print(f"Error fusionando sesiones: {e}")
        raise HTTPException(status_code=500, detail=f"Error al fusionar: {e}")



# ===========================================================================
# Upload  (validación de tamaño OOM-safe: header + chunks)
# ===========================================================================

CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB por chunk


@app.post("/upload")
async def upload_audio(
    request: Request,
    audio: UploadFile = File(...),
    custom_name: str = Form(None),
    slot_id: str = Form(None),
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
        "slot_id": slot_id,
    }


# ===========================================================================
# AI Prompt Gen
# ===========================================================================

@app.post("/api/generate-prompt")
async def generate_prompt(req: PromptGenRequest):
    try:
        prompt_generado = await llm_service.generate_prompt_for_materia(
            req.descripcion, req.modelo_elegido
        )
        return {"prompt_generado": prompt_generado}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Chat RAG
# ===========================================================================

@app.post("/api/chat")
async def chat_estudio(req: ChatRequest):
    try:
        respuesta = await llm_service.chat_with_rag(
            req.mensaje, req.materia_id, req.modelo_elegido, image_data=req.image_data
        )
        return {"respuesta": respuesta}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tutor/chat")
async def tutor_chat(req: TutorChatRequest):
    try:
        respuesta = await llm_service.tutor_chat_with_rag(
            historial_mensajes=req.historial,
            pregunta_actual=req.pregunta,
            materia_id=req.materia_id,
            modelo=req.modelo_elegido,
            image_data=req.image_data
        )
        return {"respuesta": respuesta}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tutor/v2/chat")
async def tutor_v2_chat(req: TutorV2ChatRequest):
    try:
        resultado = await llm_service.tutor_v2_agentic_chat(
            slot_id=req.slot_id,
            historial_mensajes=req.historial,
            pregunta_actual=req.pregunta,
            modelo=req.modelo_elegido,
            image_data=req.image_data
        )
        return resultado
    except Exception as e:
        import traceback
        traceback.print_exc()
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
            "slot_id": getattr(req, "slot_id", None),
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
    meta_data = await meta_store.read()
    
    materias_list = await materias_store.read()
    mat_dict = {m.get("id"): m.get("nombre", "Desconocida") for m in materias_list}

    summaries = []
    for f, metadata in meta_data.items():
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
            
        materia_id = "default"
        materia_name = "General"
        match = re.search(r"__(.*?)__", f)
        if match:
            materia_id = match.group(1)
            if materia_id != "default":
                materia_name = mat_dict.get(materia_id, "Desconocida")

        created_at = metadata.get("fecha", "Fecha desconocida")
        if created_at != "Fecha desconocida" and len(created_at.split("-")) == 3:
            # Reformat to DD/MM/YYYY for UI consistency if it's YYYY-MM-DD
            try:
                y, m, d = created_at.split("-")
                created_at = f"{d}/{m}/{y} 12:00"
            except:
                pass

        summaries.append({
            "filename": f, 
            "display_name": display_name, 
            "created_at": created_at,
            "materia_id": materia_id,
            "materia_name": materia_name
        })
        
    # Sort by creation date descending (using filename string which contains date usually)
    summaries.sort(key=lambda x: x["filename"], reverse=True)
    return {"summaries": summaries}


@app.get("/api/summaries/{filename}")
async def get_summary(filename: str):
    if not filename.endswith(".md"):
        return {"error": "Solo se permiten archivos markdown."}
    
    meta_data = await meta_store.read()
    if filename not in meta_data:
        return {"error": "Archivo no encontrado en la base de datos."}
        
    content = meta_data[filename].get("resumen", "")
    return {"content": content}


@app.put("/api/summaries/{filename}")
async def update_summary(filename: str, req: SummaryUpdate):
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Solo archivos markdown.")
        
    meta_data = await meta_store.read()
    if filename not in meta_data:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")

    # Guardar en local si la carpeta existe (Backup local)
    filepath = os.path.join(RESUMENES_DIR, filename)
    if os.path.exists(RESUMENES_DIR):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(req.content)
        except Exception:
            pass

    async def _update_meta(md_data: dict) -> dict:
        if filename in md_data:
            md_data[filename]["resumen"] = req.content
            # Re-vectorizar en Pinecone
            try:
                from services.vector_store import upsert_document
                materia_id = "default"
                match = re.search(r"__(.*?)__", filename)
                if match:
                    materia_id = match.group(1)
                
                doc_id = filename.replace(".md", "")
                upsert_document(
                    doc_id=doc_id,
                    markdown_text=req.content,
                    materia_id=materia_id,
                    fecha=md_data[filename].get("fecha", "desconocida"),
                    filename=filename
                )
            except Exception as e:
                print(f"Error al re-vectorizar {filename}: {e}")
        return md_data

    await meta_store.update(_update_meta)
    return {"message": "Resumen actualizado"}


@app.delete("/api/summaries/{filename}")
async def delete_summary(filename: str):
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Solo archivos markdown.")
        
    meta_data = await meta_store.read()
    internal_id = filename
    content = ""
    for k, v in meta_data.items():
        if v.get("filename") == filename:
            internal_id = k
            content = v.get("resumen", "")
            break
            
    # Borrar imágenes de GridFS
    import re
    from database import get_fs
    fs = get_fs()
    images = re.findall(r'!\[\[(.*?)\]\]', content)
    for img in images:
        try:
            # Buscar el archivo en GridFS para obtener su _id
            cursor = fs.find({"filename": img})
            docs = await cursor.to_list(length=10)
            for doc in docs:
                await fs.delete(doc["_id"])
        except Exception as e:
            print(f"Error al borrar imagen {img} de GridFS: {e}")

    async def _remove_meta(m_data: dict) -> dict:
        m_data.pop(internal_id, None)
        # Por si acaso la clave era el filename
        m_data.pop(filename, None)
        return m_data

    await meta_store.update(_remove_meta)
    
    # Cascading delete: Reset slot
    async def _reset_slot(slots: list) -> list:
        for s in slots:
            if s.get("md_vinculado") == filename:
                s["estado"] = "AUSENTE"
                s["md_vinculado"] = None
        return slots
    await progreso_store.update(_reset_slot)
    
    # Cascading delete: Remove related flashcards
    async def _remove_cards(cards: list) -> list:
        # Borrar si el origen coincide con la clave interna (UUID) o el filename visual
        return [c for c in cards if c.get("origen_md") != internal_id and c.get("origen_md") != filename]
    await tarjetas_store.update(_remove_cards)
    
    return {"message": "Resumen eliminado y referencias limpiadas en cascada"}


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

from pydantic import BaseModel

class TarjetaCreate(BaseModel):
    materia_id: str
    origen_md: str
    origen_slot_id: str
    contenido: str
    tipo: str = "otro"
    fecha_entrega: str = ""
    referencia_temporal: str = ""

@app.post("/api/tarjetas")
async def create_tarjeta(req: TarjetaCreate):
    from services.create_tarjeta import create_tarjeta_manual
    nueva_tarjeta = await create_tarjeta_manual(
        materia_id=req.materia_id,
        origen_md=req.origen_md,
        origen_slot_id=req.origen_slot_id,
        contenido=req.contenido,
        tipo=req.tipo,
        fecha_entrega=req.fecha_entrega,
        referencia_temporal=req.referencia_temporal
    )
    return {"message": "ok", "tarjeta": nueva_tarjeta}

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

        # El usuario solicitó no crear archivos .md ni pasarlos a Obsidian para tareas/anuncios.
        # Las tareas capturadas irán únicamente al Tablón de Avisos (tarjetas_informativas.json)

        # Guardar tarjetas informativas
        if json_data.get("tarjetas_informativas"):
            fecha_str = datetime.now().strftime("%Y-%m-%d")
            await export_service.save_tarjetas_informativas(
                json_data["tarjetas_informativas"], "Captura_Pantalla", "default", fecha_str
            )

        return {"message": "Tarea extraída y añadida al Tablón de Avisos correctamente", "filename": "Solo Tablón"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Frontend estático (SIEMPRE al final)
# ===========================================================================
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
