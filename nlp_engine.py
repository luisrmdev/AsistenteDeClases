import re
from datetime import datetime, timedelta
import collections

# Stopwords muy básicos en español
STOPWORDS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "a", "al", "en", "por", "para", "con", "sin", "sobre", "que", "qué", "y", "o", "u", "e", "es", "son", "fue", "fueron", "ser", "se", "me", "te", "le", "lo", "su", "sus", "mi", "mis", "tu", "tus", "como", "cómo", "cuando", "cuándo", "donde", "dónde", "porque", "porqué", "quien", "quién"}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
}

def parse_temporal_filter(query: str, current_date=None) -> tuple[datetime, datetime] | None:
    """
    Recibe la pregunta del usuario y devuelve una tupla (fecha_inicio, fecha_fin)
    o None si no encuentra marcadores temporales.
    """
    if current_date is None:
        current_date = datetime.now()
        
    query_lower = query.lower()
    
    # 1. "la clase pasada" -> Asumimos últimos 7 días como ventana para buscar la clase pasada
    # El ensamblador luego tomará la última dentro de este rango o se apoyará en que es un rango corto.
    if "clase pasada" in query_lower or "ultima clase" in query_lower or "última clase" in query_lower:
        return (current_date - timedelta(days=7), current_date)
        
    # 2. "esta semana"
    if "esta semana" in query_lower:
        # Lunes de esta semana
        start = current_date - timedelta(days=current_date.weekday())
        return (start, current_date)
        
    # 3. "semana pasada"
    if "semana pasada" in query_lower:
        start = current_date - timedelta(days=current_date.weekday() + 7)
        end = start + timedelta(days=6)
        return (start, end)
        
    # 4. Meses explícitos
    for mes_nombre, mes_num in MESES.items():
        if re.search(rf"\b{mes_nombre}\b", query_lower):
            # Asumimos año actual
            year = current_date.year
            start = datetime(year, mes_num, 1)
            # Aproximación del fin de mes (al día 28-31)
            next_month = mes_num + 1 if mes_num < 12 else 1
            next_year = year if mes_num < 12 else year + 1
            end = datetime(next_year, next_month, 1) - timedelta(days=1)
            return (start, end)
            
    # 5. "parcial" o "examen" -> Suele referirse a un rango de todo el semestre, devolvemos None para que el scoring haga el trabajo
    return None

def _tokenize(text: str) -> set:
    """Tokeniza un texto, pasa a minúsculas, elimina puntuación y stopwords."""
    words = re.findall(r'\b\w+\b', text.lower())
    return set(w for w in words if w not in STOPWORDS and len(w) > 2)

def score_relevance(query: str, summaries: list, threshold: float = 1.0, max_results: int = 8) -> list:
    """
    summaries: lista de dicts con la forma:
    {
      "filename": "...",
      "tags": ["tag1", "tag2"],
      "condensado": "...",
      "resumen": "..."
    }
    Devuelve los resúmenes ordenados por relevancia que superen el threshold.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        # Si la query no tiene palabras útiles, devolvemos los más recientes o todos limitados
        return summaries[:max_results]
        
    scored_summaries = []
    
    for summary in summaries:
        score = 0.0
        
        # Evaluar Tags (peso mayor)
        tags = summary.get("tags", [])
        tags_tokens = set()
        for t in tags:
            tags_tokens.update(_tokenize(t))
            
        overlap_tags = query_tokens.intersection(tags_tokens)
        score += len(overlap_tags) * 3.0
        
        # Evaluar Resumen completo (peso menor)
        resumen_tokens = _tokenize(summary.get("resumen", ""))
        overlap_resumen = query_tokens.intersection(resumen_tokens)
        # Sumamos 1.0 por cada palabra única de la query que aparece en el resumen
        score += len(overlap_resumen) * 1.0
        
        if score >= threshold:
            scored_summaries.append((score, summary))
            
    # Ordenar por score descendente
    scored_summaries.sort(key=lambda x: x[0], reverse=True)
    
    # Retornar solo los diccionarios, top K
    return [item[1] for item in scored_summaries[:max_results]]
