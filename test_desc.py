import asyncio
from services.llm_service import list_available_models
from dotenv import load_dotenv

load_dotenv()

async def main():
    models = await list_available_models()
    for m in models:
        if m["description"] is None:
            print("FOUND NONE:", m["id"])

asyncio.run(main())
