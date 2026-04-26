import os, json, re, shutil

def cleanup_subject():
    print("\n--- HỆ THỐNG DỌN DẸP DỮ LIỆU FUOVERFLOW ---")
    subject_to_delete = input("👉 Nhập mã môn muốn xóa sạch (VD: MMA301 hoặc SDN302): ").strip().upper()
    
    if not subject_to_delete:
        print("❌ Bạn chưa nhập mã môn!")
        return

    data_file = "data.js"
    quizlet_file = "dataquiz.js"
    images_dir = "images"

    # 1. Xử lý data.js (Image-based subjects)
    if os.path.exists(data_file):
        with open(data_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        match = re.search(r"const\s+quizData\s*=\s*({.*});", content, re.DOTALL)
        if match:
            try:
                data_str = match.group(1)
                quiz_dict = json.loads(data_str)
                
                if subject_to_delete in quiz_dict:
                    questions = quiz_dict[subject_to_delete]
                    print(f"📦 Tìm thấy {len(questions)} câu hỏi trong data.js...")
                    
                    count_img = 0
                    for q in questions:
                        img_path = q.get("img", "")
                        if img_path and os.path.exists(img_path):
                            try:
                                os.remove(img_path)
                                count_img += 1
                            except: pass
                    
                    del quiz_dict[subject_to_delete]
                    print(f"✅ Đã xóa {count_img} tệp ảnh đồng bộ.")
                    
                    # Ghi lại file data.js mới
                    new_content = "const quizData = " + json.dumps(quiz_dict, ensure_ascii=False) + ";"
                    with open(data_file, "w", encoding="utf-8") as f:
                        f.write(new_content)
                else:
                    print(f"⚠️ Môn '{subject_to_delete}' không có trong data.js.")
            except Exception as e:
                print(f"❌ Lỗi đọc JSON từ data.js: {e}")
        else:
            print("❌ Không tìm thấy cấu trúc quizData trong data.js")

    # 2. Xử lý dataquiz.js (Text-based subjects từ Quizlet)
    # Vì dataquiz.js hiện tại là dạng mảng phẳng hardcoded cho SWD392v2
    if os.path.exists(quizlet_file):
        if subject_to_delete.lower() == "swd392v2":
            print(f"📦 Phát hiện môn học Quizlet ({quizlet_file}). Đang dọn dẹp...")
            # Xóa sạch file hoặc đưa về mảng rỗng
            try:
                new_quizlet_content = "const quizletData = [];"
                with open(quizlet_file, "w", encoding="utf-8") as f:
                    f.write(new_quizlet_content)
                print(f"✅ Đã xóa hoàn toàn dữ liệu trong {quizlet_file}")
            except Exception as e:
                print(f"❌ Lỗi khi xóa {quizlet_file}: {e}")
        else:
            print(f"ℹ️ {quizlet_file} tồn tại nhưng không khớp mã môn Quizlet (SWD392v2). Bỏ qua.")

    # 3. Quét thêm một lần nữa trong thư mục images theo tên file (Đảm bảo sạch 100%)
    print(f"🔍 Đang quét sâu tệp rác trong thư mục '{images_dir}'...")
    additional_deleted = 0
    if os.path.exists(images_dir):
        # Chuyển tên môn về dạng snake_case để quét file (vd: MMA301 -> mma301)
        prefix = subject_to_delete.lower()
        for filename in os.listdir(images_dir):
            if filename.lower().startswith(prefix):
                try:
                    os.remove(os.path.join(images_dir, filename))
                    additional_deleted += 1
                except: pass
    
    if additional_deleted > 0:
        print(f"✅ Đã dọn dẹp thêm {additional_deleted} tệp rác liên quan.")

    # 3. Ghi lại file data.js mới
    new_content = "const quizData = " + json.dumps(quiz_dict, ensure_ascii=False) + ";"
    with open(data_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"\n✨ HOÀN TẤT: Môn {subject_to_delete} đã được xóa sổ hoàn toàn.")

if __name__ == "__main__":
    cleanup_subject()
