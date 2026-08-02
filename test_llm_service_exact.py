import asyncio
from google import genai
from dotenv import load_dotenv

load_dotenv()

async def list_available_models():
    try:
        client = genai.Client()
        def _sync_list():
            return list(client.models.list())
            
        modelos = await asyncio.to_thread(_sync_list)
        valid_models = []
        for model in modelos:
            if (
                hasattr(model, "supported_actions")
                and model.supported_actions
                and "generateContent" in model.supported_actions
            ):
                model_name = (
                    model.name.replace("models/", "")
                    if model.name.startswith("models/")
                    else model.name
                )
                if "gemini" in model_name and "1.0" not in model_name:
                    valid_models.append(
                        {
                            "id": model_name,
                            "name": model_name,
                            "description": getattr(model, "description", "Modelo de IA"),
                        }
                    )
        valid_models.sort(key=lambda x: ("flash" not in x["id"].lower(), x["id"]))
        return valid_models
    except Exception as e:
        print("Error fetching models:", e)
        return []

async def main():
    models = await list_available_models()
    print("Found valid models:", len(models))

asyncio.run(main())
