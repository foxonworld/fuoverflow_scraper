import os, re, csv, time, requests, json, argparse, sys
from collections import Counter
from pathlib import Path

# Ép kiểu mã hóa UTF-8 cho console để tránh lỗi trên Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from docx import Document
from docx.shared import Inches
from PIL import Image

import config

# ─────────────────────────────────────────────────────────────
# [SC] HỆ THỐNG QUÉT ĐỀ FUOVERFLOW - MULTI-SOURCE PRO (v8.0)
# ─────────────────────────────────────────────────────────────
BASE_URL = "https://fuoverflow.com"

def create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(180)
    return driver

def login(driver):
    from selenium.webdriver.common.keys import Keys
    print(f"\n[1/3] Dang dang nhap dien dan...", end="")
    try:
        driver.get(f"{BASE_URL}/login")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.NAME, "login")))
        driver.find_element(By.NAME, "login").send_keys(config.USERNAME)
        pw = driver.find_element(By.NAME, "password")
        pw.send_keys(config.PASSWORD)
        pw.send_keys(Keys.RETURN)
        time.sleep(5)
        print(" OK.")
    except: print(" Da vao san.")

def get_ans_via_driver(driver, url):
    for attempt in range(2):
        try:
            if not url.startswith("http"): url = BASE_URL + url
            driver.get(url)
            time.sleep(1.5 if attempt == 0 else 3.5)
            soup = BeautifulSoup(driver.page_source, "lxml")
            txts = [el.get_text(strip=True).upper() for el in soup.select(".message-body .bbWrapper, .comment-body .bbWrapper, .comment-content .bbWrapper")]
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
            if votes: return Counter(votes).most_common(1)[0][0]
        except: pass
    return "?"

def generate_quiz_data(data, subject, filename="data.js"):
    try:
        quiz_dict = {}
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r"const\s+quizData\s*=\s*(.*);", content, re.DOTALL)
                    if match: quiz_dict = json.loads(match.group(1))
            except: pass
        
        # Merge logic: Use att_id as key for perfect deduplication
        existing = quiz_dict.get(subject, [])
        # Map existing by att_id if available, otherwise by img path
        existing_map = {}
        for q in existing:
            key = q.get("att_id") or q.get("img")
            existing_map[key] = q
        
        for new_q in data:
            key = new_q.get("att_id") or new_q.get("img")
            # Nếu đã có và câu mới là "?" trong khi câu cũ đã có đáp án -> Giữ câu cũ
            if key in existing_map and new_q["ans"] == "?" and existing_map[key]["ans"] != "?":
                # Cập nhật các trường khác nếu cần (như source_id) nhưng giữ ans
                existing_map[key].update({k: v for k, v in new_q.items() if v != "?"})
            else:
                if key in existing_map:
                    existing_map[key].update(new_q)
                else:
                    existing_map[key] = new_q
            
        quiz_dict[subject] = list(existing_map.values())
        
        # Sắp xếp lại để dễ nhìn (tùy chọn)
        quiz_dict[subject].sort(key=lambda x: x.get("att_id", ""))

        with open(filename, "w", encoding="utf-8") as f:
            f.write("const quizData = " + json.dumps(quiz_dict, ensure_ascii=False) + ";")
        print(f"\n ✅ Đã cập nhật dữ liệu môn: {subject}")
    except Exception as e:
        print(f"\n ❌ Lỗi lưu dữ liệu: {e}")

def run_scraper(target_url, subject_name, source_id, images_dir="images"):
    # Đọc dữ liệu cũ để đối soát
    existing_data = []
    if os.path.exists("data.js"):
        try:
            with open("data.js", "r", encoding="utf-8") as f:
                content = f.read()
                match = re.search(r"const\s+quizData\s*=\s*(.*);", content, re.DOTALL)
                if match:
                    full_data = json.loads(match.group(1))
                    existing_data = full_data.get(subject_name, [])
        except: pass

    # Map để tìm nhanh: att_id -> item
    existing_map = {str(q.get("att_id")): q for q in existing_data if q.get("att_id")}
    # Map phụ: img_path -> item (cho các câu cũ chưa có att_id)
    img_map = {q.get("img"): q for q in existing_data if not q.get("att_id")}

    Path(images_dir).mkdir(exist_ok=True)
    driver = create_driver()
    login(driver)
    
    session = requests.Session()
    for i_c in driver.get_cookies(): session.cookies.set(i_c['name'], i_c['value'])
    
    print(f"\n[2/3] 🛰️ Đang quét nguồn: {target_url} (ID: {source_id})")
    driver.get(target_url)
    time.sleep(3)
    
    # Scroll to load all images
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(3)
    
    soup = BeautifulSoup(driver.page_source, "lxml")
    links = soup.find_all("a", href=lambda h: h and "/attachments/" in h)
    print(f"  🔍 Tìm thấy {len(links)} ảnh.")
    
    final_data = []
    for i, a in enumerate(links, 1):
        att_h = a.get("href")
        side_href = a.get("data-lb-sidebar-href", att_h)
        
        # Extract Attachment ID (e.g. /attachments/abc.12345/ -> 12345)
        att_id_match = re.search(r"\.(\d+)/?$", att_h) or re.search(r"/(\d+)/?$", att_h)
        att_id = att_id_match.group(1) if att_id_match else f"unknown_{i}"
        
        img_n = f"att_{att_id}.webp"
        img_p = os.path.join(images_dir, img_n)
        
        # --- SMART SYNC LOGIC ---
        existing_item = existing_map.get(att_id)
        
        # 1. Check if we can skip download
        skip_download = os.path.exists(img_p)
        
        # 2. Check if we can skip answer fetching (Slow part)
        # Bỏ qua nếu đã có đáp án chuẩn HOẶC đã được Audited
        skip_ans = False
        current_ans = "?"
        if existing_item:
            current_ans = existing_item.get("ans", "?")
            if current_ans != "?" or existing_item.get("is_audited"):
                skip_ans = True

        # Action: Download if missing
        if not skip_download:
            try:
                full_img_url = (BASE_URL + att_h if att_h.startswith("/") else att_h)
                if "?" not in full_img_url: full_img_url += "?full=1"
                r = session.get(full_img_url, timeout=20)
                if len(r.content) > 1000:
                    with open(img_p, "wb") as f: f.write(r.content)
                    print(f"  📥 Downloaded: {img_n}")
            except: pass
        
        # Action: Get answer if missing
        ans = current_ans
        if not skip_ans:
            ans = get_ans_via_driver(driver, side_href)
            print(f"  🔎 Scanned Ans: {ans}")
        
        # Action: Reuse existing data properties (ID, OCR, etc.)
        new_item = {
            "id": existing_item.get("id") if existing_item else None, # Keep old ID
            "att_id": att_id,
            "img": img_p,
            "ans": ans,
            "type": existing_item.get("type", "MCQ") if existing_item else "MCQ",
            "source_id": source_id,
            "is_audited": existing_item.get("is_audited", False) if existing_item else False
        }
        
        # Carry over AI results if exist
        if existing_item:
            for key in ["questionText", "aiAns"]:
                if existing_item.get(key): new_item[key] = existing_item[key]

        final_data.append(new_item)
        
        status = "REUSED" if skip_ans and skip_download else "SYNCED"
        print(f"  ✨ [{status}] Câu {i}/{len(links)}. ID: {att_id} -> Ans: {ans}", end="\r", flush=True)

    generate_quiz_data(final_data, subject_name)
    driver.quit()
    print(f"\n[3/3] ✅ Hoàn tất quét {source_id}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--source_id", required=True)
    parser.add_argument("--dir", default="images")
    args = parser.parse_args()
    
    run_scraper(args.url, args.subject, args.source_id, args.dir)
