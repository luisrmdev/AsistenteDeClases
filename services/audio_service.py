"""
services/audio_service.py — Lógica de preprocesamiento de audio con FFmpeg.
"""
import os
import subprocess
import shutil
import re
from datetime import datetime

from database import AUDIOS_DIR


def remove_silences(input_filepath: str, silence_threshold_db: int = -30) -> str:
    """
    Ejecuta FFmpeg para remover silencios del audio.

    Returns:
        La ruta del archivo a usar para el upload:
        - temp_filepath si FFmpeg tuvo éxito.
        - input_filepath original si FFmpeg falló (fallback silencioso).
    """
    filename = os.path.basename(input_filepath)
    temp_filepath = os.path.join(AUDIOS_DIR, "temp_" + filename)

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_filepath,
                "-af", f"silenceremove=stop_periods=-1:stop_duration=2:stop_threshold={silence_threshold_db}dB",
                temp_filepath,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if os.path.exists(temp_filepath):
            print("Audio optimizado con FFmpeg.")
            return temp_filepath
    except Exception as e:
        print(f"Fallo FFmpeg (usando original): {e}")

    return input_filepath


def cleanup_temp(temp_filepath: str, original_filepath: str) -> None:
    """Elimina el archivo temporal si es diferente al original."""
    if temp_filepath != original_filepath and os.path.exists(temp_filepath):
        try:
            os.remove(temp_filepath)
        except Exception:
            pass


async def merge_sessions(session1_name: str, session2_name: str) -> str:
    """
    Une dos sesiones físicas de audio y desplaza el tiempo de las capturas de la segunda sesión.
    Retorna el nombre de la nueva sesión fusionada.
    """
    session1_dir = os.path.join(AUDIOS_DIR, session1_name)
    session2_dir = os.path.join(AUDIOS_DIR, session2_name)

    if not os.path.isdir(session1_dir) or not os.path.isdir(session2_dir):
        raise ValueError("Una o ambas sesiones no existen.")

    # Encontrar archivos de audio
    audio_exts = (".webm", ".m4a", ".opus", ".mp3", ".ogg")
    audio1 = next((f for f in os.listdir(session1_dir) if f.endswith(audio_exts)), None)
    audio2 = next((f for f in os.listdir(session2_dir) if f.endswith(audio_exts)), None)

    if not audio1 or not audio2:
        raise ValueError("No se encontraron archivos de audio en las sesiones.")

    audio1_path = os.path.join(session1_dir, audio1)
    audio2_path = os.path.join(session2_dir, audio2)

    # 1. Obtener duración del audio 1
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio1_path],
        capture_output=True, text=True, check=True
    )
    duration_sec = float(result.stdout.strip())

    # 2. Crear nueva sesión
    merged_session_name = f"session_merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    merged_dir = os.path.join(AUDIOS_DIR, merged_session_name)
    os.makedirs(merged_dir, exist_ok=True)

    # 3. Concatenar audios
    list_filepath = os.path.join(merged_dir, "list.txt")
    with open(list_filepath, "w") as f:
        f.write(f"file '{os.path.abspath(audio1_path)}'\n")
        f.write(f"file '{os.path.abspath(audio2_path)}'\n")

    merged_audio_path = os.path.join(merged_dir, audio1) # Mantener el nombre base del audio 1
    subprocess.run([
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_filepath, "-c", "copy", merged_audio_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    os.remove(list_filepath)

    # 4. Copiar imágenes sesión 1
    for img in os.listdir(session1_dir):
        if img.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            shutil.copy2(os.path.join(session1_dir, img), os.path.join(merged_dir, img))

    # 5. Desplazar tiempo y copiar imágenes sesión 2
    for img in os.listdir(session2_dir):
        if img.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            match = re.search(r"t(\d+)s", img)
            if match:
                old_t = int(match.group(1))
                new_t = old_t + int(duration_sec)
                new_img = img.replace(f"t{old_t}s", f"t{new_t}s")
                shutil.copy2(os.path.join(session2_dir, img), os.path.join(merged_dir, new_img))

    # 6. Limpieza
    shutil.rmtree(session1_dir)
    shutil.rmtree(session2_dir)

    return merged_session_name
