import uuid
from datetime import datetime
from database import tarjetas_store

async def create_tarjeta_manual(
    materia_id: str,
    origen_md: str,
    origen_slot_id: str,
    contenido: str,
    tipo: str = "otro",
    fecha_entrega: str = "",
    referencia_temporal: str = "",
):
    fecha_creacion = datetime.now().strftime("%Y-%m-%d")
    
    nueva_tarjeta = {
        "id": str(uuid.uuid4()),
        "materia_id": materia_id,
        "fecha_creacion": fecha_creacion,
        "origen_md": origen_md,
        "origen_slot_id": origen_slot_id,
        "fecha_entrega": fecha_entrega,
        "estado": "PENDIENTE",
        "tipo": tipo,
        "contenido": contenido,
        "referencia_temporal": referencia_temporal,
        "nota_personal": ""
    }

    async def _add_tarjeta(tarjetas_data) -> list:
        if isinstance(tarjetas_data, dict):
            tarjetas_data = list(tarjetas_data.values()) if tarjetas_data else []
        tarjetas_data.append(nueva_tarjeta)
        return tarjetas_data

    await tarjetas_store.update(_add_tarjeta)
    return nueva_tarjeta
