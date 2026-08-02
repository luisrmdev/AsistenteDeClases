import asyncio
from services.llm_service import list_available_models
from dotenv import load_dotenv

load_dotenv()

async def main():
    models = await list_available_models()
    print("Found models:", len(models))
    print(models)

asyncio.run(main())
