import json
import os
import re
import asyncio
import aiofiles
from typing import List, Optional, Dict, Any
from app.repositories.base import BaseRepository
from app.domain.models import Subject, Question, Source, SubjectMetadata
from app.core.config import settings
from app.core.logging.logger import logger

class FileRepository(BaseRepository):
    def __init__(self):
        self.data_file = settings.DATA_FILE
        self.config_file = settings.CONFIG_FILE
        self._lock = asyncio.Lock()

    async def _read_data_js(self) -> Dict[str, Any]:
        if not os.path.exists(self.data_file):
            return {}
        
        async with self._lock:
            try:
                async with aiofiles.open(self.data_file, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                    content = content.strip()
                    if "quizData =" in content:
                        json_str = content.split("quizData =", 1)[1].strip()
                        if json_str.endswith(";"):
                            json_str = json_str[:-1]
                        return json.loads(json_str)
            except Exception as e:
                logger.error(f"Error reading data.js: {e}")
                return {}
        return {}

    async def _write_data_js(self, data: Dict[str, Any]) -> None:
        tmp_file = self.data_file + ".tmp"
        async with self._lock:
            try:
                content = f"const quizData = {json.dumps(data, ensure_ascii=False)};"
                async with aiofiles.open(tmp_file, mode='w', encoding='utf-8') as f:
                    await f.write(content)
                
                if os.path.exists(self.data_file):
                    os.replace(tmp_file, self.data_file)
                else:
                    os.rename(tmp_file, self.data_file)
            except Exception as e:
                logger.error(f"Error saving data.js: {e}")
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)

    async def get_all_subjects(self) -> List[Subject]:
        raw_data = await self._read_data_js()
        subjects = []
        metadata = raw_data.get("_metadata", {})
        
        for name, questions_raw in raw_data.items():
            if name == "_metadata":
                continue
            
            questions = [Question(**q) for q in questions_raw if isinstance(q, dict)]
            sub_meta_raw = metadata.get(name, {})
            sub_meta = SubjectMetadata(**sub_meta_raw) if sub_meta_raw else None
            
            subjects.append(Subject(name=name, questions=questions, metadata=sub_meta))
        return subjects

    async def get_subject_by_name(self, name: str) -> Optional[Subject]:
        raw_data = await self._read_data_js()
        if name not in raw_data:
            return None
        
        questions = [Question(**q) for q in raw_data[name] if isinstance(q, dict)]
        metadata = raw_data.get("_metadata", {}).get(name, {})
        sub_meta = SubjectMetadata(**metadata) if metadata else None
        
        return Subject(name=name, questions=questions, metadata=sub_meta)

    async def save_subject(self, subject: Subject) -> None:
        raw_data = await self._read_data_js()
        raw_data[subject.name] = [q.model_dump() for q in subject.questions]
        
        if subject.metadata:
            if "_metadata" not in raw_data:
                raw_data["_metadata"] = {}
            raw_data["_metadata"][subject.name] = subject.metadata.model_dump()
            
        await self._write_data_js(raw_data)

    async def get_sources(self) -> List[Source]:
        if not os.path.exists(self.config_file):
            return []
        
        try:
            async with aiofiles.open(self.config_file, mode='r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)
                sources_raw = data.get("SOURCES", [])
                return [Source(**s) for s in sources_raw]
        except Exception as e:
            logger.error(f"Error reading config.json: {e}")
            return []

    async def save_sources(self, sources: List[Source]) -> None:
        data = {}
        if os.path.exists(self.config_file):
            async with aiofiles.open(self.config_file, mode='r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)
        
        data["SOURCES"] = [s.model_dump() for s in sources]
        
        tmp_file = self.config_file + ".tmp"
        async with aiofiles.open(tmp_file, mode='w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=4, ensure_ascii=False))
        os.replace(tmp_file, self.config_file)
