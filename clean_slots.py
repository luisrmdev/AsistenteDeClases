import json
import os

with open("materias.json", "r") as f:
    materias = json.load(f)
materia_ids = {m["id"] for m in materias}

with open("progreso.json", "r") as f:
    slots = json.load(f)

valid_slots = [s for s in slots if s.get("materia_id") in materia_ids]

with open("progreso.json", "w") as f:
    json.dump(valid_slots, f, indent=4)

print(f"Deleted {len(slots) - len(valid_slots)} orphaned slots.")
