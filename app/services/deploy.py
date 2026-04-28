import os
import shutil
import json
import subprocess
import tempfile
import asyncio
from typing import Dict, List, Any, Optional
from app.services import storage
from app.core.logger import logger
from app.core.state import state

async def broadcast_deploy_log(message: str, type: str = "info"):
    """Gửi log qua WebSocket với icon/màu sắc tương ứng."""
    from app.services.crawler import broadcast_log
    icon = "🚀"
    if type == "error": icon = "❌"
    if type == "success": icon = "✅"
    if type == "warning": icon = "⚠️"
    
    await broadcast_log(f"{icon} [DEPLOY] {message}")

def run_git_cmd(args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Helper chạy lệnh git và bắt lỗi chi tiết."""
    try:
        result = subprocess.run(
            args, 
            cwd=cwd, 
            capture_output=True, 
            text=True, 
            check=True
        )
        return result
    except subprocess.CalledProcessError as e:
        error_msg = f"Git Error: {' '.join(args)}\nStdout: {e.stdout}\nStderr: {e.stderr}"
        logger.error(error_msg)
        raise Exception(f"Git operation failed: {e.stderr.strip() or e.stdout.strip()}")

def validate_environment() -> List[str]:
    """Kiểm tra các file bắt buộc phải có trước khi deploy."""
    required = ["index.html", "data.js", "icon.svg", "manifest.json", "sw.js"]
    missing = [f for f in required if not os.path.exists(f)]
    return missing

def get_subject_package_info(subject: str) -> Dict[str, Any]:
    """Tính toán thống kê và kiểm tra tính toàn vẹn của dữ liệu môn học."""
    data = storage.load_quiz_data()
    if subject not in data:
        return {"error": f"Subject '{subject}' not found in data.js"}
    
    questions = data[subject]
    if not questions:
        return {"error": f"Subject '{subject}' has no questions."}

    image_paths = set()
    for q in questions:
        if isinstance(q, dict) and q.get("img"):
            image_paths.add(q["img"])
    
    # Files tĩnh
    static_files = ["index.html", "icon.svg", "manifest.json", "sw.js", "requirements.txt"]
    total_size = 0
    found_statics = []
    for f in static_files:
        if os.path.exists(f):
            total_size += os.path.getsize(f)
            found_statics.append(f)
    
    # Images
    found_images = []
    missing_images = []
    for img in image_paths:
        if os.path.exists(img):
            total_size += os.path.getsize(img)
            found_images.append(img)
        else:
            missing_images.append(img)
            
    return {
        "subject": subject,
        "question_count": len(questions),
        "image_count": len(found_images),
        "missing_images": len(missing_images),
        "missing_list": missing_images,
        "estimated_size": total_size,
        "file_list": found_statics + ["data.js"] + found_images,
        "integrity": "OK" if not missing_images else "WARNING"
    }

async def build_and_push_to_github(subject: str, force: bool = False, rename_images: bool = False):
    """
    Harden Deployment Process:
    - Chặt chẽ về Cleanup
    - Bắt lỗi Git & Files chi tiết
    - Validate integrity trước khi push
    """
    branch_name = f"deploy/{subject}"
    # Dùng đường dẫn tuyệt đối cho temp_dir để tránh lỗi path trên Windows
    temp_root = tempfile.gettempdir()
    unique_suffix = f"{subject}_{int(asyncio.get_event_loop().time())}"
    temp_dir = os.path.join(temp_root, f"fu_deploy_{unique_suffix}")
    
    try:
        # --- PHASE 0: VALIDATION ---
        await broadcast_deploy_log(f"Đang kiểm tra hệ thống cho môn {subject}...")
        missing_sys_files = validate_environment()
        if missing_sys_files:
            raise Exception(f"Thiếu file hệ thống quan trọng: {', '.join(missing_sys_files)}")

        info = get_subject_package_info(subject)
        if "error" in info:
            raise Exception(info["error"])
            
        if info["missing_images"] > 0:
            await broadcast_deploy_log(f"Phát hiện {info['missing_images']} ảnh bị thiếu. Vẫn tiếp tục build...", "warning")

        # --- PHASE 1: GIT BRANCH SETUP ---
        await broadcast_deploy_log(f"Đang thiết lập branch `{branch_name}`...")
        
        # Kiểm tra branch tồn tại
        try:
            run_git_cmd(["git", "rev-parse", "--verify", branch_name])
            branch_exists = True
        except:
            branch_exists = False

        if not branch_exists:
            await broadcast_deploy_log(f"Tạo branch mới: {branch_name}")
            run_git_cmd(["git", "branch", branch_name])
        elif not force:
            await broadcast_deploy_log(f"Branch `{branch_name}` đã tồn tại. Đang cập nhật...", "info")

        # --- PHASE 2: WORKTREE SETUP ---
        await broadcast_deploy_log("Khởi tạo Git Worktree (Isolated Build)...")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            
        run_git_cmd(["git", "worktree", "add", temp_dir, branch_name])

        # --- PHASE 3: CONTENT PREPARATION ---
        await broadcast_deploy_log("Làm sạch thư mục triển khai...")
        # Xóa hết file cũ trong branch (giữ lại .git)
        for item in os.listdir(temp_dir):
            if item == ".git": continue
            path = os.path.join(temp_dir, item)
            if os.path.isdir(path): shutil.rmtree(path)
            else: os.remove(path)

        # Trích xuất dữ liệu
        await broadcast_deploy_log("Trích xuất và chuẩn hóa data.js...")
        data = storage.load_quiz_data()
        subject_data = {subject: data.get(subject, [])}
        if "_metadata" in data:
            subject_data["_metadata"] = {subject: data["_metadata"].get(subject, {})}

        # Copy Static Files
        static_files = ["index.html", "icon.svg", "manifest.json", "sw.js", "requirements.txt"]
        for f in static_files:
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(temp_dir, f))
            else:
                await broadcast_deploy_log(f"Bỏ qua file không tồn tại: {f}", "info")

        # Copy & Flatten Images
        img_folder = "image" if rename_images else "images"
        dest_img_dir = os.path.join(temp_dir, img_folder)
        os.makedirs(dest_img_dir, exist_ok=True)
        
        await broadcast_deploy_log(f"Copy và cấu trúc lại hình ảnh vào `{img_folder}/`...")
        new_questions = []
        copied_count = 0
        for q in subject_data[subject]:
            new_q = q.copy()
            src_img = q.get("img")
            if src_img and os.path.exists(src_img):
                fname = os.path.basename(src_img)
                shutil.copy2(src_img, os.path.join(dest_img_dir, fname))
                new_q["img"] = f"{img_folder}/{fname}"
                copied_count += 1
            new_questions.append(new_q)
        
        subject_data[subject] = new_questions
        
        # Ghi data.js final
        with open(os.path.join(temp_dir, "data.js"), "w", encoding="utf-8") as f:
            f.write(f"const quizData = {json.dumps(subject_data, ensure_ascii=False)};")

        # --- PHASE 4: GIT PUSH ---
        await broadcast_deploy_log(f"Đang commit {copied_count} ảnh và file hệ thống...")
        run_git_cmd(["git", "add", "."], cwd=temp_dir)
        
        status = run_git_cmd(["git", "status", "--porcelain"], cwd=temp_dir)
        if not status.stdout.strip():
            await broadcast_deploy_log("Không có thay đổi nào cần cập nhật.", "warning")
        else:
            run_git_cmd(["git", "commit", "-m", f"Automated deploy: {subject} build"], cwd=temp_dir)
            
            await broadcast_deploy_log(f"Đang đẩy code lên GitHub branch `{branch_name}`...")
            push_args = ["git", "push", "origin", branch_name]
            if force: push_args.append("--force")
            
            run_git_cmd(push_args, cwd=temp_dir)
            
            # Bonus: Đề xuất URL (Giả định GitHub Pages hoặc Netlify convention)
            try:
                remote_url = run_git_cmd(["git", "remote", "get-url", "origin"]).stdout.strip()
                if "github.com" in remote_url:
                    parts = remote_url.split("/")
                    user = parts[-2].split(":")[-1]
                    repo = parts[-1].replace(".git", "")
                    preview_url = f"https://{user}.github.io/{repo}/"
                    await broadcast_deploy_log(f"Dự đoán Preview URL: {preview_url} (Nếu dùng GH Pages)", "success")
            except:
                pass
                
            await broadcast_deploy_log(f"Hoàn tất! Đã cập nhật môn {subject} thành công.", "success")

    except Exception as e:
        await broadcast_deploy_log(f"Lỗi quy trình Deploy: {str(e)}", "error")
        logger.error(f"HARDENED DEPLOY ERROR: {e}")
    finally:
        # --- PHASE 5: CLEANUP (IMPORTANT) ---
        await broadcast_deploy_log("Đang dọn dẹp worktree và folder tạm...")
        try:
            # Dùng --force để xóa worktree ngay cả khi có commit chưa push (đã được bọc trong try/finally)
            subprocess.run(["git", "worktree", "remove", "--force", temp_dir], capture_output=True)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as cleanup_err:
            logger.error(f"Cleanup error: {cleanup_err}")
            
        await broadcast_deploy_log("🏁 Kết thúc phiên làm việc.")
