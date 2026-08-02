import asyncio
from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
client = genai.Client()

try:
    response = client.models.embed_content(
        model='text-embedding-004',
        contents='hello world'
    )
    print("Embedding length:", len(response.embeddings[0].values))
except Exception as e:
    print("Error:", e)
