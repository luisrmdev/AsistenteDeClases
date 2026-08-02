from database import get_db, materias_store, progreso_store
import asyncio

async def main():
    materias = await materias_store.read()
    print("Materias IDs:")
    for m in materias:
        print(f"- {m.get('id')} ({m.get('nombre')})")
        
    slots = await progreso_store.read()
    print("\nProgreso Slots materia_ids:")
    if slots:
        for s in slots[:5]:
            print(f"- {s.get('materia_id')} (Slot: {s.get('id')})")
    else:
        print("No slots.")

asyncio.run(main())
