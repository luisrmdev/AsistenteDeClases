"""
database.py — Gestor de persistencia con protección de concurrencia.
Todos los archivos JSON del sistema se leen y escriben ÚNICAMENTE a través
de esta clase para evitar race conditions y corrupción de datos.
"""
import asyncio
import json
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# --- Constantes de rutas de archivos ---
AUDIOS_DIR = "audios"
RESUMENES_DIR = "resumenes"
PAPELERA_DIR = "papelera_audios"
EXPORTACIONES_DIR = "exportaciones"
MEMORIA_DIR = "memoria_ia"
MATERIAS_FILE = "materias.json"
STATS_FILE = "stats.json"
SETTINGS_FILE = "settings.json"
META_FILE = os.path.join(RESUMENES_DIR, "resumenes_meta.json")
TARJETAS_FILE = os.path.join(RESUMENES_DIR, "tarjetas_informativas.json")

# Crear directorios necesarios al importar
for _dir in [AUDIOS_DIR, RESUMENES_DIR, PAPELERA_DIR, EXPORTACIONES_DIR, MEMORIA_DIR]:
    os.makedirs(_dir, exist_ok=True)


class JsonStore:
    """
    Gestor de un único archivo JSON con asyncio.Lock para proteger lecturas
    y escrituras concurrentes. Cada archivo JSON tiene su propia instancia.
    """

    def __init__(self, filepath: str, default: Any = None):
        self._filepath = filepath
        self._default = default if default is not None else {}
        self._lock = asyncio.Lock()

    async def read(self) -> Any:
        async with self._lock:
            return self._read_sync()

    async def write(self, data: Any) -> None:
        async with self._lock:
            self._write_sync(data)

    async def update(self, updater_fn) -> Any:
        """Lee, aplica updater_fn(data) -> new_data, y escribe atómicamente."""
        async with self._lock:
            data = self._read_sync()
            new_data = updater_fn(data)
            self._write_sync(new_data)
            return new_data

    def _read_sync(self) -> Any:
        if not os.path.exists(self._filepath):
            return self._default() if callable(self._default) else self._default
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self._default() if callable(self._default) else self._default

    def _write_sync(self, data: Any) -> None:
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# --- Instancias globales (una por archivo) ---
settings_store = JsonStore(SETTINGS_FILE, default={
    "obsidian_vault_path": os.getenv("OBSIDIAN_VAULT_PATH", ""),
    "browser_cookie_source": "brave",
    "max_audio_upload_mb": 500,
    "default_model": "gemini-3.1-flash-lite",
    "rag_max_docs": 8,
})
materias_store = JsonStore(MATERIAS_FILE, default=list)
stats_store = JsonStore(STATS_FILE, default=dict)
meta_store = JsonStore(META_FILE, default=dict)
tarjetas_store = JsonStore(TARJETAS_FILE, default=list)
