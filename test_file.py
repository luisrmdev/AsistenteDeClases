import sys
from google import genai
from dotenv import load_dotenv
import time

load_dotenv()
client = genai.Client()

filepath = "audios/meet_20260718_012740.webm"
print(f"Uploading {filepath}...")
file = client.files.upload(file=filepath, config={'mime_type': 'audio/webm'})
print(f"File uploaded. Name: {file.name}")

file_info = client.files.get(name=file.name)
while file_info.state.name == "PROCESSING":
    print("Processing...")
    time.sleep(2)
    file_info = client.files.get(name=file.name)

print(f"State: {file_info.state.name}")
if file_info.state.name == "FAILED":
    print(f"Error details: {file_info.error}")
else:
    print("Success!")
