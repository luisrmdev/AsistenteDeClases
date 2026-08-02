import asyncio
from google import genai
from dotenv import load_dotenv

load_dotenv()

async def list_available_models():
    client = genai.Client()
    def _sync_list():
        return list(client.models.list())
        
    modelos = await asyncio.to_thread(_sync_list)
    valid_models = []
    print("Total models:", len(modelos))
    for model in modelos:
        if not hasattr(model, "supported_actions"):
            print("No supported actions:", model.name)
            continue
        if not model.supported_actions:
            print("Empty supported actions:", model.name)
            continue
        if "generateContent" not in model.supported_actions:
            print("No generateContent:", model.name)
            continue
            
        model_name = (
            model.name.replace("models/", "")
            if model.name.startswith("models/")
            else model.name
        )
        if "gemini" in model_name and "1.0" not in model_name:
            valid_models.append(model_name)
    return valid_models

async def main():
    models = await list_available_models()
    print("Found valid models:", len(models))
    print(models)

asyncio.run(main())
