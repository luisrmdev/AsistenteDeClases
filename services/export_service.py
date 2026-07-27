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
    tareas_store,
)


async def save_markdown_and_metadata(
    md_filename: str,
    suggested_filename: str,
    suggested_folder: str,
    texto_limpio: str,
    tags: list,
    fecha_str: str,
) -> None:
    """
    Guarda el archivo Markdown en resumenes/ y actualiza resumenes_meta.json.
    """
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
        }
        return meta_data

    await meta_store.update(_updater)


async def save_to_obsidian(
    suggested_filename: str,
    suggested_folder: str,
    texto_limpio: str,
) -> None:
    """
    Escribe el Markdown directamente en la bóveda de Obsidian si está configurada.
    Falla silenciosamente si la ruta no existe o no está montada.
    """
    settings = await settings_store.read()
    obsidian_path = settings.get("obsidian_vault_path", "")
    if not obsidian_path or not os.path.exists(obsidian_path):
        return

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


async def generate_ics_and_save_tasks(
    eventos: list,
    suggested_filename: str,
    md_filename: str,
) -> str | None:
    """
    Genera un archivo .ics en exportaciones/ y persiste los eventos en
    tareas_meta.json.

    Returns:
        El nombre del archivo .ics generado, o None si hubo error.
    """
    ics_filename = None

    # Persistir en tareas_meta.json
    async def _add_tasks(tareas_data: list) -> list:
        for t in eventos:
            t["id"] = str(uuid.uuid4())
            t["origen"] = suggested_filename
            t["completada"] = False
            tareas_data.append(t)
        return tareas_data

    await tareas_store.update(_add_tasks)

    # Generar archivo ICS
    try:
        from icalendar import Calendar, Event  # type: ignore

        cal = Calendar()
        for evento in eventos:
            fecha_evt_str = evento.get("fecha_YYYY_MM_DD")
            if not fecha_evt_str:
                continue
            try:
                dt = datetime.strptime(fecha_evt_str, "%Y-%m-%d").date()
                ievent = Event()
                ievent.add("summary", evento.get("titulo", "Sin título"))
                ievent.add("dtstart", dt)
                ievent.add("description", evento.get("descripcion", ""))
                cal.add_component(ievent)
            except Exception as e:
                print("Error parseando fecha para ICS:", e)

        ics_filename = f"{md_filename.replace('.md', '')}.ics"
        with open(os.path.join(EXPORTACIONES_DIR, ics_filename), "wb") as f:
            f.write(cal.to_ical())
    except Exception as e:
        print("Error generando Calendario:", e)

    return ics_filename


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
