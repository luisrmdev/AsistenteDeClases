from dotenv import load_dotenv
load_dotenv()
from google import genai
client = genai.Client()
models = list(client.models.list())
print(dir(models[0]))
print(models[0].supported_actions)
