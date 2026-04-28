"""
Storage service: Đọc / ghi data.js và config.json.
Yêu cầu bắt buộc:
  - Atomic write (.tmp → os.replace)
  - Backup tự động trước khi ghi
  - Async Lock để tránh race condition
"""
import asyncio
import json
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import (
    BACKUP_DIR,
    CONFIG_FILE,
    DATA_FILE,
    DRAFTS_DIR,
    MAX_BACKUPS,
)
from app.core.logger import logger
from app.core.state import state


# ─────────────────────────────────────────────────────────────────────────────
# Helpers nội bộ
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dirs():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(DRAFTS_DIR, exist_ok=True)


def _prune_backups():
    """Xóa backup cũ, chỉ giữ lại MAX_BACKUPS bản gần nhất."""
    files = sorted(
        [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith(".js")],
        key=os.path.getctime,
    )
    while len(files) > MAX_BACKUPS:
        os.remove(files.pop(0))


# ─────────────────────────────────────────────────────────────────────────────
# Quiz Data (data.js)
# ─────────────────────────────────────────────────────────────────────────────

def load_quiz_data() -> Dict[str, Any]:
    """Read data.js and parse JSON. Sync function, safe to call from anywhere."""
    if not os.path.exists(DATA_FILE):
        return {}

    for attempt in range(5):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if "quizData =" in content:
                json_str = content.split("quizData =", 1)[1].strip()
                if json_str.endswith(";"):
                    json_str = json_str[:-1]
                
                if not json_str:
                    logger.error("Dữ liệu sau 'quizData =' bị rỗng!")
                    return {}
                
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as je:
                    logger.error(f"Lỗi parse JSON: {je}. Nội dung bắt đầu bằng: {json_str[:100]}...")
                    raise je
        except (json.JSONDecodeError, IndexError) as e:
            if attempt == 4:
                logger.error(f"Không đọc được data.js sau 5 lần: {e}")
            time.sleep(0.5)  # Tăng thời gian chờ một chút
        except Exception as e:
            logger.error(f"Lỗi đọc data.js: {e}")
            break
    return {}


async def save_quiz_data(data: Dict[str, Any], backup: bool = True) -> bool:
    """
    Ghi data.js. Hoàn toàn an toàn:
      1. asyncio.Lock – không race condition
      2. Backup – tự động trước khi ghi
      3. Atomic write: ghi .tmp rồi os.replace
      4. Tất cả I/O chạy trong thread pool – không block event loop
    """
    loop = asyncio.get_running_loop()

    def _do_write():
        _ensure_dirs()

        # 1. Backup
        if backup and os.path.exists(DATA_FILE):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(BACKUP_DIR, f"data_{ts}.js")
            shutil.copy2(DATA_FILE, bak)
            _prune_backups()

        # 2. Serialize to string first (so JSON errors don't corrupt file)
        content = f"const quizData = {json.dumps(data, ensure_ascii=False)};"

        # 3. Atomic write: write to .tmp then replace
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())  # Ensure bytes reach disk before replace

        os.replace(tmp, DATA_FILE)

    async with state.get_write_lock():
        try:
            await loop.run_in_executor(None, _do_write)
            return True
        except Exception as e:
            logger.error(f"Lỗi ghi data.js: {e}")
            tmp = DATA_FILE + ".tmp"
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            return False


# ─────────────────────────────────────────────────────────────────────────────
# App Config (config.json)
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> Dict[str, Any]:
    """Đọc config.json. Trả về dict rỗng nếu lỗi."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Lỗi đọc config.json: {e}")
        return {}


def save_config(data: Dict[str, Any]) -> bool:
    """Ghi config.json (atomic write, không cần backup vì config nhỏ)."""
    tmp = CONFIG_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)
        return True
    except Exception as e:
        logger.error(f"Lỗi ghi config.json: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Backup helpers
# ─────────────────────────────────────────────────────────────────────────────

def create_named_backup(label: str) -> Optional[str]:
    """Tạo backup thủ công với tên cụ thể (dùng trước khi apply audit)."""
    if not os.path.exists(DATA_FILE):
        return None
    _ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(BACKUP_DIR, f"data_{label}_{ts}.js")
    shutil.copy2(DATA_FILE, bak)
    _prune_backups()
    return bak


def list_backups() -> List[str]:
    _ensure_dirs()
    return sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith(".js")],
        key=lambda x: os.path.getctime(os.path.join(BACKUP_DIR, x)),
        reverse=True,
    )


def restore_backup(filename: str) -> bool:
    """Restore backup using atomic write to avoid corruption."""
    path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(path):
        return False
    try:
        # Atomic: copy to .tmp first, then replace
        tmp = DATA_FILE + ".restore.tmp"
        shutil.copy2(path, tmp)
        os.replace(tmp, DATA_FILE)
        return True
    except Exception as e:
        logger.error(f"Lỗi restore: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Draft helpers (cho audit / smart refine)
# ─────────────────────────────────────────────────────────────────────────────

def save_draft(subject: str, tool_type: str, data: Any):
    _ensure_dirs()
    path = os.path.join(DRAFTS_DIR, f"{subject}_{tool_type}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_draft(subject: str, tool_type: str) -> Optional[Any]:
    path = os.path.join(DRAFTS_DIR, f"{subject}_{tool_type}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def delete_draft(subject: str, tool_type: str):
    path = os.path.join(DRAFTS_DIR, f"{subject}_{tool_type}.json")
    if os.path.exists(path):
        os.remove(path)
