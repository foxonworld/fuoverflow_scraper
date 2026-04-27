from abc import ABC, abstractmethod
from typing import List, Optional, Any
from app.domain.models import Subject, Question, Source

class BaseRepository(ABC):
    @abstractmethod
    async def get_all_subjects(self) -> List[Subject]:
        pass

    @abstractmethod
    async def get_subject_by_name(self, name: str) -> Optional[Subject]:
        pass

    @abstractmethod
    async def save_subject(self, subject: Subject) -> None:
        pass

    @abstractmethod
    async def get_sources(self) -> List[Source]:
        pass

    @abstractmethod
    async def save_sources(self, sources: List[Source]) -> None:
        pass
