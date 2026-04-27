"""
Thread-safe global state manager.
Dùng 1 lock duy nhất để kiểm soát trạng thái hệ thống.
"""
import asyncio
import subprocess
import threading
from typing import Optional, List, Any, Dict


class AppState:
    """
    Singleton quản lý tất cả trạng thái runtime.
    Thread-safe cho cả async và sync code.
    """
    _instance: Optional["AppState"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "AppState":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._setup()
        return cls._instance

    def _setup(self):
        # ─── Crawler state ────────────────────────────────────────────────
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[float] = None
        self.workflow_running: bool = False

        # ─── Log buffer (cho WebSocket replay) ───────────────────────────
        self.logs: List[str] = []
        self.active_websockets: List[Any] = []

        # ─── AI state ────────────────────────────────────────────────────
        self.ai_results: Optional[Dict[str, Any]] = None
        self.ai_progress: Dict[str, Any] = {
            "current": 0,
            "total": 0,
            "status": "",
        }

        # ─── Audit state ──────────────────────────────────────────────────
        self.audit_results: Optional[List[Any]] = None

        # ─── Async lock (dùng trong async code) ──────────────────────────
        # KHÔNG tạo Lock ở đây vì asyncio.Lock phải tạo trong event loop.
        self._write_lock: Optional[asyncio.Lock] = None

    def get_write_lock(self) -> asyncio.Lock:
        """Trả về asyncio.Lock dùng chung. Thread-safe lazy init."""
        # Double-checked locking để tránh tạo 2 Lock cùng lúc
        if self._write_lock is None:
            with self._init_lock:
                if self._write_lock is None:
                    self._write_lock = asyncio.Lock()
        return self._write_lock

    @property
    def is_crawler_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def clear_process(self):
        self.process = None
        self.start_time = None
        self.workflow_running = False


# ─── Singleton instance ───────────────────────────────────────────────────────
state = AppState()
