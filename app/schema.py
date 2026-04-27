"""
Tất cả Pydantic Models của hệ thống.
Gộp từ domain/models.py và schemas/ cũ.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


# ─── Domain Models ────────────────────────────────────────────────────────────

class Question(BaseModel):
    id: Optional[str] = None
    att_id: Optional[str] = None
    img: str = ""
    ans: str = "?"
    type: str = "MCQ"           # "MCQ" | "TF"
    source_id: Optional[str] = None
    is_audited: bool = False
    is_smart_audited: bool = False
    questionText: Optional[str] = None
    aiAns: Optional[str] = None

    class Config:
        extra = "allow"         # cho phép thêm field lạ để không crash khi data cũ có field thừa


class Source(BaseModel):
    id: str
    name: str
    url: str
    subject: str
    enabled: bool = True


# ─── Config Model ─────────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    USERNAME: str = ""
    PASSWORD: str = ""
    AI_PROVIDER: str = "gemini"
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    SOURCES: List[Dict[str, Any]] = []
    IMAGES_DIR: str = "images"
    IMAGES_WORD_DIR: str = "images_word"
    ENABLE_WORD_EXPORT: bool = False
    ENABLE_OCR_CLASSIFY: bool = False
    PAGE_TIMEOUT: int = 60
    REQUEST_DELAY: int = 2


# ─── API Request / Response Schemas ───────────────────────────────────────────

class AuditRequest(BaseModel):
    urls: List[str]


class StartCrawlerRequest(BaseModel):
    source_ids: List[str] = []
    options: Dict[str, bool] = {}


class BulkDeleteRequest(BaseModel):
    ids: List[str]


class UpdateItemRequest(BaseModel):
    ans: Optional[str] = None
    questionText: Optional[str] = None
    type: Optional[str] = None

    class Config:
        extra = "allow"
