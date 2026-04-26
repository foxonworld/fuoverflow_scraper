import json
import os

# Đường dẫn file config
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

_config = load_config()

# Cấu hình được nạp từ file JSON
USERNAME = _config.get("USERNAME", "")
PASSWORD = _config.get("PASSWORD", "")
GEMINI_API_KEYS = _config.get("GEMINI_API_KEYS", [])
SUBJECT_NAME = _config.get("SUBJECT_NAME", "Unknown")
POST_URLS = _config.get("POST_URLS", [])
IMAGES_DIR = _config.get("IMAGES_DIR", "images")
IMAGES_WORD_DIR = _config.get("IMAGES_WORD_DIR", "images_word")
ENABLE_WORD_EXPORT = _config.get("ENABLE_WORD_EXPORT", False)
ENABLE_OCR_CLASSIFY = _config.get("ENABLE_OCR_CLASSIFY", False)
PAGE_TIMEOUT = _config.get("PAGE_TIMEOUT", 15)
REQUEST_DELAY = _config.get("REQUEST_DELAY", 5)

