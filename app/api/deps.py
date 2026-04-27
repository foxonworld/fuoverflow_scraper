from app.infrastructure.file_repository import FileRepository
from app.services.ai_service import AIService
from app.services.crawler_service import CrawlerService
from app.workers.task_manager import task_manager

def get_repository():
    return FileRepository()

def get_ai_service():
    return AIService()

def get_crawler_service():
    return CrawlerService()

def get_task_manager():
    return task_manager
