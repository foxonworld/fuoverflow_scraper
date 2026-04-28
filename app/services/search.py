
import re
import asyncio
from typing import List, Dict, Any
from app.services import storage, ai as ai_svc
from app.utils.helpers import get_source_mapping

async def get_expanded_keywords(query: str) -> List[str]:
    """
    Sử dụng AI để mở rộng từ khóa tìm kiếm từ câu hỏi tự nhiên.
    Ví dụ: "thời gian" -> ["thời gian", "mốc thời gian", "năm", "thế kỷ", "giai đoạn"]
    """
    prompt = f"""
    Bạn là chuyên gia ngôn ngữ. Hãy phân tích câu truy vấn tìm kiếm của người dùng sau đây: "{query}"
    Hãy liệt kê khoảng 5-8 từ khóa (keywords) liên quan nhất (cả tiếng Việt và tiếng Anh nếu cần) để tìm kiếm trong bộ đề thi trắc nghiệm.
    Các từ khóa nên bao gồm cả từ đồng nghĩa, khái niệm bao quát hoặc chi tiết hơn.
    CHỈ TRẢ VỀ danh sách các từ khóa, ngăn cách bằng dấu phẩy. Không giải thích gì thêm.
    Ví dụ: thời gian, mốc thời gian, thế kỷ, năm, giai đoạn, lịch sử.
    """
    try:
        raw_text = await ai_svc.ask_raw_text(prompt)
        if not raw_text:
            return [query]
        keywords = [k.strip().lower() for k in raw_text.split(',') if k.strip()]
        # Đảm bảo từ khóa gốc luôn có trong danh sách
        if query.lower() not in keywords:
            keywords.insert(0, query.lower())
        return keywords
    except Exception:
        return [query]

def highlight_text(text: str, keywords: List[str]) -> str:
    """Highlight các từ khóa trong văn bản bằng thẻ <mark>."""
    if not text: return ""
    highlighted = text
    # Sắp xếp từ khóa theo độ dài giảm dần để tránh highlight chồng lấn lỗi (ví dụ "thời gian" và "thời")
    sorted_keywords = sorted(keywords, key=len, reverse=True)
    
    for kw in sorted_keywords:
        if not kw: continue
        # Sử dụng regex để tìm kiếm không phân biệt hoa thường và highlight
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        highlighted = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", highlighted)
    return highlighted

async def perform_smart_search(subject: str, query: str) -> List[Dict[str, Any]]:
    """
    Thực hiện tìm kiếm thông minh:
    1. Mở rộng từ khóa
    2. Lọc câu hỏi & đáp án
    3. Tính điểm relevance theo trọng số
    4. Highlight kết quả
    """
    # 1. Lấy từ khóa mở rộng
    keywords = await get_expanded_keywords(query)
    
    # 2. Tải dữ liệu
    data = storage.load_quiz_data()
    questions = data.get(subject, [])
    if not questions:
        return []
        
    results = []
    query_lower = query.lower()
    
    for q in questions:
        if not isinstance(q, dict): continue
        
        # Lấy nội dung câu hỏi và các phương án
        text = q.get("questionText", "") or ""
        text_lower = text.lower()
        
        options = q.get("optionsData", {}) or {}
        options_text = " ".join([str(v) for v in options.values()])
        options_lower = options_text.lower()
        
        score = 0
        matched_kws = []
        
        # 3. Tính điểm (Scoring) - Ưu tiên khớp câu truy vấn gốc
        # Khớp trong câu hỏi (Trọng số cao nhất)
        if query_lower in text_lower:
            score += 25
            matched_kws.append(query_lower)
        # Khớp trong phương án
        if query_lower in options_lower:
            score += 20
            if query_lower not in matched_kws: matched_kws.append(query_lower)
            
        # Khớp theo từ khóa mở rộng
        for kw in keywords:
            # Khớp trong câu hỏi
            if kw in text_lower:
                score += 8
                if kw not in matched_kws: matched_kws.append(kw)
            # Khớp trong phương án
            if kw in options_lower:
                score += 5
                if kw not in matched_kws: matched_kws.append(kw)
        
        if score > 0:
            # 4. Highlight và chuẩn bị kết quả
            item = dict(q)
            # Highlight cả câu hỏi và các phương án (nếu là dạng text)
            item["highlightedText"] = highlight_text(text, matched_kws)
            
            if options:
                # Highlight từng phương án
                hi_options = {}
                for k, v in options.items():
                    hi_options[k] = highlight_text(str(v), matched_kws)
                item["highlightedOptions"] = hi_options
                
            item["relevance"] = score
            results.append(item)
            
    # 5. Sắp xếp theo relevance giảm dần
    results.sort(key=lambda x: x["relevance"], reverse=True)
    
    return results[:50]
