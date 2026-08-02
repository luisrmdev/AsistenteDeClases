import os
from google import genai
from pinecone import Pinecone

from google.genai import types

# --- Conexión a Pinecone ---
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_HOST = os.getenv("PINECONE_HOST")

if not PINECONE_API_KEY or not PINECONE_HOST:
    print("ADVERTENCIA: Faltan variables de entorno de Pinecone. El RAG no funcionará.")
    index = None
else:
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(host=PINECONE_HOST)


def _chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> list[str]:
    """
    Corta un texto grande en chunks de tamaño máximo `chunk_size` con una superposición
    de `overlap` caracteres para no perder contexto semántico entre cortes.
    """
    if not text:
        return []
    
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        
        # Si no es el último chunk, intentar cortar en un punto final o salto de línea
        if end < len(text):
            # Buscar el último salto de línea en la ventana
            last_newline = text.rfind('\n', start, end)
            if last_newline != -1 and last_newline > start + (chunk_size // 2):
                end = last_newline + 1
            else:
                # Si no hay salto, buscar un punto
                last_dot = text.rfind('. ', start, end)
                if last_dot != -1 and last_dot > start + (chunk_size // 2):
                    end = last_dot + 2
                    
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)
        
    return [c for c in chunks if len(c) > 10]


def get_embedding(text: str) -> list[float]:
    """
    Llama a Gemini Embeddings para vectorizar un texto.
    """
    client = genai.Client()
    response = client.models.embed_content(
        model='gemini-embedding-2',
        contents=text,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    return response.embeddings[0].values


def upsert_document(doc_id: str, markdown_text: str, materia_id: str, fecha: str, filename: str):
    """
    Trocea un documento Markdown, lo vectoriza y lo inyecta en Pinecone.
    """
    if not index or not markdown_text.strip():
        return
        
    # 1. Chunking
    chunks = _chunk_text(markdown_text)
    if not chunks:
        return
        
    # 2. Vectorización
    client = genai.Client()
    embeddings = []
    for c in chunks:
        res = client.models.embed_content(
            model='gemini-embedding-2',
            contents=c,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        embeddings.append(res.embeddings[0].values)
    
    # 3. Formateo de Vectores para Pinecone
    vectors = []
    for i in range(len(chunks)):
        vec_id = f"{doc_id}_{i}"
        metadata = {
            "doc_id": doc_id,
            "materia_id": materia_id,
            "fecha": fecha,
            "filename": filename,
            "chunk_index": i,
            "text": chunks[i]  # Pinecone permite guardar el texto en la metadata
        }
        vectors.append((vec_id, embeddings[i], metadata))
    
    # 4. Upsert (Pinecone hace upsert automáticamente por ID, así que 
    # si el doc_id es el mismo, sobrescribe)
    # Sin embargo, si el nuevo doc tiene MENOS chunks, los chunks viejos sobrantes
    # podrían quedar huérfanos. Para un MVP de estudio esto es tolerable. 
    # Lo ideal sería borrar todo `doc_id` primero, pero Pinecone free-tier 
    # no permite delete by metadata filter fácilmente sin namespace, así que sobreescribimos.
    
    # Pinecone recomienda batches de 100
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        index.upsert(vectors=batch)

def delete_document(doc_id: str):
    """
    Elimina los chunks asociados a un documento en Pinecone.
    """
    if not index or not doc_id:
        return
    try:
        # Intentar borrado por filtro de metadatos (funciona en índices Serverless)
        index.delete(filter={"doc_id": {"$eq": doc_id}})
    except Exception as e:
        print(f"Error borrando por filtro en Pinecone, intentando por IDs fijos: {e}")
        # Fallback: borrar por IDs (doc_id_0 hasta doc_id_100 asumiendo un máximo seguro)
        ids_to_delete = [f"{doc_id}_{i}" for i in range(150)]
        try:
            index.delete(ids=ids_to_delete)
        except Exception as e2:
            print(f"Error final al borrar de Pinecone: {e2}")


def semantic_search(query: str, materia_id: str = "todas", max_results: int = 5, where_filter: dict = None) -> list[dict]:
    """
    Busca los chunks más relevantes semánticamente en Pinecone.
    """
    if not index or not query.strip():
        return []
        
    # Vectorizar la pregunta
    query_embedding = get_embedding(query)
    
    # Construir filtros (Metadata filtering en Pinecone)
    filter_dict = {}
    if materia_id and materia_id != "todas" and materia_id != "default":
        filter_dict["materia_id"] = {"$eq": materia_id}
        
    if where_filter:
        for k, v in where_filter.items():
            filter_dict[k] = {"$eq": v}
            
    # Consultar a Pinecone
    try:
        results = index.query(
            vector=query_embedding,
            top_k=max_results,
            include_metadata=True,
            filter=filter_dict if filter_dict else None
        )
    except Exception as e:
        print(f"Error querying Pinecone: {e}")
        return []
        
    if not results or "matches" not in results or len(results["matches"]) == 0:
        return []
        
    # Formatear la respuesta (igual que el viejo Chroma para no romper el LLM)
    recovered_chunks = []
    for match in results["matches"]:
        metadata = match.get("metadata", {})
        chunk_text = metadata.pop("text", "") # Sacamos el texto y dejamos la pura metadata visual
        recovered_chunks.append({
            "chunk_text": chunk_text,
            "metadata": metadata,
            "distance": 1.0 - match.get("score", 0.0) # Cosine similarity (1.0 = idéntico), así que distancia = 1 - score
        })
        
    return recovered_chunks
