import base64
import json
import os
import aiohttp
from typing import List, Dict, Any, Optional
from app.ai.providers.base import BaseAIProvider
from app.core.config import settings
from app.core.logging.logger import logger

class GeminiProvider(BaseAIProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model_id = "gemini-1.5-flash"
        self.vision_model_id = "gemini-3.1-flash-lite-preview"

    async def extract_text_from_images(
        self, 
        image_paths: List[str], 
        prompt: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []

        default_prompt = """Bạn là một chuyên gia OCR. Hãy đọc nội dung từ các ảnh bài tập trắc nghiệm này.
        BỎ QUA các thành phần UI (logo, nút bấm, progress bar).
        CHỈ TRÍCH XUẤT: Câu hỏi và các phương án.
        Trả về JSON: {"results": [{"questionText": "...", "aiAns": "A/B/C/D hoặc TF"}]}"""
        
        prompt = prompt or default_prompt
        parts = [{"text": prompt}]
        
        for path in image_paths:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                    parts.append({
                        "inline_data": {
                            "mime_type": "image/webp",
                            "data": b64
                        }
                    })

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.vision_model_id}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                try:
                    async with session.post(url, json=payload, timeout=60) as response:
                        res_json = await response.json()
                        if response.status in [429, 503]:
                            logger.warning(f"Gemini API error {response.status}, retrying...")
                            await asyncio.sleep(10 * (attempt + 1))
                            continue
                        
                        if 'candidates' not in res_json:
                            return []
                        
                        text_res = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                        return self._parse_json(text_res)
                except Exception as e:
                    logger.error(f"Gemini Exception: {e}")
                    await asyncio.sleep(5)
        return []

    async def ask_text_only(self, prompt: str) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_id}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }

        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                try:
                    async with session.post(url, json=payload, timeout=60) as response:
                        res_json = await response.json()
                        if response.status in [429, 503]:
                            await asyncio.sleep(10 * (attempt + 1))
                            continue
                        
                        if 'candidates' not in res_json:
                            return []
                        
                        text_res = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                        return self._parse_json(text_res)
                except Exception as e:
                    logger.error(f"Gemini Text Exception: {e}")
                    await asyncio.sleep(5)
        return []

    def _parse_json(self, text: str) -> List[Dict[str, Any]]:
        try:
            if text.startswith("```json"):
                text = text[7:-3].strip()
            elif text.startswith("```"):
                text = text[3:-3].strip()
            
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            return parsed.get("results", [])
        except Exception:
            return []
