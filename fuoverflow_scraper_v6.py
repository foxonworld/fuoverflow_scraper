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
        
        # Merge logic: update existing or add new
        existing = quiz_dict.get(subject, [])
        existing_map = {q["img"]: q for q in existing}
        
        for new_q in data:
            existing_map[new_q["img"]] = new_q
            
        quiz_dict[subject] = list(existing_map.values())
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write("const quizData = " + json.dumps(quiz_dict, ensure_ascii=False) + ";")
        print(f"\n ✅ Đã cập nhật dữ liệu môn: {subject}")
    except Exception as e:
        print(f"\n ❌ Lỗi lưu dữ liệu: {e}")

def run_scraper(target_url, subject_name, source_id, images_dir="images"):
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
        
        safe_slug = re.sub(r'[^a-zA-Z0-9]', '_', source_id)
        img_n = f"{safe_slug}_q{str(i).zfill(3)}.webp"
        img_p = os.path.join(images_dir, img_n)
        
        # Download image
        if not os.path.exists(img_p):
            try:
                full_img_url = (BASE_URL + att_h if att_h.startswith("/") else att_h)
                if "?" not in full_img_url: full_img_url += "?full=1"
                r = session.get(full_img_url, timeout=20)
                if len(r.content) > 1000:
                    with open(img_p, "wb") as f: f.write(r.content)
            except: pass
        
        ans = get_ans_via_driver(driver, side_href)
        
        final_data.append({
            "img": img_p,
            "ans": ans,
            "type": "MCQ",
            "source_id": source_id
        })
        print(f"  💡 Câu {i}/{len(links)}. [Ảnh: {img_n}] -> Đáp án: {ans}", end="\r", flush=True)

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
