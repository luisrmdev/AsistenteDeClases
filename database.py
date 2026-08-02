"""
database.py — Gestor de persistencia con MongoDB Atlas (Stateless).
Protección de concurrencia mediante asyncio.Lock.
"""
import asyncio
import os
from typing import Any
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# --- Constantes de rutas de archivos (Legacy para adjuntos y exportaciones temporales) ---
AUDIOS_DIR = "grabaciones"
RESUMENES_DIR = "resumenes"
ADJUNTOS_DIR = os.path.join(RESUMENES_DIR, "adjuntos")
PAPELERA_DIR = "papelera_sesiones"
EXPORTACIONES_DIR = "exportaciones"
MEMORIA_DIR = "memoria_ia"

# Crear directorios temporales necesarios
for _dir in [AUDIOS_DIR, RESUMENES_DIR, ADJUNTOS_DIR, PAPELERA_DIR, EXPORTACIONES_DIR, MEMORIA_DIR]:
    os.makedirs(_dir, exist_ok=True)

# --- Conexión a MongoDB Atlas ---
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise ValueError("Falta MONGODB_URI en .env")

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

# ... (other imports stay the same)

client = AsyncIOMotorClient(MONGODB_URI)
db = client.synq_db
fs = AsyncIOMotorGridFSBucket(db, bucket_name='adjuntos')

class MongoStore:
    """
    Gestor que simula un JSON local, pero guarda los datos en una colección 
    de MongoDB usando un documento 'singleton' para mantener compatibilidad total 
    con la base de código existente.
    """
    def __init__(self, collection_name: str, default: Any = None):
        self.collection = db[collection_name]
        self.doc_id = "singleton"
        self._default = default if default is not None else {}
        self._lock = asyncio.Lock()

    async def read(self) -> Any:
        async with self._lock:
            doc = await self.collection.find_one({"_id": self.doc_id})
            if not doc:
                default_val = self._default() if callable(self._default) else self._default
                return default_val
            return doc.get("data", self._default() if callable(self._default) else self._default)

    async def write(self, data: Any) -> None:
        async with self._lock:
            await self.collection.update_one(
                {"_id": self.doc_id},
                {"$set": {"data": data}},
                upsert=True
            )

    async def update(self, updater_fn) -> Any:
        """Lee, aplica updater_fn(data) -> new_data, y escribe atómicamente."""
        async with self._lock:
            doc = await self.collection.find_one({"_id": self.doc_id})
            if not doc:
                data = self._default() if callable(self._default) else self._default
            else:
                data = doc.get("data", self._default() if callable(self._default) else self._default)
                
            if asyncio.iscoroutinefunction(updater_fn):
                new_data = await updater_fn(data)
            else:
                new_data = updater_fn(data)
                
            await self.collection.update_one(
                {"_id": self.doc_id},
                {"$set": {"data": new_data}},
                upsert=True
            )
            return new_data


settings_store = MongoStore("settings", default={
    "obsidian_vault_path": os.getenv("OBSIDIAN_VAULT_PATH", ""),
    "max_audio_upload_mb": 500,
    "max_papelera_items": 10,
    "default_model": "gemini-3.1-flash-lite",
    "rag_max_docs": 8,
    "nlp_threshold": 1.0,
    "audio_silence_db": -30,
})

materias_store = MongoStore("materias", default=list)
meta_store = MongoStore("meta_resumenes", default=dict)
tarjetas_store = MongoStore("tarjetas", default=list)
cola_store = MongoStore("cola", default=list)
progreso_store = MongoStore("progreso", default=list)
usuarios_store = MongoStore("usuarios", default=list)
