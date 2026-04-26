import json, os, re
import config

if not getattr(config, "ENABLE_OCR_CLASSIFY", True):
    print("⚠️ Tính năng OCR đang bị TẮT trong config.py. Vui lòng bật lại nếu muốn chạy script này.")
    exit(0)

import easyocr
print("[1/2] Đang tải mô hình AI EasyOCR (lần đầu sẽ mất xíu thời gian)...")
reader = easyocr.Reader(['en'], gpu=False, verbose=False)

def check_tf(img_path):
    try:
        res = reader.readtext(img_path, detail=0)
        text = " ".join(res).lower()
        if "true" in text and "false" in text:
            return "TF"
    except Exception as e:
        pass
    return "MCQ"

print("[2/2] Bắt đầu đọc file data.js...")
data_file = "data.js"
if not os.path.exists(data_file):
    print("❌ Không tìm thấy file data.js!")
    exit(1)

with open(data_file, "r", encoding="utf-8") as f:
    content = f.read()

match = re.search(r"const\s+quizData\s*=\s*(.*);", content, re.DOTALL)
if match:
    full_db = json.loads(match.group(1))
    total = sum(len(qs) for qs in full_db.values())
    print(f"📦 Tìm thấy tổng cộng {total} câu ảnh. Đang tiến hành quét phân loại...\n")
    
    count = 0
    tf_count = 0
    for subj, qs in full_db.items():
        for q in qs:
            count += 1
            if q.get("type", "UNKNOWN") in ["TF", "MCQ"]:
                if q.get("type") == "TF": tf_count += 1
                continue # Nếu đã có loại rồi thì next
            
            p = os.path.normpath(q["img"])
            if os.path.exists(p):
                typ = check_tf(p)
                q["type"] = typ
                if typ == "TF": tf_count += 1
            else:
                q["type"] = "MCQ"
            
            print(f"  👉 Quét [{count}/{total}]: {p} -> [{q['type']}]", end="\r", flush=True)

    with open(data_file, "w", encoding="utf-8") as f:
        f.write("const quizData = " + json.dumps(full_db, ensure_ascii=False) + ";")
    print(f"\n\n✅ Đã phân loại xong! Tổng số câu True/False tìm được: {tf_count}")
else:
    print("❌ Format file data.js không hợp lệ!")
