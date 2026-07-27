import os
import re

SERVER_PY = "server.py"

with open(SERVER_PY, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update imports
content = content.replace("from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile", "import asyncio\nfrom contextlib import asynccontextmanager\nfrom fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile")
content = content.replace("    tarjetas_store,\n)", "    tarjetas_store,\n    cola_store,\n)")

# 2. Add worker loop and lifespan
worker_code = """
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
"""
content = content.replace("app = FastAPI()", worker_code)

# 3. Replace old `/api/generate` and `/api/save` with new `/api/generate` and `/api/cola/*`
# Let's extract the part of the code we need to replace using regex.
pattern = r"# ===========================================================================\n# Generate \(Human-in-the-Loop\)\n# ===========================================================================.*?# ===========================================================================\n# Summaries\n# ==========================================================================="

new_endpoints = """# ===========================================================================
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
# ==========================================================================="""

content = re.sub(pattern, new_endpoints, content, flags=re.DOTALL)

with open(SERVER_PY, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch successful!")
