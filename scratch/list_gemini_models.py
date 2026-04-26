import os
import requests
import json

def list_models():
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        api_key = config.get("GEMINI_API_KEY")
        if not api_key:
            print("API Key not found in config.json")
            return

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url)
        models = response.json()
        
        print("\n--- MODEL LIST ---")
        for m in models.get('models', []):
            name = m['name'].replace('models/', '')
            print(f"ID: {name}")
        print("------------------\n")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_models()
