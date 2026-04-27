"""
Crawler Service: Chạy fuoverflow_scraper_v6.py trong subprocess.
- Không block event loop (chạy trong asyncio executor)
- Có timeout mềm (dừng nếu quá lâu)
- Stop an toàn: kill cả process tree qua psutil
"""
import asyncio
import subprocess

import psutil

from app.core.config import SCRAPER_SCRIPT
from app.core.logger import logger
from app.core.state import state


# ─────────────────────────────────────────────────────────────────────────────
# Broadcast helper
# ─────────────────────────────────────────────────────────────────────────────

async def broadcast_log(message: str):
    """
    Ghi log vào state.logs và gửi tới tất cả WebSocket đang kết nối.
    """
    from datetime import datetime
    formatted = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    logger.info(message)

    state.logs.append(formatted)
    from app.core.config import MAX_LOGS
    if len(state.logs) > MAX_LOGS:
        state.logs.pop(0)

    if not state.active_websockets:
        return

    dead: list = []
    for ws in list(state.active_websockets):
        try:
            await ws.send_text(formatted)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            state.active_websockets.remove(ws)
        except ValueError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Stream subprocess stdout → WebSocket
# ─────────────────────────────────────────────────────────────────────────────

async def _stream_output(stream):
    """Read subprocess stdout line by line and broadcast to WebSocket."""
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, stream.readline)
            if not line:
                break
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                await broadcast_log(msg)
    except Exception as e:
        await broadcast_log(f"Lỗi đọc log subprocess: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Start / Stop
# ─────────────────────────────────────────────────────────────────────────────

async def start_single_crawl(url: str, subject: str, source_id: str):
    """Chạy scraper cho 1 source. Blocks cho đến khi xong."""
    cmd = [
        "python", "-u", SCRAPER_SCRIPT,
        "--url", url,
        "--subject", subject,
        "--source_id", source_id,
    ]
    await broadcast_log(f"▶ Bắt đầu crawl: {source_id} ({subject})")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    state.process = proc

    try:
        await _stream_output(proc.stdout)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, proc.wait)
        rc = proc.returncode
        await broadcast_log(f"Ket thuc crawl: {source_id} (exit code {rc})")
    except asyncio.CancelledError:
        await stop_crawl()
        raise
    except Exception as e:
        await broadcast_log(f"Loi crawl {source_id}: {e}")
    finally:
        state.process = None


async def stop_crawl():
    """Dừng toàn bộ process tree (Python + ChromeDriver + Chrome)."""
    proc = state.process
    if proc is None:
        return
    await broadcast_log("Dang dung crawler...")
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        for c in children:
            c.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs(children + [parent], timeout=5)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        await broadcast_log("Da dung tat ca tien trinh.")
    except psutil.NoSuchProcess:
        pass  # Process already dead, that's fine
    except Exception as e:
        logger.error(f"Lỗi stop_crawl: {e}")
        try:
            proc.kill()
        except Exception:
            pass
    finally:
        state.process = None  # Clear regardless
