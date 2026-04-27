from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from app.api.deps import get_ai_service, get_task_manager, get_repository
from app.services.ai_service import AIService
from app.workers.task_manager import TaskManager
from app.repositories.base import BaseRepository

router = APIRouter()

@router.post("/classify/{name}")
async def classify_subject(
    name: str, 
    source_id: Optional[str] = None,
    ai_service: AIService = Depends(get_ai_service),
    repo: BaseRepository = Depends(get_repository),
    task_mgr: TaskManager = Depends(get_task_manager)
):
    # Logic for classification worker
    async def classification_task(subject_name, sid):
        subject = await repo.get_subject_by_name(subject_name)
        if not subject: return
        
        to_process = []
        for q in subject.questions:
            if sid and q.source_id != sid: continue
            to_process.append(q)
        
        # Batch processing with AI Service
        # ...
        pass

    job_id = task_mgr.add_job("classification", classification_task, name, source_id)
    return {"job_id": job_id, "message": "Classification started"}

@router.post("/clean/{name}/scan")
async def start_clean_scan(
    name: str,
    task_mgr: TaskManager = Depends(get_task_manager)
):
    # Logic for AI clean worker
    return {"message": "AI Clean scan started"}
