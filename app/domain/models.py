from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class Question(BaseModel):
    id: Optional[str] = None
    att_id: Optional[str] = None
    img: str
    ans: str = "?"
    type: str = "MCQ"
    source_id: Optional[str] = None
    is_audited: bool = False
    is_smart_audited: bool = False
    questionText: Optional[str] = None
    aiAns: Optional[str] = None
    explanation: Optional[str] = None

class SubjectMetadata(BaseModel):
    hidden: bool = False
    last_updated: Optional[datetime] = None

class Subject(BaseModel):
    name: str
    questions: List[Question] = []
    metadata: Optional[SubjectMetadata] = None

class Source(BaseModel):
    id: str
    name: str
    url: str
    subject: str
    enabled: bool = True

class SystemStats(BaseModel):
    total_subjects: int
    total_questions: int
    answer_distribution: Dict[str, int]
