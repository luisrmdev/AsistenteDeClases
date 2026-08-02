import asyncio
import os
from database import meta_store, RESUMENES_DIR
from services.vector_store import upsert_document

async def migrate():
    meta_data = await meta_store.read()
    print(f"Encontrados {len(meta_data)} documentos en el meta_store.")
    
    for filename, meta in meta_data.items():
        doc_id = filename.replace(".md", "")
        materia_id = "default"
        # Infer materia_id from filename (e.g. __math__)
        if "__" in doc_id:
            parts = doc_id.split("__")
            if len(parts) > 1:
                materia_id = parts[1]
                
        fecha = meta.get("fecha", "1970-01-01")
        
        md_path = os.path.join(RESUMENES_DIR, filename)
        if os.path.exists(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            print(f"Procesando {filename}...")
            upsert_document(
                doc_id=doc_id,
                markdown_text=content,
                materia_id=materia_id,
                fecha=fecha,
                filename=filename
            )
        else:
            print(f"Archivo no encontrado: {md_path}")
            
    print("Migración completada con éxito.")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    asyncio.run(migrate())
