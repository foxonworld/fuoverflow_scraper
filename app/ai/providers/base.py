from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseAIProvider(ABC):
    @abstractmethod
    async def extract_text_from_images(
        self, 
        image_paths: List[str], 
        prompt: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def ask_text_only(self, prompt: str) -> List[Dict[str, Any]]:
        pass
