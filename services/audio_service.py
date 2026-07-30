"""
services/audio_service.py — Lógica de preprocesamiento de audio con FFmpeg.
"""
import os
import subprocess

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
