import subprocess
import os
import psutil
import asyncio
from typing import Optional, List, Callable
from app.core.config import settings
from app.core.logging.logger import logger
from app.core.state import state

class CrawlerService:
    def __init__(self):
        self.script_path = os.path.join(settings.BASE_DIR, "fuoverflow_scraper_v6.py")
        self.process: Optional[subprocess.Popen] = None

    async def start_crawl(self, url: str, subject: str, source_id: str, log_callback: Optional[Callable] = None):
        cmd = [
            "python", "-u", self.script_path,
            "--url", url,
            "--subject", subject,
            "--source_id", source_id
        ]
        
        logger.info(f"Starting crawl for {subject} ({source_id})")
        
        # Run in executor to not block event loop
        self.process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            bufsize=0
        )
        
        state.process = self.process
        
        try:
            while True:
                line = await asyncio.get_event_loop().run_in_executor(None, self.process.stdout.readline)
                if not line:
                    break
                msg = line.decode("utf-8", errors="replace").strip()
                if msg and log_callback:
                    await log_callback(msg)
            
            self.process.wait()
        except Exception as e:
            logger.error(f"Crawler error: {e}")
        finally:
            self.process = None
            state.process = None

    async def stop_crawl(self):
        if not state.process:
            return
        
        logger.info("Stopping crawler processes...")
        try:
            parent = psutil.Process(state.process.pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
            
            _, alive = psutil.wait_procs(parent.children() + [parent], timeout=5)
            for p in alive:
                p.kill()
        except Exception as e:
            logger.error(f"Error stopping crawler: {e}")
            if state.process:
                state.process.kill()
        finally:
            state.process = None
