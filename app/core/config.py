"""
Centralized configuration. Đọc từ config.json (không phải .env)
để tương thích với hệ thống cũ dùng Dashboard để cập nhật config.
"""
import os

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR: str = os.path.abspath(os.getcwd())

DATA_FILE: str      = os.path.join(BASE_DIR, "data.js")
CONFIG_FILE: str    = os.path.join(BASE_DIR, "config.json")
IMAGES_DIR: str     = os.path.join(BASE_DIR, "images")
BACKUP_DIR: str     = os.path.join(BASE_DIR, "data", "backups")
DRAFTS_DIR: str     = os.path.join(BASE_DIR, "drafts")
SCRAPER_SCRIPT: str = os.path.join(BASE_DIR, "fuoverflow_scraper_v6.py")

print(f"--- SYSTEM PATHS ---")
print(f"BASE_DIR:  {BASE_DIR}")
print(f"DATA_FILE: {DATA_FILE}")
print(f"DRAFTS:    {DRAFTS_DIR}")
print(f"--------------------")

# ─── App Settings ─────────────────────────────────────────────────────────────
APP_TITLE: str = "FuOverflow Scraper Dashboard"
HOST: str = "0.0.0.0"
PORT: int = 8000
MAX_BACKUPS: int = 20           # Giữ tối đa N bản backup
MAX_LOGS: int = 1000            # Giữ tối đa N dòng log trong RAM
