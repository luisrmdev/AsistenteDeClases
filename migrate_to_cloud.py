import asyncio
import json
import os
from database import db
from services.vector_store import upsert_document
from services.auth_service import get_password_hash

async def migrate_json_to_mongo(file_path: str, collection_name: str, doc_id: str = "singleton"):
    if not os.path.exists(file_path):
        print(f"Omitiendo {file_path}, no existe.")
        return
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        await db[collection_name].update_one(
            {"_id": doc_id},
            {"$set": {"data": data}},
            upsert=True
        )
        print(f"Migrado {file_path} a MongoDB -> {collection_name}")
    except Exception as e:
        print(f"Error migrando {file_path}: {e}")

async def create_initial_admin():
    collection = db["usuarios"]
    admin = await collection.find_one({"_id": "singleton"})
    
    if admin and admin.get("data"):
        print("El administrador ya existe en MongoDB.")
        return
        
    print("Creando usuario administrador inicial...")
    # Creamos un usuario por defecto
    users_data = [{
        "username": "admin",
        "password_hash": get_password_hash("synq2026") # Contraseña por defecto
    }]
    
    await collection.update_one(
        {"_id": "singleton"},
        {"$set": {"data": users_data}},
        upsert=True
    )
    print("Usuario inicial creado: admin / synq2026")

async def migrate_markdown_to_pinecone():
    resumenes_dir = "resumenes"
    if not os.path.exists(resumenes_dir):
        return
        
    files = [f for f in os.listdir(resumenes_dir) if f.endswith(".md")]
    print(f"Encontrados {len(files)} archivos Markdown para subir a Pinecone.")
    
    for filename in files:
        filepath = os.path.join(resumenes_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Extraer materia_id del nombre del archivo (resumen__materia_id__...)
        materia_id = "default"
        import re
        match = re.search(r"__(.*?)__", filename)
        if match:
            materia_id = match.group(1)
            
        fecha = "desconocida"
        doc_id = filename
        
        print(f"Vectorizando y subiendo: {filename}...")
        upsert_document(
            doc_id=doc_id,
            markdown_text=content,
            materia_id=materia_id,
            fecha=fecha,
            filename=filename
        )
        print(f"Subido: {filename}")

async def main():
    print("--- INICIANDO MIGRACIÓN A LA NUBE (SYNQ) ---")
    
    await create_initial_admin()
    
    # 1. Migrar JSONs Transaccionales
    await migrate_json_to_mongo("settings.json", "settings")
    await migrate_json_to_mongo("materias.json", "materias")
    await migrate_json_to_mongo("resumenes/cola_tareas.json", "cola")
    await migrate_json_to_mongo("resumenes/progreso_semestral.json", "progreso")
    await migrate_json_to_mongo("resumenes/tarjetas_informativas.json", "tarjetas")
    await migrate_json_to_mongo("resumenes/resumenes_meta.json", "meta_resumenes")
    
    # 2. Migrar Vectores a Pinecone
    print("\nIniciando subida a Pinecone (Esto puede tomar varios minutos si hay muchos archivos)...")
    await migrate_markdown_to_pinecone()
    
    print("\n--- MIGRACIÓN COMPLETADA EXITOSAMENTE ---")
    print("Ya puedes borrar los archivos .json locales y la carpeta .agent/chroma_db si lo deseas.")

if __name__ == "__main__":
    asyncio.run(main())
