from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client()

try:
    models = client.models.list()
    print("Available flash models:")
    for m in models:
        if "flash" in m.name:
            print(m.name)
except Exception as e:
    print(f"Error: {e}")
