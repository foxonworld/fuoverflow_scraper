import asyncio
import uuid
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from app.core.logging.logger import logger

@dataclass
class Job:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "generic"
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED
    progress: int = 0
    total: int = 0
    message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    logs: List[str] = field(default_factory=list)

class TaskManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.jobs: Dict[str, Job] = {}
        self._worker_task = None

    def start(self):
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("TaskManager worker started.")

    async def _worker(self):
        while True:
            job_id, coro_func, args, kwargs = await self.queue.get()
            job = self.jobs[job_id]
            job.status = "RUNNING"
            job.updated_at = datetime.now()
            
            try:
                logger.info(f"Executing job {job_id} ({job.type})")
                await coro_func(*args, **kwargs)
                job.status = "COMPLETED"
                job.progress = job.total
            except Exception as e:
                logger.error(f"Job {job_id} failed: {e}")
                job.status = "FAILED"
                job.message = str(e)
            finally:
                job.updated_at = datetime.now()
                self.queue.task_done()

    def add_job(self, job_type: str, coro_func: Callable, *args, **kwargs) -> str:
        job = Job(type=job_type)
        self.jobs[job.id] = job
        self.queue.put_nowait((job.id, coro_func, args, kwargs))
        return job.id

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[Job]:
        return list(self.jobs.values())

task_manager = TaskManager()
