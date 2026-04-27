import base64
import json
import os
import aiohttp
import asyncio
from typing import List, Dict, Any, Optional
from app.ai.providers.base import BaseAIProvider
from app.core.logging.logger import logger

class GroqProvider(BaseAIProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.vision_model = "meta-llama/llama-4-scout-17b-16e-instruct"
        self.text_model = "llama-3.1-8b-instant"

    async def extract_text_from_images(
        self, 
        image_paths: List[str], 
        prompt: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []

        default_prompt = """Bạn là một chuyên gia OCR chuyên nghiệp. Hãy đọc nội dung văn bản từ các ảnh bài tập trắc nghiệm này.
        Trả về JSON: {"results": [{"questionText": "...", "aiAns": "A/B/C/D hoặc TF"}]}"""
        
        prompt = prompt or default_prompt
        content = [{"type": "text", "text": prompt}]
        
        for path in image_paths:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/webp;base64,{b64}"}
                    })

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.vision_model if image_paths else self.text_model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"}
        }

        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                try:
                    async with session.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=60) as response:
                        res_json = await response.json()
                        if response.status == 429:
                            await asyncio.sleep(15)
                            continue
                        
                        if 'choices' not in res_json:
                            return []
                        
                        text_res = res_json['choices'][0]['message']['content']
                        return json.loads(text_res).get("results", [])
                except Exception as e:
                    logger.error(f"Groq Exception: {e}")
                    await asyncio.sleep(5)
        return []

    async def ask_text_only(self, prompt: str) -> List[Dict[str, Any]]:
        return await self.extract_text_from_images([], prompt)
