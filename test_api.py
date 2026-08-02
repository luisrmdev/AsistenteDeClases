import requests
res = requests.get("http://localhost:8000/api/models")
print(res.status_code)
print(res.text)
