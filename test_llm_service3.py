import asyncio
from services.llm_service import list_available_models
from dotenv import load_dotenv

load_dotenv()

async def main():
    models = await list_available_models()
    print("Models returned by llm_service:", len(models))

asyncio.run(main())
