from typing import List, Dict, Any, Optional
from app.ai.providers.base import BaseAIProvider
from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.groq import GroqProvider
from app.core.config import settings
from app.core.logging.logger import logger

class AIService:
    def __init__(self, provider_name: Optional[str] = None):
        self.provider_name = provider_name or settings.AI_PROVIDER
        self.provider: BaseAIProvider = self._get_provider()

    def _get_provider(self) -> BaseAIProvider:
        if self.provider_name == "gemini":
            return GeminiProvider(api_key=settings.GEMINI_API_KEY)
        else:
            return GroqProvider(api_key=settings.GROQ_API_KEY)

    async def process_images(self, image_paths: List[str], prompt: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            return await self.provider.extract_text_from_images(image_paths, prompt)
        except Exception as e:
            logger.error(f"AI Service Error (Images): {e}")
            return []

    async def process_text(self, prompt: str) -> List[Dict[str, Any]]:
        try:
            return await self.provider.ask_text_only(prompt)
        except Exception as e:
            logger.error(f"AI Service Error (Text): {e}")
            return []
