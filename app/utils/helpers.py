"""
Helper utilities: Tính điểm sort source, mapping source ID → tên đẹp.
"""
import re
from typing import Dict


def get_source_score(display_name: str) -> int:
    """
    Tính điểm sắp xếp cho một tên môn học / mã đề.
    Quy tắc: năm cao hơn > kỳ fa > su > sp > có final/fe > không phải re
    """
    s = display_name.lower()

    year = 0
    sem_match = re.search(r"(sp|fa|su)\s*(\d{2,4})", s)
    if sem_match:
        year = int(sem_match.group(2))
    else:
        y4 = re.search(r"(20\d{2})", s)
        if y4:
            year = int(y4.group(1))
    if year > 2000:
        year -= 2000

    sem_priority = 0
    if "fa" in s:
        sem_priority = 3
    elif "su" in s:
        sem_priority = 2
    elif "sp" in s:
        sem_priority = 1

    type_bonus = 5 if ("fe" in s or "final" in s) else 0
    is_re = -1 if "re" in s else 0

    return year * 1000 + sem_priority * 100 + type_bonus + is_re


def get_source_mapping(config: Dict) -> Dict[str, str]:
    """Trả về {source_id: display_name} từ config.json."""
    sources = config.get("SOURCES", [])
    return {s.get("id"): s.get("name") for s in sources if s.get("id") and s.get("name")}
