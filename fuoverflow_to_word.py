import os, csv
from docx import Document
from docx.shared import Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

def create_word_guide():
    csv_file = "output_full_final.csv" # Dữ liệu chúng ta vừa quét
    output_docx = "Giai_De_FuOverflow.docx"
    images_dir = "images"
    
    if not os.path.exists(csv_file):
        print(f"❌ Không tìm thấy file {csv_file}. Bạn hãy chạy scraper trước!")
        return

    doc = Document()
    doc.add_heading('BỘ ĐỀ THI TỔNG HỢP FUOVERFLOW', 0)
    
    print(f"📄 Đang tạo file Word từ {csv_file}...")
    
    with open(csv_file, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter="\t")
        for i, row in enumerate(reader, 1):
            if not row: continue
            
            # 1. Đánh số câu
            doc.add_heading(f'Câu {i}', level=1)
            
            # 2. Chèn ảnh (Dựa vào quy tắc đặt tên deX_qYYY.webp)
            # Vì đề thi của bạn có 4 URL, mỗi URL 50 câu:
            de_num = (i - 1) // 50 + 1
            q_num = (i - 1) % 50 + 1
            img_n = f"de{de_num}_q{str(q_num).zfill(3)}.webp"
            img_p = os.path.join(images_dir, img_n)
            
            if os.path.exists(img_p):
                doc.add_picture(img_p, width=Inches(5.0))
                # Căn giữa ảnh
                last_p = doc.paragraphs[-1]
                last_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                doc.add_paragraph(f"[Lỗi: Không tìm thấy ảnh {img_n}]")
            
            # 3. Chèn đáp án
            ans_text = row[1] if len(row) > 1 else "Answer: ?"
            p = doc.add_paragraph()
            run = p.add_run(f"👉 {ans_text}")
            run.bold = True
            run.font.size = 140000 # Cỡ chữ 14 (khoảng đó)
            
            # Thêm đường kẻ ngang ngăn cách các câu
            doc.add_paragraph("_" * 50)

    doc.save(output_docx)
    print(f"✅ ĐÃ XONG! Mời bạn mở file: {os.path.abspath(output_docx)}")

if __name__ == "__main__":
    create_word_guide()
