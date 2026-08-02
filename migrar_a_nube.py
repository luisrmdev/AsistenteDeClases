"""
Script para migrar imágenes locales a la nube (MongoDB Atlas GridFS).
Se conecta a MONGODB_URI definido en el .env y sube todas las imágenes de resumenes/adjuntos/.
"""
import os
import asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

load_dotenv()

async def main():
    print("Iniciando migración a la nube...")
    mongo_uri = os.getenv("MONGODB_URI")
    if not mongo_uri:
        print("ERROR: MONGODB_URI no está definido en el archivo .env")
        return

    client = AsyncIOMotorClient(mongo_uri)
    db = client.synq_db
    fs = AsyncIOMotorGridFSBucket(db, bucket_name='adjuntos')

    adjuntos_dir = os.path.join("resumenes", "adjuntos")
    if not os.path.exists(adjuntos_dir):
        print(f"El directorio {adjuntos_dir} no existe. Nada que migrar.")
        return

    archivos = os.listdir(adjuntos_dir)
    if not archivos:
        print(f"No hay imágenes en {adjuntos_dir}. Nada que migrar.")
        return

    print(f"Se encontraron {len(archivos)} archivos. Iniciando subida...")
    
    subidos = 0
    omitidos = 0
    errores = 0

    for archivo in archivos:
        file_path = os.path.join(adjuntos_dir, archivo)
        if not os.path.isfile(file_path):
            continue
            
        try:
            # Check if exists
            cursor = fs.find({"filename": archivo})
            docs = await cursor.to_list(length=1)
            if docs:
                print(f"Omitiendo {archivo}: Ya existe en la nube.")
                omitidos += 1
                continue

            with open(file_path, "rb") as f:
                content = f.read()
                
            await fs.upload_from_stream(
                archivo,
                content,
                metadata={"migrated": True}
            )
            print(f"Subido exitosamente: {archivo}")
            subidos += 1
        except Exception as e:
            print(f"Error al subir {archivo}: {e}")
            errores += 1

    print("\n--- Resumen de Migración ---")
    print(f"Total encontrados: {len(archivos)}")
    print(f"Subidos a la nube: {subidos}")
    print(f"Omitidos (ya existían): {omitidos}")
    print(f"Errores: {errores}")
    print("Migración completada.")

if __name__ == "__main__":
    asyncio.run(main())
