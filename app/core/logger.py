"""
Logger đơn giản - log ra console (màu sắc) và file.
Không cần handler phức tạp.
"""
import logging
import sys
import os
from app.core.config import BASE_DIR

LOG_FILE = os.path.join(BASE_DIR, "server.log")

# ─── Format ───────────────────────────────────────────────────────────────────
_formatter = logging.Formatter(
    fmt="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ─── Logger Setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger("fuoverflow")
logger.setLevel(logging.INFO)

if not logger.handlers:
    # Console handler
    _console = logging.StreamHandler(sys.stdout)
    _console.setFormatter(_formatter)
    logger.addHandler(_console)

    # File handler (debug khi cần)
    try:
        _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        _file_handler.setFormatter(_formatter)
        _file_handler.setLevel(logging.WARNING)   # Chỉ ghi WARNING+ vào file
        logger.addHandler(_file_handler)
    except Exception:
        pass


def safe_print(message: str):
    """In ra console, tránh lỗi UnicodeEncodeError trên Windows."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("ascii", "replace").decode("ascii"))
