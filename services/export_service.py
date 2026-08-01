"""
services/export_service.py — Guardado de Markdown, Obsidian, Calendario ICS
                             y Reglas del Profesor.
"""
import os
import uuid
from datetime import datetime

from database import (
    EXPORTACIONES_DIR,
    MEMORIA_DIR,
    RESUMENES_DIR,
    meta_store,
    settings_store,
    tarjetas_store,
    progreso_store,
)


async def save_markdown_and_metadata(
    md_filename: str,
    suggested_filename: str,
    suggested_folder: str,
    texto_limpio: str,
    tags: list,
    fecha_str: str,
    slot_id: str = None,
) -> None:
    """
    Guarda el archivo Markdown en resumenes/ y actualiza resumenes_meta.json.
    """
    # Inject slot_id into YAML frontmatter if exists
    if slot_id:
        yaml_end_idx = texto_limpio.find("---", 3)
        if yaml_end_idx != -1:
            texto_limpio = texto_limpio[:yaml_end_idx] + f"slot_id: {slot_id}\n" + texto_limpio[yaml_end_idx:]
            
    # Archivo .md local con nombre sugerido por Gemini
    dest_path = os.path.join(RESUMENES_DIR, suggested_filename)
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(texto_limpio)

    # Condensado: primeros 150 caracteres del contenido
    condensado_text = texto_limpio[:150].replace("\n", " ").strip()
    if len(texto_limpio) > 150:
        condensado_text += "..."

    async def _updater(meta_data: dict) -> dict:
        meta_data[md_filename] = {
            "filename": suggested_filename,
            "folder": suggested_folder,
            "tags": tags,
            "condensado": condensado_text,
            "fecha": fecha_str,
            "resumen": texto_limpio,
            "slot_id": slot_id,
        }
        return meta_data

    await meta_store.update(_updater)
    
    # Update Slot state if linked
    if slot_id:
        async def _update_slot(slots: list) -> list:
            for s in slots:
                if s["id"] == slot_id:
                    s["estado"] = "AL_DIA"
                    s["md_vinculado"] = md_filename
            return slots
        await progreso_store.update(_update_slot)


async def save_to_obsidian(
    suggested_filename: str,
    suggested_folder: str,
    texto_limpio: str,
    image_paths: list = None,
) -> None:
    """
    Escribe el Markdown directamente en la bóveda de Obsidian si está configurada.
    Si se proveen image_paths, copia cada imagen a vault/Adjuntos/ para que la
    sintaxis ![[nombre.jpg]] generada por Gemini funcione correctamente en Obsidian.
    Falla silenciosamente si la ruta no existe o no está montada.
    """
    import shutil as _shutil

    settings = await settings_store.read()
    obsidian_path = settings.get("obsidian_vault_path", "")
    if not obsidian_path or not os.path.exists(obsidian_path):
        return

    # --- Escribir el Markdown ---
    target_dir = (
        os.path.join(obsidian_path, suggested_folder)
        if suggested_folder
        else obsidian_path
    )
    os.makedirs(target_dir, exist_ok=True)
    obs_file = os.path.join(target_dir, suggested_filename)
    try:
        with open(obs_file, "w", encoding="utf-8") as f:
            f.write(texto_limpio)
    except Exception as e:
        print("Error guardando en Obsidian:", e)

    # --- Copiar imágenes a vault/Adjuntos/ (siempre, el sistema es multi-modal) ---
    adjuntos_dir = os.path.join(obsidian_path, "Adjuntos")
    os.makedirs(adjuntos_dir, exist_ok=True)
    for img_path in (image_paths or []):
        if not os.path.exists(img_path):
            continue
        dest = os.path.join(adjuntos_dir, os.path.basename(img_path))
        try:
            _shutil.copy2(img_path, dest)
            print(f"[Obsidian] Imagen copiada: {os.path.basename(img_path)}")
        except Exception as e:
            print(f"[Obsidian] Error copiando imagen {img_path}: {e}")


async def save_tarjetas_informativas(
    tarjetas: list,
    md_filename: str,
    materia_id: str,
    fecha_creacion: str,
    slot_id: str = None,
) -> None:
    """
    Persiste las tarjetas informativas extraídas en tarjetas_informativas.json.
    """
    if not tarjetas:
        return

    async def _add_tarjetas(tarjetas_data: list) -> list:
        for t in tarjetas:
            t["id"] = str(uuid.uuid4())
            t["materia_id"] = materia_id
            t["fecha_creacion"] = fecha_creacion
            t["origen_md"] = md_filename
            t["origen_slot_id"] = slot_id
            t["fecha_entrega"] = t.get("fecha_entrega", "")
            t["estado"] = "PENDIENTE"
            t["tipo"] = t.get("tipo", "otro")
            t["contenido"] = t.get("contenido", "")
            t["referencia_temporal"] = t.get("referencia_temporal", "")
            t["nota_personal"] = ""
            tarjetas_data.append(t)
        return tarjetas_data

    await tarjetas_store.update(_add_tarjetas)


async def save_teacher_rules(
    nuevas_reglas: list,
    materia_id: str | None,
    fecha_str: str,
) -> None:
    """
    Acumula en append las reglas del profesor en memoria_ia/reglas_{materia}.md.
    """
    materia_name = (
        materia_id if materia_id and materia_id != "default" else "general"
    )
    reglas_filepath = os.path.join(MEMORIA_DIR, f"reglas_{materia_name}.md")
    try:
        with open(reglas_filepath, "a+", encoding="utf-8") as rf:
            for regla in nuevas_reglas:
                tema = regla.get("tema", "Sin tema")
                metodo = regla.get("metodo_paso_a_paso", "")
                if metodo:
                    rf.write(f"\n### Regla extraída el {fecha_str}: {tema}\n")
                    rf.write(f"{metodo}\n---\n")
    except Exception as e:
        print("Error guardando memoria del profesor:", e)
