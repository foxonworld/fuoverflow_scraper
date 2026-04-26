import os
import json
import re

def cleanup():
    data_file = "data.js"
    if not os.path.exists(data_file):
        print("❌ Không tìm thấy file data.js")
        return

    print("🔍 Đang đọc data.js...")
    with open(data_file, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r"const\s+quizData\s*=\s*(.*);", content, re.DOTALL)
    if not match:
        print("❌ Định dạng file data.js không hợp lệ.")
        return

    quiz_data = json.loads(match.group(1))
    
    valid_images = set()
    new_quiz_data = {}

    # 1. Lọc bỏ các câu có đáp án "?"
    for subject, questions in quiz_data.items():
        before_count = len(questions)
        filtered = [q for q in questions if q['ans'] != "?"]
        after_count = len(filtered)
        new_quiz_data[subject] = filtered
        
        for q in filtered:
            valid_images.add(os.path.basename(q['img']))
            
        print(f"✅ Môn {subject}: Đã xoá {before_count - after_count} câu lỗi, giữ lại {after_count} câu chuẩn.")

    # 2. Lưu lại data.js sạch
    with open(data_file, "w", encoding="utf-8") as f:
        f.write("const quizData = " + json.dumps(new_quiz_data, ensure_ascii=False) + ";")
    print("\n💾 Đã cập nhật data.js sạch sẽ.")

    # 3. Xoá ảnh thừa trong thư mục images
    img_dir = "images"
    word_dir = "images_word"
    
    deleted_count = 0
    if os.path.exists(img_dir):
        for f in os.listdir(img_dir):
            if f.endswith(".webp") and f not in valid_images:
                try:
                    os.remove(os.path.join(img_dir, f))
                    deleted_count += 1
                except: pass
    
    if os.path.exists(word_dir):
        for f in os.listdir(word_dir):
            if f.endswith(".png"):
                # Chuyển tên .png về .webp để check trong valid_images
                webp_name = f.replace(".png", ".webp")
                if webp_name not in valid_images:
                    try:
                        os.remove(os.path.join(word_dir, f))
                        deleted_count += 1
                    except: pass

    print(f"🗑️ Đã giải phóng không gian: Xoá {deleted_count} file ảnh rác/lỗi.")

if __name__ == "__main__":
    cleanup()
