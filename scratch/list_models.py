import requests
import json

api_key = "AIzaSyAPGPj_KL7mFIr5rHWgD_BR5IaBR6cE7ds"
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"

resp = requests.get(url)
if resp.status_code == 200:
    models = resp.json().get("models", [])
    for m in models:
        print(f"Name: {m['name']}, Display: {m['displayName']}")
else:
    print(f"Error: {resp.status_code}, {resp.text}")
