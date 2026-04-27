import asyncio
import json
import os
import subprocess
import time
import psutil  # Dùng để quản lý process tree
import shutil
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
import requests
import re
import warnings

# Ẩn các cảnh báo khó chịu từ Torch và EasyOCR
warnings.filterwarnings("ignore", category=UserWarning)
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE" # Tránh lỗi conflict thư viện trên Windows

# Khởi tạo Local OCR (Chỉ tải model khi dùng lần đầu)
reader = None

def safe_print(message: str):
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode('ascii', 'replace').decode('ascii'))

def get_ocr_reader():
    global reader
    if reader is None:
        try:
            import easyocr
            safe_print("⏳ Loading EasyOCR Model (Vietnamese/English)...")
            reader = easyocr.Reader(['vi', 'en'], gpu=False) 
            safe_print("✅ EasyOCR Ready!")
        except Exception as e:
            safe_print(f"❌ Error loading EasyOCR: {str(e)}")
            return None
    return reader

def local_ocr_extract(image_path: str):
    """Sử dụng EasyOCR quét cục bộ trên máy"""
    ocr = get_ocr_reader()
    if not ocr: return ""
    try:
        results = ocr.readtext(image_path, detail=0)
        return " ".join(results)
    except Exception as e:
        safe_print(f"OCR Error for {image_path}: {e}")
        return ""

app = FastAPI(title="FuOverflow Scraper Dashboard")

# Khóa để tránh race condition khi nhấn Start nhiều lần
start_lock = asyncio.Lock()
workflow_lock = asyncio.Lock()

# Các route phục vụ file HTML trực tiếp
@app.get("/")
@app.get("/admin.html")
async def read_admin():
    return FileResponse("admin.html")

@app.get("/index.html")
async def read_index():
    return FileResponse("index.html")

# --- Static Routing for Website compatibility ---
@app.get("/data.js")
async def get_data_js():
    return FileResponse("data.js")

@app.get("/dataquiz.js")
async def get_dataquiz_js():
    if os.path.exists("dataquiz.js"):
        return FileResponse("dataquiz.js")
    from fastapi import Response
    return Response(content="const quizletData = {};", media_type="application/javascript")

@app.get("/sw.js")
async def get_sw_js():
    return FileResponse("sw.js", media_type="application/javascript")

@app.get("/manifest.json")
async def get_manifest():
    return FileResponse("manifest.json")

@app.get("/icon.svg")
async def get_icon():
    return FileResponse("icon.svg")

# Mount images folder directly at /images for index.html
if os.path.exists("images"):
    app.mount("/images", StaticFiles(directory="images"), name="images")

# Mount everything else at /static
app.mount("/static", StaticFiles(directory="."), name="static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Data Manager Endpoints ---
def get_quiz_data():
    DATA_FILE = "data.js"
    if not os.path.exists(DATA_FILE):
        return {}
    
    # Retry mechanism to handle concurrent writes
    for attempt in range(5):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if "quizData =" in content:
                    json_str = content.split("quizData =", 1)[1].strip()
                    if json_str.endswith(";"):
                        json_str = json_str[:-1]
                    return json.loads(json_str)
        except (json.JSONDecodeError, IndexError) as e:
            if attempt == 4: print(f"Error reading data.js after 5 attempts: {e}")
            time.sleep(0.5) # Wait for other process to finish writing
        except Exception as e:
            print(f"Unexpected error reading data.js: {e}")
            break
    return {}

def save_quiz_data(data):
    # Atomic write to prevent corruption
    tmp_file = "data.js.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(f"const quizData = {json.dumps(data, ensure_ascii=False)};")
        
        # Replace the original file with the new one atomically
        if os.path.exists("data.js"):
            os.replace(tmp_file, "data.js")
        else:
            os.rename(tmp_file, "data.js")
    except Exception as e:
        print(f"Error saving data.js: {e}")
        if os.path.exists(tmp_file):
            os.remove(tmp_file)

@app.get("/api/data/subjects")
async def list_subjects():
    data = get_quiz_data()
    subjects = []
    metadata = data.get("_metadata", {})
    
    for name, questions in data.items():
        if name == "_metadata": continue
        if not isinstance(questions, list): continue
        
        subjects.append({
            "name": name,
            "count": len(questions),
            "last_updated": datetime.now().isoformat() 
        })
    return {"subjects": subjects, "metadata": metadata}

@app.delete("/api/data/subjects/{name}")
async def delete_subject(name: str):
    data = get_quiz_data()
    if name in data:
        # Xóa khỏi JSON
        del data[name]
        save_quiz_data(data)
        
        # Xóa folder ảnh tương ứng (nếu có)
        import shutil
        subject_dir = os.path.join("images", name.lower())
        if os.path.exists(subject_dir):
            shutil.rmtree(subject_dir)
            
        return {"message": f"Deleted subject {name}"}
    raise HTTPException(status_code=404, detail="Subject not found")

@app.get("/api/data/subjects/{name}")
async def get_subject_data(name: str, page: int = 1, limit: int = 12, filter_type: str = "all", source_id: str = ""):
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    questions = data[name]
    
    # Tự động sửa lỗi thiếu ID cho tất cả các câu trong môn này
    import uuid
    id_changed = False
    for q in questions:
        if isinstance(q, dict) and not q.get("id"):
            q["id"] = str(uuid.uuid4())[:8]
            id_changed = True
    if id_changed:
        save_quiz_data(data)
    
    processed = []
    for idx, q in enumerate(questions):
        # Filter by source_id if provided
        if source_id and q.get("source_id") != source_id:
            continue
            
        img_path = q.get("img", "")
        img_exists = os.path.exists(img_path) if img_path else False
        
        error = None
        if not img_path: error = "missing_image"
        elif not img_exists: error = "image_not_found"
        elif not q.get("ans"): error = "missing_answer"
        
        q_item = {**q, "original_index": idx, "error": error}
        
        if filter_type == "error" and not error: continue
        if filter_type == "valid" and error: continue
        
        processed.append(q_item)
    
    # Phân trang
    total = len(processed)
    start = (page - 1) * limit
    end = start + limit
    
    return {
        "items": processed[start:end],
        "total": total,
        "page": page,
        "limit": limit,
        "sources": list(set(q.get("source_id") for q in questions if q.get("source_id")))
    }

@app.delete("/api/data/subjects/{name}/items/{item_id}")
async def delete_quiz_item(name: str, item_id: str):
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    # Tìm và xóa theo ID
    new_list = [q for q in data[name] if str(q.get("id")) != item_id]
    if len(new_list) < len(data[name]):
        data[name] = new_list
        save_quiz_data(data)
        
        # Cập nhật kết quả AI: Loại bỏ ID vừa xóa khỏi danh sách trùng lặp
        if state.ai_results:
            target_id = str(item_id)
            state.ai_results['exact'] = [p for p in state.ai_results.get('exact', []) if str(p[0]) != target_id and str(p[1]) != target_id]
            state.ai_results['fuzzy'] = [p for p in state.ai_results.get('fuzzy', []) if str(p[0]) != target_id and str(p[1]) != target_id]
            
        return {"message": "Deleted successfully"}
    
    raise HTTPException(status_code=400, detail="Item not found or already deleted")

@app.post("/api/ai/clean/{name}/reanalyze")
async def reanalyze_ai_results(name: str):
    """Tính toán lại danh sách trùng lặp từ dữ liệu đã có trong data.js (không gọi AI)"""
    data = get_quiz_data()
    if name in data:
        import uuid
        changed = False
        for q in data[name]:
            if not q.get("id"):
                q["id"] = str(uuid.uuid4())[:8]
                changed = True
        if changed:
            save_quiz_data(data)
            
    state.ai_progress = {"current": 0, "total": 100, "status": "Re-analyzing existing data..."}
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: asyncio.run(ai_clean_worker(name, mode='reanalyze')))
    return {"message": "Re-analysis started"}

@app.post("/api/ai/clean/{name}/export_v2")
async def export_clean_v2(name: str):
    if not state.ai_results:
        raise HTTPException(status_code=400, detail="Vui lòng chạy quét AI trước khi xuất v2")
        
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
        
    # Thu thập tất cả các ID của câu trùng lặp (target IDs)
    results = state.ai_results
    dupe_ids = set()
    for pair in (results.get('exact', []) + results.get('fuzzy', [])):
        dupe_ids.add(str(pair[1]))
        
    # Tạo danh sách mới lọc bỏ các ID trùng lặp
    clean_questions = [q for q in data[name] if str(q.get("id")) not in dupe_ids]
            
    new_name = f"{name}_v2"
    data[new_name] = clean_questions
    
    save_quiz_data(data)
    return {"message": f"Đã tạo bản copy sạch: {new_name}", "new_subject": new_name, "count": len(clean_questions)}

@app.post("/api/data/subjects/{name}/items/bulk_delete")
async def bulk_delete_quiz_items(name: str, ids: List[str]):
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    # Lọc bỏ các câu có ID nằm trong danh sách cần xóa (chỉ xử lý các câu là dict)
    original_count = len(data[name])
    data[name] = [q for q in data[name] if not isinstance(q, dict) or str(q.get("id")) not in [str(i) for i in ids]]
    deleted_count = original_count - len(data[name])
            
    save_quiz_data(data)
    
    # Cập nhật kết quả AI: Loại bỏ danh sách ID vừa xóa khỏi kết quả trùng lặp
    if state.ai_results:
        id_strings = [str(i) for i in ids]
        state.ai_results['exact'] = [p for p in state.ai_results.get('exact', []) if str(p[0]) not in id_strings and str(p[1]) not in id_strings]
        state.ai_results['fuzzy'] = [p for p in state.ai_results.get('fuzzy', []) if str(p[0]) not in id_strings and str(p[1]) not in id_strings]
            
    return {"message": f"Deleted {deleted_count} items", "count": deleted_count}

@app.patch("/api/data/subjects/{name}/items/{item_id}")
async def update_quiz_item(name: str, item_id: str, update_data: dict):
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    for q in data[name]:
        if isinstance(q, dict) and str(q.get("id")) == item_id:
            for key, value in update_data.items():
                q[key] = value
            save_quiz_data(data)
            return {"message": "Updated successfully"}
    
    raise HTTPException(status_code=400, detail="Item not found")

@app.get("/api/data/stats")
async def get_system_stats():
    data = get_quiz_data()
    total_subjects = len(data)
    total_questions = sum(len(qs) for qs in data.values())
    
    # Thống kê phân bổ đáp án (tổng quát)
    ans_dist = {"A": 0, "B": 0, "C": 0, "D": 0, "Other": 0}
    for qs in data.values():
        for q in qs:
            if isinstance(q, dict):
                ans = q.get("ans", "Other")
                if not isinstance(ans, str): ans = "Other"
                ans = ans.upper()
                if ans in ans_dist: ans_dist[ans] += 1
                else: ans_dist["Other"] += 1
            
    return {
        "total_subjects": total_subjects,
        "total_questions": total_questions,
        "answer_distribution": ans_dist
    }

# --- Answer Audit System ---
def get_thread_from_img(img_path, config_urls):
    """Trích xuất Thread URL từ tên file ảnh và config"""
    filename = os.path.basename(img_path)
    # Tên file thường có dạng: mon_hoc_ma_de_q001.webp
    # Cần tìm URL trong config.json có slug khớp nhất
    import re
    for url in config_urls:
        slug = url.rstrip("/").split("/")[-1]
        safe_slug = re.sub(r'[^a-zA-Z0-9]', '_', slug)
        if safe_slug in filename:
            return url, slug
    return None, None

@app.get("/api/audit/subjects/{name}/threads")
async def get_subject_threads(name: str):
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    config_data = load_config_data()
    config_urls = config_data.get("POST_URLS", [])
    
    threads = {}
    for q in data[name]:
        url, slug = get_thread_from_img(q["img"], config_urls)
        if url:
            if url not in threads:
                threads[url] = {"url": url, "slug": slug, "count": 0}
            threads[url]["count"] += 1
            
    return list(threads.values())

async def audit_thread_worker(subject_name, urls):
    """Worker chạy ngầm để quét đáp án từ comment"""
    try:
        from fuoverflow_scraper_v6 import create_driver, login, BASE_URL
        from bs4 import BeautifulSoup
        import re
        from collections import Counter
        
        print(f"\n[AUDIT] 🚀 Bắt đầu đối soát môn: {subject_name}")
        driver = create_driver()
        login(driver)
        
        data = get_quiz_data()
        questions = data.get(subject_name, [])
        total_questions = len(questions)
        
        diff_results = []
        processed_count = 0
        
        for url in urls:
            msg = f"🔍 Đang đối soát Thread: {url}"
            print(f"\n[AUDIT] {msg}")
            await broadcast_log(msg)
            
            driver.get(url)
            time.sleep(3)
            soup = BeautifulSoup(driver.page_source, "lxml")
            
            # Tìm tất cả link ảnh trong thread này
            links = soup.find_all("a", href=lambda h: h and "/attachments/" in h)
            thread_total = len(links)
            
            for i, a in enumerate(links, 1):
                att_h = a.get("href")
                side_href = a.get("data-lb-sidebar-href", att_h)
                if not side_href.startswith("http"): side_href = BASE_URL + side_href
                
                # Tìm câu hỏi tương ứng trong data.js dựa trên tên file logic
                slug = url.rstrip("/").split("/")[-1]
                safe_slug = re.sub(r'[^a-zA-Z0-9]', '_', slug)
                img_name_part = f"{safe_slug}_q{str(i).zfill(3)}"
                
                # Tìm q_idx trong data
                q_idx = -1
                for idx, q in enumerate(questions):
                    if img_name_part in q["img"]:
                        q_idx = idx
                        break
                
                if q_idx == -1: 
                    continue
                
                processed_count += 1
                progress_msg = f"[AUDIT] Progress: {processed_count}/{total_questions} questions (Thread: {i}/{thread_total})"
                print(progress_msg) # Xuất log có xuống dòng để terminal ko bị "đứng"
                await broadcast_log(progress_msg)
                
                # Quét comment của ảnh này
                driver.get(side_href)
                time.sleep(2)
                s_soup = BeautifulSoup(driver.page_source, "lxml")
                txts = [el.get_text(strip=True).upper() for el in s_soup.select(".message-body .bbWrapper, .comment-body .bbWrapper, .comment-content .bbWrapper")]
                
                votes = []
                for t in txts:
                    m = re.search(r"(?:ĐÁP\s*ÁN|ANSWER|ANS|CHỌN|D\.ÁN|->|=>)\s*[:=]*\s*([A-D\s,]+)", t)
                    if m:
                        letters = re.findall(r"[A-D]", m.group(1))
                        f = "".join(sorted(set(letters))) 
                        if f: votes.append(f)
                    elif len(t) <= 12:
                        letters = re.findall(r"[A-D]", t)
                        f = "".join(sorted(set(letters)))
                        if f: votes.append(f)
                
                counts = Counter(votes)
                suggested = "?"
                has_conflict = False
                if votes:
                    common = counts.most_common(2)
                    suggested = common[0][0]
                    if len(common) > 1 and common[0][1] == common[1][1]:
                        has_conflict = True
                
                old_ans = questions[q_idx]["ans"]
                q_id = questions[q_idx].get("id")
                
                if old_ans != suggested or has_conflict:
                    diff_results.append({
                        "id": q_id,
                        "img": questions[q_idx]["img"],
                        "old_ans": old_ans,
                        "suggested": suggested,
                        "stats": dict(counts),
                        "has_conflict": has_conflict,
                        "url": side_href
                    })
            
        driver.quit()
        state.audit_results = diff_results
        final_msg = f"✅ Đối soát xong! Tìm thấy {len(diff_results)} câu cần xem xét."
        print(f"\n[AUDIT] {final_msg}")
        await broadcast_log(final_msg)
        save_draft(subject_name, "audit", diff_results)
        
    except Exception as e:
        err_msg = f"❌ Lỗi đối soát: {str(e)}"
        print(f"\n[AUDIT] {err_msg}")
        await broadcast_log(err_msg)
        if 'driver' in locals(): driver.quit()

class AuditRequest(BaseModel):
    urls: List[str]

@app.post("/api/audit/subjects/{name}/scan")
async def start_audit_scan(name: str, req: AuditRequest):
    if state.process is not None and state.process.poll() is None:
        raise HTTPException(status_code=400, detail="Crawler is running. Please stop it first.")
    
    state.audit_results = None
    asyncio.create_task(audit_thread_worker(name, req.urls))
    return {"message": "Audit scan started in background"}

@app.get("/api/audit/subjects/{name}/results")
async def get_audit_results(name: str):
    return load_draft(name, "audit") or state.audit_results

@app.post("/api/audit/subjects/{name}/apply")
async def apply_audit_changes(name: str, changes: dict):
    create_backup(f"{name}_pre_audit_apply")
    data = get_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    updated_count = 0
    id_map = {str(q.get("id")): q for q in data[name] if isinstance(q, dict)}
    
    for q_id, new_ans in changes.items():
        if q_id in id_map:
            id_map[q_id]["ans"] = new_ans
            id_map[q_id]["is_audited"] = True # Đánh dấu đã Audit
            updated_count += 1
            
    save_quiz_data(data)
    state.audit_results = None 
    delete_draft(name, "audit") # Xóa bản nháp sau khi đã áp dụng
    return {"message": f"Applied {updated_count} changes successfully"}

# --- Gemini AI Support ---
def extract_text_from_images_gemini(image_paths: List[str], api_key: str, custom_prompt: str = None):
    """Gửi nhiều ảnh lên Google Gemini 1.5 Flash để trích xuất text hàng loạt"""
    if not api_key: return []
    
    import base64
    default_prompt = """Bạn là một chuyên gia OCR. Hãy đọc nội dung từ các ảnh bài tập trắc nghiệm này.
    BỎ QUA các thành phần UI (logo, nút bấm, progress bar).
    CHỈ TRÍCH XUẤT: Câu hỏi và các phương án.
    Trả về JSON: {"results": [{"questionText": "...", "aiAns": "A/B/C/D hoặc TF"}]}"""
    
    prompt = custom_prompt if custom_prompt else default_prompt
    contents = [{"text": prompt}]
    for path in image_paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
                contents.append({
                    "inline_data": {
                        "mime_type": "image/webp",
                        "data": b64
                    }
                })

    # Dùng bản Flash Lite 3.1 để có hạn mức cao (500 lượt/ngày)
    model_id = "gemini-3.1-flash-lite-preview"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": contents}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            res_json = response.json()
            
            # Xử lý Rate Limit (429)
            if response.status_code == 429:
                wait_time = 15 # Mặc định đợi 15s cho Gemini Free
                error_msg = ""
                if isinstance(res_json, dict):
                    error_msg = res_json.get("error", {}).get("message", "")
                print(f"[GEMINI RATE LIMIT] Waiting {wait_time}s before retry... ({error_msg})")
                time.sleep(wait_time)
                continue

            if isinstance(res_json, list) or 'candidates' not in res_json:
                print(f"[GEMINI ERROR] Unexpected response format: {json.dumps(res_json)[:200]}")
                return []
            
            text_res = res_json['candidates'][0]['content']['parts'][0]['text']
            # Làm sạch chuỗi JSON nếu Gemini trả về kèm markdown ```json ... ```
            text_res = text_res.strip()
            if text_res.startswith("```json"):
                text_res = text_res[7:-3].strip()
            elif text_res.startswith("```"):
                text_res = text_res[3:-3].strip()
                
            parsed = json.loads(text_res)
            # Nếu Gemini trả về thẳng mảng kết quả
            if isinstance(parsed, list):
                return parsed
            # Nếu trả về object chứa key 'results'
            return parsed.get("results", [])
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[GEMINI EXCEPTION] {str(e)}")
                # Log thêm nội dung để debug nếu cần
                if 'res_json' in locals():
                    print(f"[DEBUG] Last res_json type: {type(res_json)}")
    return []

# --- Groq AI Support ---
def extract_text_from_images_groq(image_paths: List[str], api_key: str, custom_prompt: str = None):
    """Gửi nhiều ảnh lên Groq (Llama 4 Scout) để trích xuất text hàng loạt"""
    if not api_key: return []
    
    import base64
    default_prompt = """Bạn là một chuyên gia OCR chuyên nghiệp. Hãy đọc nội dung văn bản từ các ảnh bài tập trắc nghiệm này.
    
    YÊU CẦU QUAN TRỌNG:
    1. BỎ QUA HOÀN TOÀN các thành phần giao diện (UI) sau:
       - Logo/Watermark website (fuoverflow.com, FUO, ...)
       - Dòng trạng thái tiến độ (ví dụ: 'There are ... questions', 'your progress of answering is')
       - Số thứ tự câu hỏi (ví dụ: 'Question: 51', 'Q: 28')
       - Các nút bấm (Back, Next, Finish, Choose 1 answer)
    2. CHỈ TRÍCH XUẤT nội dung cốt lõi: Câu hỏi và các phương án trả lời.
    3. Trả về một đối tượng JSON có khóa duy nhất là 'results', bên trong là mảng các đối tượng:
    {
      "questionText": "nội dung câu hỏi và các đáp án (đã lược bỏ rác UI)",
      "aiAns": "A/B/C/D hoặc TF"
    }
    Lưu ý: Chỉ trả về JSON, không giải thích gì thêm."""
    
    prompt = custom_prompt if custom_prompt else default_prompt
    content = [{"type": "text", "text": prompt}]
    for path in image_paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/webp;base64,{b64}"}
                })
                
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Nếu không có ảnh, chuyển sang model text-only để tăng tốc độ và giới hạn
    is_vision = len(image_paths) > 0
    model_id = "meta-llama/llama-4-scout-17b-16e-instruct" if is_vision else "llama-3.1-8b-instant"
    
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"}
    }
    
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=60)
            res_json = response.json()
            
            # Xử lý Rate Limit (429)
            if response.status_code == 429:
                wait_time = 15 # Mặc định đợi 15s nếu không parse được thời gian
                msg = res_json.get("error", {}).get("message", "")
                import re
                match = re.search(r"try again in ([\d\.]+)s", msg)
                if match: wait_time = float(match.group(1)) + 1
                print(f"[GROQ RATE LIMIT] Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue

            if 'choices' not in res_json:
                print(f"[GROQ ERROR] Full Response: {json.dumps(res_json, indent=2)}")
                return []
            text_res = res_json['choices'][0]['message']['content']
            return json.loads(text_res).get("results", [])
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[GROQ EXCEPTION] {str(e)}")
            time.sleep(2)
    return []

def extract_text_from_images(image_paths: List[str], config: dict, custom_prompt: str = None):
    """Router tự động chọn Provider dựa trên cấu hình"""
    provider = config.get("AI_PROVIDER", "groq")
    if provider == "gemini":
        return extract_text_from_images_gemini(image_paths, config.get("GEMINI_API_KEY"), custom_prompt)
    else:
        return extract_text_from_images_groq(image_paths, config.get("GROQ_API_KEY"), custom_prompt)

@app.get("/api/ai/test")
async def test_ai_api():
    config = load_config_data()
    provider = config.get("AI_PROVIDER", "groq")
    
    if provider == "gemini":
        api_key = config.get("GEMINI_API_KEY")
        if not api_key: return {"ok": False, "error": "Missing Gemini API Key"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": "Hi"}]}]}
        try:
            r = requests.post(url, json=payload, timeout=60) # Tăng lên 60s
            return {"ok": r.status_code == 200, "data": r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    else:
        api_key = config.get("GROQ_API_KEY")
        if not api_key: return {"ok": False, "error": "Missing Groq API Key"}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": "meta-llama/llama-4-scout-17b-16e-instruct", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=10)
            if r.status_code == 200: return {"ok": True}
            return {"ok": False, "error": r.json().get('error', {}).get('message', r.text)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

async def ai_classify_worker(subject_name: str, source_id: str = None):
    config = load_config_data()
    
    data = get_quiz_data()
    questions = data.get(subject_name, [])
    
    # Filter by source if requested
    to_process = []
    for idx, q in enumerate(questions):
        if source_id and q.get("source_id") != source_id: continue
        to_process.append(idx)

    state.ai_progress = {"current": 0, "total": len(to_process), "status": "Classifying TF/MCQ..."}
    
    batch_size = 5 # Use safer batch size
    loop = asyncio.get_event_loop()
    
    for i in range(0, len(to_process), batch_size):
        batch_indices = to_process[i:i+batch_size]
        paths = [questions[idx]["img"] for idx in batch_indices]
        
        prompt = "Phân loại các ảnh này là 'TF' (Câu hỏi Đúng/Sai) hoặc 'MCQ' (Trắc nghiệm A/B/C/D). Trả về JSON mảng 'results' chứa { 'type': 'TF'/'MCQ' }. Chỉ trả về JSON."
        
        # Call the router via executor
        results = await loop.run_in_executor(None, extract_text_from_images, paths, config, prompt)
        
        for j, res in enumerate(results):
            if j < len(batch_indices):
                questions[batch_indices[j]]["type"] = res.get("type", "MCQ")
        
        state.ai_progress["current"] += len(batch_indices)
        save_quiz_data(data)
        await asyncio.sleep(0.5)

    state.ai_progress["status"] = "Classification Complete"

@app.post("/api/ai/classify/{name}")
async def start_ai_classify(name: str, source_id: str = None):
    asyncio.create_task(ai_classify_worker(name, source_id))
    return {"message": "Classification started"}


# --- Subject Management Endpoints ---
@app.post("/api/data/subjects/{name}/visibility")
async def toggle_subject_visibility(name: str, hidden: bool):
    data = get_quiz_data()
    if name in data:
        if "_metadata" not in data: data["_metadata"] = {}
        if name not in data["_metadata"]: data["_metadata"][name] = {}
        data["_metadata"][name]["hidden"] = hidden
        save_quiz_data(data)
        return {"message": "Updated"}
    raise HTTPException(status_code=404, detail="Not found")

@app.post("/api/data/subjects/{name}/clone")
async def clone_subject(name: str, new_name: str):
    data = get_quiz_data()
    if name in data:
        if new_name in data: raise HTTPException(status_code=400, detail="Name exists")
        import copy
        data[new_name] = copy.deepcopy(data[name])
        save_quiz_data(data)
        return {"message": "Cloned"}
    raise HTTPException(status_code=404, detail="Not found")

# --- Original Config & Process Endpoints ---

# Constants
CONFIG_FILE = "config.json"
SCRAPER_SCRIPT = "fuoverflow_scraper_v6.py"

# Global state
class State:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[float] = None
        self.logs: List[str] = []
        self.max_logs = 1000
        self.active_websockets: List[WebSocket] = []
        self.ai_results = None
        self.ai_progress = {"current": 0, "total": 0, "status": ""}
        self.audit_results = None
        self.workflow_running = False

state = State()

def create_backup(subject_name: str):
    if not os.path.exists("backups"): os.makedirs("backups")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = f"backups/data_{subject_name}_{ts}.js"
    if os.path.exists("data.js"):
        shutil.copy("data.js", bak_path)
        # Giữ lại tối đa 20 bản sao lưu gần nhất
        backups = sorted([os.path.join("backups", f) for f in os.listdir("backups") if f.endswith(".js")], key=os.path.getctime)
        if len(backups) > 20:
            os.remove(backups[0])
        return bak_path
    return None

def save_draft(subject: str, tool_type: str, data: dict):
    if not os.path.exists("drafts"): os.makedirs("drafts")
    path = f"drafts/{subject}_{tool_type}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_draft(subject: str, tool_type: str):
    path = f"drafts/{subject}_{tool_type}.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def delete_draft(subject: str, tool_type: str):
    path = f"drafts/{subject}_{tool_type}.json"
    if os.path.exists(path):
        os.remove(path)

# Models với Validation cơ bản
class SourceModel(BaseModel):
    id: str
    name: str
    url: str
    subject: str
    enabled: bool = True

class Config(BaseModel):
    USERNAME: str
    PASSWORD: str
    AI_PROVIDER: str = "gemini"
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    SOURCES: List[dict] = []
    IMAGES_DIR: str = "images"
    IMAGES_WORD_DIR: str = "images_word"
    ENABLE_WORD_EXPORT: bool = False
    ENABLE_OCR_CLASSIFY: bool = False
    PAGE_TIMEOUT: int = 60
    REQUEST_DELAY: int = 2


# Helpers
def load_config_data():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config_data(data):
    # Ghi file nguyên tử (Atomic write) để tránh corrupt
    tmp_file = CONFIG_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp_file, CONFIG_FILE)

async def broadcast_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}"
    
    # In ra console an toàn (tránh lỗi charmap trên Windows)
    safe_print(formatted_msg)
        
    state.logs.append(formatted_msg)
    if len(state.logs) > state.max_logs:
        state.logs.pop(0)
    
    # Broadcast tới tất cả client đang kết nối
    if not state.active_websockets:
        return

    # Gom nhóm log nếu gửi quá nhanh (đã xử lý ở mức cơ bản bằng việc gửi từng dòng)
    disconnected = []
    for ws in state.active_websockets:
        try:
            await ws.send_text(formatted_msg)
        except Exception:
            disconnected.append(ws)
    
    for ws in disconnected:
        if ws in state.active_websockets:
            state.active_websockets.remove(ws)

async def read_stream(stream):
    try:
        while True:
            # Chạy readline trong thread pool để không block event loop
            line = await asyncio.get_event_loop().run_in_executor(None, stream.readline)
            if not line:
                break
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                await broadcast_log(msg)
    except Exception as e:
        await broadcast_log(f"Lỗi đọc log: {str(e)}")

# API Endpoints
@app.get("/api/config")
async def get_config():
    return load_config_data()

@app.post("/api/config")
async def update_config(config: Config):
    save_config_data(config.dict())
    return {"message": "Config updated successfully"}

@app.get("/api/status")
async def get_status():
    is_running = False
    if state.process is not None:
        if state.process.poll() is None:
            is_running = True
        else:
            state.process = None # Cleanup
            
    return {
        "running": is_running,
        "start_time": datetime.fromtimestamp(state.start_time).isoformat() if state.start_time and is_running else None,
        "uptime": int(time.time() - state.start_time) if state.start_time and is_running else 0,
        "pid": state.process.pid if is_running else None
    }

@app.post("/api/start")
async def start_scraper(request: dict):
    async with start_lock:
        if state.process and state.process.poll() is None:
            raise HTTPException(status_code=400, detail="Robot is already running")
        
        # New check for background tasks (AI Clean, Trash Filter, etc)
        if getattr(state, 'workflow_running', False):
            raise HTTPException(status_code=400, detail="System is still processing previous crawl results")
        options = request.get("options", {})
        config_data = load_config_data()
        all_sources = {s["id"]: s for s in config_data.get("SOURCES", [])}
        
        source_ids = request.get("source_ids", [])
        selected_sources = [all_sources[sid] for sid in source_ids if sid in all_sources]
        if not selected_sources:
            raise HTTPException(status_code=400, detail="No valid sources selected")

        state.start_time = time.time()
        state.logs = [] # Clear logs
        
        async def run_sequence():
            state.workflow_running = True
            try:
                processed_subjects = set()
                for source in selected_sources:
                    cmd = [
                        "python", "-u", SCRAPER_SCRIPT,
                        "--url", source["url"],
                        "--subject", source["subject"],
                        "--source_id", source["id"]
                    ]
                    await broadcast_log(f"--- STARTING: {source['name']} ---")
                    state.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
                    await read_stream(state.process.stdout)
                    state.process.wait()
                    await broadcast_log(f"--- FINISHED: {source['name']} ---")
                    processed_subjects.add(source["subject"])

                # After all sources are finished
                for subject in processed_subjects:
                    if options.get("ai_lab"):
                        await broadcast_log(f"🧠 [AI LAB] Starting OCR for {subject}...")
                        await ai_clean_worker(subject, mode='scan_only_ocr')

                    if options.get("trash_filter"):
                        await broadcast_log(f"🗑️ [TRASH FILTER] Cleaning up subject: {subject}...")
                        await trash_filter_worker(subject)

                    if options.get("ai_clean"):
                        await broadcast_log(f"🪄 [AI CLEAN] Scanning duplicates for {subject}...")
                        await ai_clean_worker(subject, mode='scan')

                await broadcast_log("✨ ALL SELECTED SOURCES COMPLETED!")
            finally:
                state.process = None
                state.workflow_running = False

        asyncio.create_task(run_sequence())
        return {"message": f"Started sequence of {len(selected_sources)} sources"}

@app.post("/api/stop")
async def stop_scraper():
    if state.process is None or state.process.poll() is not None:
        return {"message": "Scraper is not running"}
    
    await broadcast_log("--- ĐANG DỪNG CRAWLER VÀ DRIVER... ---")
    
    try:
        # Sử dụng psutil để kill toàn bộ process tree (bao gồm cả chrome/chromedriver)
        parent = psutil.Process(state.process.pid)
        children = parent.children(recursive=True)
        
        # Gửi tín hiệu terminate cho con trước, cha sau
        for child in children:
            child.terminate()
        parent.terminate()
        
        # Chờ tối đa 5s để đóng sạch
        _, alive = psutil.wait_procs(children + [parent], timeout=5)
        
        # Nếu vẫn còn sống thì dùng kill (buộc dừng)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
                
        await broadcast_log("--- TẤT CẢ TIẾN TRÌNH ĐÃ DỪNG ---")
    except Exception as e:
        await broadcast_log(f"LỖI KHI DỪNG: {str(e)}")
        # Fallback dùng lệnh kill của subprocess nếu psutil lỗi
        state.process.kill()
    
    return {"message": "Scraper stopped"}

@app.post("/api/cleanup")
async def cleanup_data_api():
    try:
        create_backup("global_cleanup")
        # Chạy script cleanup trong process riêng
        subprocess.run(["python", "cleanup_data.py"], check=True)
        return {"message": "Cleanup completed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/drafts")
async def list_drafts():
    if not os.path.exists("drafts"): return {"drafts": []}
    files = os.listdir("drafts")
    drafts = []
    for f in files:
        if f.endswith(".json"):
            parts = f.replace(".json", "").split("_")
            if len(parts) >= 2:
                drafts.append({
                    "subject": "_".join(parts[:-1]),
                    "type": parts[-1],
                    "file": f,
                    "mtime": os.path.getmtime(os.path.join("drafts", f))
                })
    return {"drafts": drafts}

@app.get("/api/drafts/{subject}/{tool_type}")
async def get_draft(subject: str, tool_type: str):
    data = load_draft(subject, tool_type)
    if data: return data
    raise HTTPException(status_code=404, detail="Draft not found")

@app.delete("/api/drafts/{subject}/{tool_type}")
async def remove_draft(subject: str, tool_type: str):
    delete_draft(subject, tool_type)
    return {"message": "Draft deleted"}

@app.get("/api/backups")
async def list_backups():
    if not os.path.exists("backups"): return {"backups": []}
    files = sorted([f for f in os.listdir("backups") if f.endswith(".js")], key=lambda x: os.path.getctime(os.path.join("backups", x)), reverse=True)
    return {"backups": files}

@app.post("/api/backups/{filename}/restore")
async def restore_backup(filename: str):
    path = os.path.join("backups", filename)
    if os.path.exists(path):
        shutil.copy(path, "data.js")
        return {"message": "Restored successfully"}
    raise HTTPException(status_code=404, detail="Backup not found")

async def trash_filter_worker(subject_name: str):
    create_backup(f"{subject_name}_pre_trash")
    data = get_quiz_data()
    if subject_name not in data: return
    
    questions = data[subject_name]
    before_count = len(questions)
    
    # Lọc: Có ảnh, Ảnh tồn tại, và Đáp án không phải là "?"
    clean_questions = []
    removed_count = 0
    
    for q in questions:
        img_path = q.get("img", "")
        ans = q.get("ans", "")
        
        # Điều kiện giữ lại:
        # 1. Có đường dẫn ảnh
        # 2. File ảnh tồn tại trên đĩa
        # 3. Đáp án khác "?" và không rỗng
        if img_path and os.path.exists(img_path) and ans and ans != "?":
            clean_questions.append(q)
        else:
            removed_count += 1
            
    if removed_count > 0:
        data[subject_name] = clean_questions
        save_quiz_data(data)
        await broadcast_log(f"🗑️ Trash Filter: Removed {removed_count} invalid items from {subject_name}")
    else:
        await broadcast_log(f"✅ Trash Filter: Subject {subject_name} is already clean.")

# --- AI Clean Logic ---
@app.get("/api/ai/clean/results")
async def get_ai_clean_results():
    return {"results": state.ai_results, "progress": state.ai_progress}

@app.post("/api/ai/clean/{name}/scan")
async def start_ai_clean_scan(name: str):
    if state.process is not None and state.process.poll() is None:
        raise HTTPException(status_code=400, detail="Crawler is running")
    
    state.ai_results = None
    state.ai_progress = {"current": 0, "total": 0, "status": "Starting Full AI Scan..."}
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: asyncio.run(ai_clean_worker(name)))
    return {"message": "Full scan started"}

@app.post("/api/ai/clean/{name}/test")
async def start_ai_clean_test(name: str):
    """Quét thử 3 câu đầu tiên để kiểm tra API"""
    if state.process is not None and state.process.poll() is None:
        raise HTTPException(status_code=400, detail="Crawler is running")
    
    state.ai_results = None
    state.ai_progress = {"current": 0, "total": 3, "status": "Starting Quick Test..."}
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, lambda: asyncio.run(ai_clean_worker(name, limit=3)))
    return {"message": "Quick Test started"}

async def ai_clean_worker(subject_name: str, limit: int = None, mode: str = 'scan'):
    try:
        import hashlib
        import uuid
        config = load_config_data()
        data = get_quiz_data()
        all_questions = data.get(subject_name, [])
        
        # Ensure ALL questions in subject have IDs first
        id_changed = False
        for q in all_questions:
            if isinstance(q, dict) and not q.get("id"):
                q["id"] = str(uuid.uuid4())[:8]
                id_changed = True
        if id_changed:
            save_quiz_data(data)

        # Nếu có limit (Quick Test), chỉ lấy số lượng yêu cầu
        questions = all_questions[:limit] if limit else all_questions
        total = len(questions)
        
        state.ai_progress["total"] = total
        
        if mode == 'reanalyze':
            await broadcast_log(f"🔄 AI Clean: Re-analyzing existing data for {subject_name}...")
        elif mode == 'scan_only_ocr':
            await broadcast_log(f"📸 AI Lab: Running Phase 1 (OCR) for {subject_name}...")
        else:
            await broadcast_log(f"🚀 AI Clean: Starting Hybrid Scan for {subject_name} ({total} questions)")
        
        # 1. Hashing Check (Chỉ chạy khi scan)
        hashes = {}
        exact_dupes = []

        if mode == 'scan':
            for q in questions:
                img_path = q["img"]
                if os.path.exists(img_path):
                    with open(img_path, "rb") as f:
                        h = hashlib.md5(f.read()).hexdigest()
                        if h in hashes:
                            exact_dupes.append((hashes[h], q.get("id"))) # Lưu (ID_gốc, ID_trùng)
                        else:
                            hashes[h] = q.get("id")
        
            await broadcast_log(f"✅ Hashing done: Found {len(exact_dupes)} exact image duplicates")
        
        # 2. Local OCR (Offline - No API limits)
        if mode in ['scan', 'scan_only_ocr']:
            state.ai_progress["status"] = "Phase 1: Local OCR Scanning..."
            for i in range(total):
                state.ai_progress["current"] = i
                q = questions[i]
                img_path = q["img"]
                
                # CHỈ QUÉT NẾU CHƯA CÓ TEXT (Bảo toàn công sức cũ)
                if not q.get("questionText"):
                    if os.path.exists(img_path):
                        if total < 10 or i % 10 == 0:
                            percent = int((i/total)*100)
                            await broadcast_log(f"📸 OCR [{i}/{total}] {percent}% - {os.path.basename(img_path)}")
                        
                        loop = asyncio.get_event_loop()
                        raw_text = await loop.run_in_executor(None, local_ocr_extract, img_path)
                        q["questionText"] = raw_text
                
                if i % 20 == 0:
                    save_quiz_data(data) # Lưu tiến độ

        # 3. AI Refinement (Batch)
        if mode in ['scan', 'scan_only_ocr']:
            state.ai_progress["status"] = "Phase 2: AI Refinement (Cleaning Text)..."
            batch_size = 20 if config.get("AI_PROVIDER") == "gemini" else 5
            
            # CHỈ LỌC NHỮNG CÂU CHƯA ĐƯỢC AI LÀM SẠCH (Bảo toàn API)
            pending_indices = [idx for idx, q in enumerate(questions) if not q.get("aiAns") and q.get("questionText")]
            
            if pending_indices:
                for i in range(0, len(pending_indices), batch_size):
                    chunk = pending_indices[i : i + batch_size]
                    batch_texts = [questions[idx].get("questionText", "") for idx in chunk]
                    
                    state.ai_progress["current"] = questions.index(questions[chunk[0]])
                    
                    refine_prompt = "Fix OCR errors, remove UI noise. Return JSON: [{'questionText': 'text', 'aiAns': 'ans'}]. Data: " + json.dumps(batch_texts)

                    loop = asyncio.get_event_loop()
                    results = await loop.run_in_executor(None, extract_text_from_images, [], config, refine_prompt)
                    
                    for j, res in enumerate(results):
                        if j < len(chunk):
                            idx = chunk[j]
                            questions[idx]["questionText"] = res.get("questionText", "")
                            questions[idx]["aiAns"] = res.get("aiAns", "")
                    
                    save_quiz_data(data)
                    await asyncio.sleep(1)

        if mode == 'scan_only_ocr':
            await broadcast_log(f"✅ AI Lab OCR finished for {subject_name}")
            return

        await broadcast_log("✨ OCR & AI Refinement Complete! Starting Duplicate Analysis...")
        
        # 3. Fuzzy Text Duplicate Check
        from difflib import SequenceMatcher

        def normalize_text(text):
            if not text: return ""
            text = text.lower()
            noise_patterns = [
                r'fuoverflow(\.com)?', r'choose 1 answer', r'multiple choice',
                r'there are \d+ questions', r'your progress of answering is',
                r'question: \d+', r'q: \d+', r'back', r'next', r'finish the exam',
                r'về trang chủ', r'câu hỏi \d+'
            ]
            for pattern in noise_patterns:
                text = re.sub(pattern, '', text)
            
            text = re.sub(r'[^\w\s]', '', text) 
            return " ".join(text.split())

        text_dupes = []
        dupe_ids = [d[1] for d in exact_dupes]
        for i in range(total):
            id_i = questions[i].get("id")
            if id_i in dupe_ids: continue
            
            t1 = normalize_text(questions[i].get("questionText", ""))
            if len(t1) < 10: continue 
            
            for j in range(i + 1, total):
                id_j = questions[j].get("id")
                if id_j in dupe_ids: continue
                
                t2 = normalize_text(questions[j].get("questionText", ""))
                if not t2: continue
                
                ratio = SequenceMatcher(None, t1, t2).ratio()
                if ratio > 0.85:
                    text_dupes.append((id_i, id_j)) # Lưu (ID_gốc, ID_trùng)

        state.ai_results = {
            "exact": exact_dupes,
            "fuzzy": text_dupes,
            "total_scanned": total
        }
        # LƯU BẢN NHÁP (Draft)
        save_draft(subject_name, "clean", state.ai_results)
        
        state.ai_progress["status"] = "Scan Complete"
        save_quiz_data(data)
        await broadcast_log(f"✨ AI Clean Finished! Found {len(exact_dupes) + len(text_dupes)} potential duplicates.")
        
    except Exception as e:
        await broadcast_log(f"❌ AI Clean Error: {str(e)}")
        state.ai_progress["status"] = "Error"

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    state.active_websockets.append(websocket)
    
    # Gửi lại 100 dòng log cũ nhất để client mới vào xem được ngay
    for log in state.logs[-100:]:
        await websocket.send_text(log)
        
    try:
        while True:
            # Giữ kết nối
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in state.active_websockets:
            state.active_websockets.remove(websocket)

def kill_port_owner(port):
    import psutil
    import os
    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == 'LISTEN':
            pid = conn.pid
            if pid and pid != os.getpid():
                try:
                    p = psutil.Process(pid)
                    safe_print(f"⚠️ Port {port} is occupied by PID {pid} ({p.name()}). Killing it...")
                    p.terminate()
                    p.wait(timeout=3)
                except Exception as e:
                    safe_print(f"❌ Could not kill process on port {port}: {e}")

if __name__ == "__main__":
    import uvicorn
    # Tự động dọn dẹp cổng trước khi chạy
    kill_port_owner(8000)
    
    safe_print("\n" + "="*50)
    safe_print("DASHBOARD IS RUNNING AT: http://localhost:8000")
    safe_print("Access admin.html to manage.")
    safe_print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
