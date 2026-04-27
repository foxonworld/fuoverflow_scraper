"""
AI Service: Gọi Gemini hoặc Groq để OCR và phân tích ảnh.
- Không dùng Strategy Pattern phức tạp: chỉ if/else provider
- Có retry 3 lần + timeout
- Không crash toàn server nếu API lỗi
"""
import asyncio
import base64
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests

from app.core.logger import logger
from app.services.storage import load_config


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json_result(text: str) -> List[Dict[str, Any]]:
    """Parse JSON trả về từ AI, xử lý cả trường hợp có markdown code fence."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        return parsed.get("results", [])
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Gemini
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini(
    api_key: str,
    parts: List[Dict[str, Any]],
    model: str = "gemini-1.5-flash",
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Gọi Gemini API đồng bộ (chạy trong thread). Retry 3 lần."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code in (429, 503):
                wait = 10 * (attempt + 1)
                logger.warning(f"Gemini {resp.status_code}, đợi {wait}s (lần {attempt+1}/3)...")
                time.sleep(wait)
                continue
            data = resp.json()
            if "candidates" not in data:
                logger.error(f"Gemini phản hồi lạ: {str(data)[:200]}")
                return []
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            return _parse_json_result(raw)
        except requests.Timeout:
            logger.warning(f"Gemini timeout (lần {attempt+1}/3)")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Gemini exception (lần {attempt+1}/3): {e}")
            time.sleep(5)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Groq
# ─────────────────────────────────────────────────────────────────────────────

def _call_groq(
    api_key: str,
    content: List[Dict[str, Any]],
    model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Gọi Groq API đồng bộ (chạy trong thread). Retry 3 lần."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 429:
                msg = resp.json().get("error", {}).get("message", "")
                import re
                m = re.search(r"try again in ([\d\.]+)s", msg)
                wait = float(m.group(1)) + 1 if m else 15
                logger.warning(f"Groq rate limit, đợi {wait}s (lần {attempt+1}/3)...")
                time.sleep(wait)
                continue
            data = resp.json()
            if "choices" not in data:
                logger.error(f"Groq phản hồi lạ: {str(data)[:200]}")
                return []
            raw = data["choices"][0]["message"]["content"]
            return _parse_json_result(raw)
        except requests.Timeout:
            logger.warning(f"Groq timeout (lần {attempt+1}/3)")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Groq exception (lần {attempt+1}/3): {e}")
            time.sleep(5)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Public API (async wrappers - chạy blocking calls trong thread pool)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_OCR_PROMPT = """Bạn là chuyên gia OCR ảnh trắc nghiệm.
BỎ QUA: logo, nút bấm, thanh tiến độ, số thứ tự câu, watermark.
CHỈ TRÍCH XUẤT: nội dung câu hỏi và các phương án.
Trả về JSON: {"results": [{"questionText": "...", "aiAns": "A/B/C/D hoặc TF"}]}"""

DEFAULT_REFINE_PROMPT_PREFIX = (
    "Fix OCR errors, remove UI noise. Return JSON: "
    '[{"questionText": "text", "aiAns": "ans"}]. Data: '
)


async def extract_text_from_images(
    image_paths: List[str],
    custom_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Gửi ảnh lên AI provider (Gemini / Groq) để OCR.
    Thực hiện toàn bộ xử lý (kể cả đọc file) trong thread pool.
    """
    cfg = load_config()
    provider = cfg.get("AI_PROVIDER", "gemini")
    prompt_text = custom_prompt or DEFAULT_OCR_PROMPT
    # Filter valid paths early (sync check is fine for metadata only)
    valid_paths = [p for p in image_paths if os.path.exists(p)]

    loop = asyncio.get_running_loop()  # Safer than get_event_loop() in Python 3.10+

    if provider == "gemini":
        api_key = cfg.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.error("Thiếu GEMINI_API_KEY")
            return []

        # Build parts INSIDE executor to avoid blocking event loop with file reads
        def _gemini_task():
            parts: List[Dict[str, Any]] = [{"text": prompt_text}]
            for path in valid_paths:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                parts.append({"inline_data": {"mime_type": "image/webp", "data": b64}})
            return _call_gemini(api_key, parts, "gemini-1.5-flash", 60)

        return await loop.run_in_executor(None, _gemini_task)

    else:  # groq
        api_key = cfg.get("GROQ_API_KEY", "")
        if not api_key:
            logger.error("Thiếu GROQ_API_KEY")
            return []

        def _groq_task():
            content: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            has_images = False
            for path in valid_paths:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{b64}"}})
                has_images = True
            model = "meta-llama/llama-4-scout-17b-16e-instruct" if has_images else "llama-3.1-8b-instant"
            return _call_groq(api_key, content, model, 60)

        return await loop.run_in_executor(None, _groq_task)


async def ask_text_only(prompt: str) -> List[Dict[str, Any]]:
    """Gửi text prompt tới AI provider (không có ảnh)."""
    cfg = load_config()
    provider = cfg.get("AI_PROVIDER", "gemini")
    loop = asyncio.get_running_loop()

    if provider == "gemini":
        api_key = cfg.get("GEMINI_API_KEY", "")
        if not api_key:
            return []
        parts = [{"text": prompt}]
        return await loop.run_in_executor(
            None, _call_gemini, api_key, parts, "gemini-1.5-flash", 60
        )
    else:
        api_key = cfg.get("GROQ_API_KEY", "")
        if not api_key:
            return []
        content = [{"type": "text", "text": prompt}]
        return await loop.run_in_executor(
            None, _call_groq, api_key, content, "llama-3.1-8b-instant", 60
        )


async def test_connection() -> Dict[str, Any]:
    """Kiểm tra kết nối AI provider. Trả về {ok, error}."""
    cfg = load_config()
    provider = cfg.get("AI_PROVIDER", "gemini")
    loop = asyncio.get_running_loop()
    try:
        if provider == "gemini":
            api_key = cfg.get("GEMINI_API_KEY", "")
            if not api_key:
                return {"ok": False, "error": "Thiếu GEMINI_API_KEY"}
            await loop.run_in_executor(
                None, _call_gemini, api_key, [{"text": "Hi"}], "gemini-1.5-flash", 15
            )
            return {"ok": True}
        else:
            api_key = cfg.get("GROQ_API_KEY", "")
            if not api_key:
                return {"ok": False, "error": "Thiếu GROQ_API_KEY"}
            await loop.run_in_executor(
                None, _call_groq, api_key, [{"type": "text", "text": "Hi"}], "llama-3.1-8b-instant", 15
            )
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
