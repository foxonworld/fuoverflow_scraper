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
    """Parse JSON trả về từ AI, dùng regex để tìm khối JSON nếu có text thừa."""
    import re
    text = text.strip()
    
    # 1. Thử parse trực tiếp trước
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list): return parsed
        return parsed.get("results", [])
    except:
        pass

    # 2. Tìm khối JSON trong markdown hoặc text tự do
    # Tìm khối [ ... ]
    match_list = re.search(r'\[\s*{.*}\s*\]', text, re.DOTALL)
    if match_list:
        try:
            return json.loads(match_list.group(0))
        except:
            pass
            
    # Tìm khối { "results": [ ... ] }
    match_obj = re.search(r'\{\s*"results"\s*:.*\]\s*\}', text, re.DOTALL)
    if match_obj:
        try:
            parsed = json.loads(match_obj.group(0))
            return parsed.get("results", [])
        except:
            pass

    # 3. Fallback: Split by ``` and try each part
    parts = re.split(r'```(?:json)?', text)
    for part in parts:
        part = part.strip()
        if not part: continue
        try:
            parsed = json.loads(part)
            if isinstance(parsed, list): return parsed
            if isinstance(parsed, dict) and "results" in parsed:
                return parsed["results"]
        except:
            continue

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Gemini
# ─────────────────────────────────────────────────────────────────────────────

def _get_retry_delay(resp: requests.Response, attempt: int, default: int = 10) -> int:
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except Exception:
            pass
    return default * (attempt + 1)


def _call_gemini(
    api_key: str,
    parts: List[Dict[str, Any]],
    model: str = "gemini-3.1-flash-lite-preview",
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Gọi Gemini API đồng bộ (chạy trong thread). Retry 3 lần."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": parts}]
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code in (429, 503):
                wait = _get_retry_delay(resp, attempt)
                logger.warning(f"Gemini {resp.status_code}, đợi {wait}s (lần {attempt+1}/3)...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                logger.error(f"Gemini lỗi {resp.status_code}: {resp.text[:200]}")
                return []
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


def _call_gemini_raw(
    api_key: str,
    parts: List[Dict[str, Any]],
    model: str = "gemini-3.1-flash-lite-preview",
    timeout: int = 60,
) -> str:
    """Gọi Gemini API và trả về văn bản thô (không parse JSON)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": parts}]}
    headers = {"Content-Type": "application/json"}
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code in (429, 503):
                wait = _get_retry_delay(resp, attempt)
                logger.warning(f"Gemini raw {resp.status_code}, đợi {wait}s (lần {attempt+1}/3)...")
                time.sleep(wait)
                continue
            if resp.status_code == 200:
                data = resp.json()
                if "candidates" in data:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                logger.error(f"Gemini raw phản hồi lạ: {str(data)[:200]}")
                return ""
            logger.error(f"Gemini raw lỗi {resp.status_code}: {resp.text[:200]}")
            return ""
        except requests.Timeout:
            logger.warning(f"Gemini raw timeout (lần {attempt+1}/3)")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Gemini raw exception (lần {attempt+1}/3): {e}")
            time.sleep(5)
    return ""


async def ask_raw_text(prompt: str) -> str:
    """Gửi prompt và nhận về văn bản thô từ AI provider (hỗ trợ chuyển đổi provider)."""
    cfg = load_config()
    provider = cfg.get("AI_PROVIDER", "gemini")
    loop = asyncio.get_running_loop()
    
    # Ưu tiên lấy key theo provider đã chọn
    gemini_key = cfg.get("GEMINI_API_KEY", "").strip()
    groq_key = cfg.get("GROQ_API_KEY", "").strip()

    async def _try_call(p, k):
        if p == "gemini" and k:
            return await loop.run_in_executor(None, _call_gemini_raw, k, [{"text": prompt}])
        elif p == "groq" and k:
            # Groq không có hàm raw riêng, dùng hàm list rồi lấy text
            res = await loop.run_in_executor(None, _call_groq, k, [{"type": "text", "text": prompt}])
            if res and isinstance(res, list) and len(res) > 0:
                return str(res[0])
            # Nếu Groq trả về chuỗi trực tiếp (tùy version _parse_json_result)
            return ""
        return ""

    # Thử provider chính
    primary_key = gemini_key if provider == "gemini" else groq_key
    result = await _try_call(provider, primary_key)
    
    # Nếu thất bại và có provider phụ, thử fallback
    if not result:
        fallback_provider = "groq" if provider == "gemini" else "gemini"
        fallback_key = groq_key if provider == "gemini" else gemini_key
        if fallback_key:
            logger.info(f"🔄 Switching to fallback provider: {fallback_provider}")
            result = await _try_call(fallback_provider, fallback_key)
            
    return result or ""


# ─────────────────────────────────────────────────────────────────────────────
# Groq
# ─────────────────────────────────────────────────────────────────────────────

def _call_groq(
    api_key: str,
    content: List[Dict[str, Any]],
    model: str = "llama-3.1-8b-instant",
    timeout: int = 60,
) -> List[Dict[str, Any]]:
    """Gọi Groq API đồng bộ (chạy trong thread). Retry 3 lần. Không dùng json_object mode."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        # KHÔNG dùng response_format json_object - Groq bị lỗi json_validate_failed
        # Prompt sẽ yêu cầu JSON format, ta parse kết quả
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if resp.status_code in (429, 503):
                msg = resp.json().get("error", {}).get("message", "")
                import re
                m = re.search(r"try again in ([\d\.]+)s", msg)
                wait = float(m.group(1)) + 1 if m else _get_retry_delay(resp, attempt, default=15)
                logger.warning(f"Groq rate limit, đợi {wait}s (lần {attempt+1}/3)...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                logger.error(f"Groq lỗi {resp.status_code}: {err_msg}")
                return []
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
            return _call_gemini(api_key, parts, "gemini-3.1-flash-lite-preview", 60)

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
            # Use same model regardless (Groq's llama-3.1 is good for both)
            model = "llama-3.1-8b-instant"
            return _call_groq(api_key, content, model, 60)

        return await loop.run_in_executor(None, _groq_task)


async def ask_text_only(prompt: str) -> List[Dict[str, Any]]:
    """Gửi text prompt tới AI provider. Ưu tiên Groq nếu có (khác rate limits)."""
    cfg = load_config()
    loop = asyncio.get_running_loop()
    
    # Intelligent provider selection: prefer Groq if key exists
    groq_key = cfg.get("GROQ_API_KEY", "").strip()
    gemini_key = cfg.get("GEMINI_API_KEY", "").strip()
    
    if groq_key:
        # Try Groq first (better for text, different rate limits)
        content = [{"type": "text", "text": prompt}]
        result = await loop.run_in_executor(
            None, _call_groq, groq_key, content, "llama-3.1-8b-instant", 60
        )
        if result:
            return result
        # Fall back to Gemini if Groq fails
        logger.warning("Groq failed, falling back to Gemini...")
        if not gemini_key:
            return []
        parts = [{"text": prompt}]
        return await loop.run_in_executor(
            None, _call_gemini, gemini_key, parts, "gemini-3.1-flash-lite-preview", 60
        )
    else:
        # No Groq, use Gemini
        if not gemini_key:
            return []
        parts = [{"text": prompt}]
        return await loop.run_in_executor(
            None, _call_gemini, gemini_key, parts, "gemini-3.1-flash-lite-preview", 60
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
                None, _call_gemini, api_key, [{"text": "Hi"}], "gemini-3.1-flash-lite-preview", 15
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
