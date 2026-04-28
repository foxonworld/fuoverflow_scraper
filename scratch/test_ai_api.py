import asyncio
import os
import sys

# Add current directory to path so we can import app
sys.path.append(os.getcwd())

from app.services import ai as ai_svc
from app.core.logger import logger

async def test_apis():
    from app.services import storage
    cfg = storage.load_config()
    
    prompt = "Say 'Hello'"
    
    print("\n--- TESTING GROQ ---")
    groq_key = cfg.get("GROQ_API_KEY", "")
    if groq_key:
        import requests
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        payload = {"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
        try:
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=10)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
        except Exception as e:
            print(f"Groq Exception: {e}")
    else:
        print("No GROQ_API_KEY found.")

    print("\n--- TESTING GEMINI (3.1-flash-lite-preview) ---")
    gemini_key = cfg.get("GEMINI_API_KEY", "")
    if gemini_key:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-preview:generateContent?key={gemini_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            print(f"Status: {resp.status_code}")
            print(f"Response: {resp.text}")
        except Exception as e:
            print(f"Gemini Exception: {e}")
    print("\n--- TESTING ask_text_only ---")
    try:
        # Prompt that should return JSON
        prompt = "Return exactly this JSON list: [{\"questionText\": \"test\", \"aiAns\": \"A\"}]"
        result = await ai_svc.ask_text_only(prompt)
        print(f"Result: {result}")
        if not result:
            print("Warning: result is empty list!")
    except Exception as e:
        print(f"ask_text_only Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_apis())
