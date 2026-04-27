from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_crawler_service, get_task_manager
from app.services.crawler_service import CrawlerService
from app.workers.task_manager import TaskManager

router = APIRouter()

@router.post("/start")
async def start_crawl(
    url: str, 
    subject: str, 
    source_id: str,
    crawler: CrawlerService = Depends(get_crawler_service),
    task_mgr: TaskManager = Depends(get_task_manager)
):
    # Add crawl job to worker
    job_id = task_mgr.add_job(
        "crawler", 
        crawler.start_crawl, 
        url, subject, source_id
    )
    return {"job_id": job_id, "message": "Crawl started in background"}

@router.post("/stop")
async def stop_crawl(crawler: CrawlerService = Depends(get_crawler_service)):
    await crawler.stop_crawl()
    return {"message": "Crawler stopped"}
