"""
API Layer - Phần 1: Config, Status, Data Management (CRUD subjects/items).
"""
import asyncio
import copy
import json
import os
import uuid
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, Response

from app.core.state import state
from app.schema import AppConfig, AuditRequest, BulkDeleteRequest
from app.services import storage
from app.services.crawler import broadcast_log
from app.utils.helpers import get_source_mapping, get_source_score
from app.services import deploy as deploy_svc

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_source_mapping() -> Dict[str, str]:
    cfg = storage.load_config()
    return get_source_mapping(cfg)


# ─── HTML Pages ───────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/admin.html", response_class=HTMLResponse)
async def read_admin():
    with open("admin.html", "r", encoding="utf-8") as f:
        html = f.read()
    mapping = _get_source_mapping()
    inject = f"<script>window.globalSourceMapping = {json.dumps(mapping, ensure_ascii=False)};</script>"
    return HTMLResponse(html.replace("</head>", f"{inject}</head>"))


@router.get("/index.html", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()
    mapping = _get_source_mapping()
    inject = f"<script>window.globalSourceMapping = {json.dumps(mapping, ensure_ascii=False)};</script>"
    return HTMLResponse(html.replace("</head>", f"{inject}</head>"))


@router.get("/deploy.html", response_class=HTMLResponse)
async def read_deploy():
    with open("deploy.html", "r", encoding="utf-8") as f:
        html = f.read()
    mapping = _get_source_mapping()
    inject = f"<script>window.globalSourceMapping = {json.dumps(mapping, ensure_ascii=False)};</script>"
    return HTMLResponse(html.replace("</head>", f"{inject}</head>"))


@router.get("/data.js")
async def get_data_js():
    return FileResponse("data.js")

@router.get("/dataquiz.js")
async def get_dataquiz_js():
    if os.path.exists("dataquiz.js"):
        return FileResponse("dataquiz.js")
    return Response(content="const quizletData = {};", media_type="application/javascript")

@router.get("/sw.js")
async def get_sw_js():
    return FileResponse("sw.js", media_type="application/javascript")

@router.get("/manifest.json")
async def get_manifest():
    return FileResponse("manifest.json")

@router.get("/icon.svg")
async def get_icon():
    return FileResponse("icon.svg")


# ─── Config ───────────────────────────────────────────────────────────────────

@router.get("/api/config")
async def get_config():
    return storage.load_config()


@router.post("/api/config")
async def update_config(config: AppConfig):
    storage.save_config(config.model_dump())
    return {"message": "Config updated"}


@router.get("/api/source_mapping")
async def api_source_mapping():
    return _get_source_mapping()


# ─── Status & Logs ────────────────────────────────────────────────────────────

@router.get("/api/status")
async def get_status():
    is_running = state.is_crawler_running
    if not is_running:
        state.clear_process()
    return {
        "running": is_running,
        "start_time": datetime.fromtimestamp(state.start_time).isoformat() if state.start_time and is_running else None,
        "uptime": int(__import__("time").time() - state.start_time) if state.start_time and is_running else 0,
        "pid": state.process.pid if is_running else None,
        "workflow_running": state.workflow_running,
    }


@router.get("/api/ai/clean/results")
async def get_ai_results():
    return {"results": state.ai_results, "progress": state.ai_progress}


# ─── Subjects List ────────────────────────────────────────────────────────────

@router.get("/api/data/subjects")
async def list_subjects():
    data = storage.load_quiz_data()
    mapping = _get_source_mapping()
    subjects = []
    metadata = data.get("_metadata", {})
    for name, questions in data.items():
        if name == "_metadata" or not isinstance(questions, list):
            continue
        sources_in_subject = list(set(q.get("source_id") for q in questions if q.get("source_id")))
        display_sources = sorted(
            [mapping.get(s, s) for s in sources_in_subject],
            key=get_source_score, reverse=True
        )
        subjects.append({
            "name": name,
            "count": len(questions),
            "sources": display_sources,
            "last_updated": datetime.now().isoformat(),
        })
    return {"subjects": subjects, "metadata": metadata}


# ─── Subject Detail ───────────────────────────────────────────────────────────

@router.get("/api/data/subjects/{name}")
async def get_subject_data(
    name: str, page: int = 1, limit: int = 12,
    filter_type: str = "all", source_id: str = ""
):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")

    questions = data[name]
    mapping = _get_source_mapping()

    # Auto-assign IDs
    changed = False
    for q in questions:
        if isinstance(q, dict) and not q.get("id"):
            q["id"] = str(uuid.uuid4())[:8]
            changed = True
    if changed:
        await storage.save_quiz_data(data, backup=False)

    processed = []
    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        raw_sid = q.get("source_id", "")
        display_sid = mapping.get(raw_sid, raw_sid)
        if source_id and display_sid != source_id:
            continue
        img_path = q.get("img", "")
        img_exists = os.path.exists(img_path) if img_path else False
        error = None
        if not img_path:
            error = "missing_image"
        elif not img_exists:
            error = "image_not_found"
        elif not q.get("ans"):
            error = "missing_answer"
        q_item = {**q, "original_index": idx, "error": error, "source_id": display_sid}
        if filter_type == "error" and not error:
            continue
        if filter_type == "valid" and error:
            continue
        processed.append(q_item)

    processed.sort(key=lambda x: (get_source_score(x.get("source_id", "")), x.get("img", "")), reverse=True)

    total = len(processed)
    start = (page - 1) * limit
    raw_sources = list(set(q.get("source_id") for q in questions if isinstance(q, dict) and q.get("source_id")))
    sources = sorted(list(set(mapping.get(rs, rs) for rs in raw_sources)), key=get_source_score, reverse=True)

    return {"items": processed[start:start+limit], "total": total, "page": page, "limit": limit, "sources": sources}


# ─── Delete Subject ───────────────────────────────────────────────────────────

@router.delete("/api/data/subjects/{name}")
async def delete_subject(name: str):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")

    subject_items = data.get(name, [])
    target_imgs = {item["img"] for item in subject_items if isinstance(item, dict) and item.get("img")}
    used_by_others = {item["img"] for k, v in data.items() if k != name and k != "_metadata" for item in (v if isinstance(v, list) else []) if isinstance(item, dict) and item.get("img")}
    orphaned = target_imgs - used_by_others

    deleted_count = 0
    for img_path in orphaned:
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
                deleted_count += 1
            except Exception:
                pass

    del data[name]
    await storage.save_quiz_data(data)
    return {"message": f"Deleted {name}", "images_cleaned": deleted_count}


# ─── Item CRUD ────────────────────────────────────────────────────────────────

@router.patch("/api/data/subjects/{name}/items/{item_id}")
async def update_quiz_item(name: str, item_id: str, update_data: dict):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    for q in data[name]:
        if isinstance(q, dict) and str(q.get("id")) == item_id:
            q.update(update_data)
            await storage.save_quiz_data(data, backup=False)
            return {"message": "Updated"}
    raise HTTPException(status_code=404, detail="Item not found")


@router.delete("/api/data/subjects/{name}/items/{item_id}")
async def delete_quiz_item(name: str, item_id: str):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    new_list = [q for q in data[name] if str(q.get("id")) != item_id]
    if len(new_list) < len(data[name]):
        data[name] = new_list
        await storage.save_quiz_data(data, backup=False)
        if state.ai_results:
            state.ai_results["exact"] = [p for p in state.ai_results.get("exact", []) if str(p[0]) != item_id and str(p[1]) != item_id]
            state.ai_results["fuzzy"] = [p for p in state.ai_results.get("fuzzy", []) if str(p[0]) != item_id and str(p[1]) != item_id]
        return {"message": "Deleted"}
    raise HTTPException(status_code=404, detail="Item not found")


@router.post("/api/data/subjects/{name}/items/bulk_delete")
async def bulk_delete_items(name: str, req: BulkDeleteRequest):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
    ids = [str(i) for i in req.ids]
    original = len(data[name])
    data[name] = [q for q in data[name] if not isinstance(q, dict) or str(q.get("id")) not in ids]
    deleted = original - len(data[name])
    await storage.save_quiz_data(data, backup=False)
    if state.ai_results:
        state.ai_results["exact"] = [p for p in state.ai_results.get("exact", []) if str(p[0]) not in ids and str(p[1]) not in ids]
        state.ai_results["fuzzy"] = [p for p in state.ai_results.get("fuzzy", []) if str(p[0]) not in ids and str(p[1]) not in ids]
    return {"message": f"Deleted {deleted} items", "count": deleted}


# ─── Subject Management ───────────────────────────────────────────────────────

@router.post("/api/data/subjects/{name}/visibility")
async def toggle_visibility(name: str, hidden: bool):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Not found")
    data.setdefault("_metadata", {}).setdefault(name, {})["hidden"] = hidden
    await storage.save_quiz_data(data, backup=False)
    return {"message": "Updated"}


@router.post("/api/data/subjects/{name}/clone")
async def clone_subject(name: str, new_name: str):
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Not found")
    if new_name in data:
        raise HTTPException(status_code=400, detail="Name exists")
    data[new_name] = copy.deepcopy(data[name])
    await storage.save_quiz_data(data)
    return {"message": "Cloned"}


# ─── Stats ────────────────────────────────────────────────────────────────────

@router.get("/api/data/stats")
async def get_stats():
    data = storage.load_quiz_data()
    total_subjects = sum(1 for k in data if k != "_metadata")
    total_questions = sum(len(v) for k, v in data.items() if k != "_metadata" and isinstance(v, list))
    ans_dist = {"A": 0, "B": 0, "C": 0, "D": 0, "Other": 0}
    for k, qs in data.items():
        if k == "_metadata" or not isinstance(qs, list):
            continue
        for q in qs:
            if isinstance(q, dict):
                ans = (q.get("ans") or "Other").upper()
                ans_dist[ans] = ans_dist.get(ans, 0) + 1 if ans in ans_dist else ans_dist.setdefault("Other", ans_dist["Other"] + 1)
    return {"total_subjects": total_subjects, "total_questions": total_questions, "answer_distribution": ans_dist}


# ─── Backups & Drafts ─────────────────────────────────────────────────────────

@router.get("/api/backups")
async def list_backups():
    return {"backups": storage.list_backups()}


@router.post("/api/backups/{filename}/restore")
async def restore_backup(filename: str):
    if storage.restore_backup(filename):
        return {"message": "Restored"}
    raise HTTPException(status_code=404, detail="Backup not found")


@router.get("/api/drafts")
async def list_drafts():
    from app.core.config import DRAFTS_DIR as D
    if not os.path.exists(D):
        return {"drafts": []}
    files = [f for f in os.listdir(D) if f.endswith(".json")]
    result = []
    # Các loại tool_type hợp lệ
    valid_types = ["ai_audit", "smart_audit", "audit", "clean", "trash", "classify"]
    
    for f in files:
        name_no_ext = f.replace(".json", "")
        found = False
        for t in valid_types:
            if name_no_ext.endswith("_" + t):
                subject = name_no_ext[:-(len(t)+1)]
                result.append({
                    "subject": subject, 
                    "type": t, 
                    "file": f, 
                    "mtime": os.path.getmtime(os.path.join(D, f))
                })
                found = True
                break
        
        if not found:
            # Fallback cho các loại khác
            parts = name_no_ext.rsplit("_", 1)
            if len(parts) == 2:
                result.append({"subject": parts[0], "type": parts[1], "file": f, "mtime": os.path.getmtime(os.path.join(D, f))})
    return {"drafts": result}


@router.get("/api/drafts/{subject}/{tool_type}")
async def get_draft_content(subject: str, tool_type: str):
    data = storage.load_draft(subject, tool_type)
    if not data:
        raise HTTPException(status_code=404, detail="Draft not found")
    return data


@router.delete("/api/drafts/{subject}/{tool_type}")
async def remove_draft(subject: str, tool_type: str):
    storage.delete_draft(subject, tool_type)
    return {"message": "Deleted"}


# ─── Cleanup ──────────────────────────────────────────────────────────────────

@router.post("/api/cleanup")
async def cleanup_data():
    try:
        storage.create_named_backup("global_cleanup")
        import subprocess as sp
        sp.run(["python", "cleanup_data.py"], check=True)
        return {"message": "Cleanup done"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# CRAWLER ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

import time as _time
from app.services import crawler as crawler_svc


@router.post("/api/start")
async def start_scraper(request: dict, background_tasks: BackgroundTasks):
    if state.is_crawler_running:
        raise HTTPException(status_code=400, detail="Crawler đang chạy")
    if state.workflow_running:
        raise HTTPException(status_code=400, detail="Hệ thống đang xử lý")

    options = request.get("options", {})
    cfg = storage.load_config()
    all_sources = {s["id"]: s for s in cfg.get("SOURCES", [])}
    selected = [all_sources[sid] for sid in request.get("source_ids", []) if sid in all_sources]
    if not selected:
        raise HTTPException(status_code=400, detail="Không có source hợp lệ")

    state.start_time = _time.time()
    state.logs = []

    async def run_workflow():
        state.workflow_running = True
        try:
            processed_subjects = set()
            for source in selected:
                await crawler_svc.start_single_crawl(source["url"], source["subject"], source["id"])
                processed_subjects.add(source["subject"])

            for subject in processed_subjects:
                if options.get("ai_lab"):
                    await broadcast_log(f"🧠 [AI LAB] OCR cho {subject}...")
                    await _ai_clean_worker(subject, mode="scan_only_ocr")
                if options.get("trash_filter"):
                    await _trash_filter_worker(subject)
                if options.get("ai_clean"):
                    await _ai_clean_worker(subject, mode="scan")

            await broadcast_log("✨ HOÀN TẤT TẤT CẢ SOURCES!")
        finally:
            state.workflow_running = False

    background_tasks.add_task(run_workflow)
    return {"message": f"Đã bắt đầu {len(selected)} sources"}


@router.post("/api/stop")
async def stop_scraper():
    await crawler_svc.stop_crawl()
    return {"message": "Đã dừng"}


# ═══════════════════════════════════════════════════════════════════════════════
# AI ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

from app.services import ai as ai_svc
from difflib import SequenceMatcher
import hashlib, re


@router.get("/api/ai/test")
async def test_ai():
    return await ai_svc.test_connection()


@router.post("/api/ai/classify/{name}")
async def start_classify(name: str, background_tasks: BackgroundTasks, source_id: str = None):
    background_tasks.add_task(_ai_classify_worker, name, source_id)
    return {"message": "Classification started"}


@router.post("/api/ai/clean/{name}/scan")
async def start_clean_scan(name: str, background_tasks: BackgroundTasks):
    if state.is_crawler_running:
        raise HTTPException(status_code=400, detail="Crawler đang chạy")
    state.ai_results = None
    state.ai_progress = {"current": 0, "total": 0, "status": "Starting..."}
    background_tasks.add_task(_ai_clean_worker, name)
    return {"message": "AI scan started"}


@router.post("/api/ai/clean/{name}/test")
async def start_clean_test(name: str, background_tasks: BackgroundTasks):
    state.ai_results = None
    state.ai_progress = {"current": 0, "total": 3, "status": "Quick test..."}
    background_tasks.add_task(_ai_clean_worker, name, 3)
    return {"message": "Quick test started"}


@router.post("/api/ai/clean/{name}/reanalyze")
async def reanalyze(name: str, background_tasks: BackgroundTasks):
    state.ai_progress = {"current": 0, "total": 100, "status": "Re-analyzing..."}
    background_tasks.add_task(_ai_clean_worker, name, None, "reanalyze")
    return {"message": "Re-analysis started"}


    return {"message": f"Đã tạo {new_name}", "new_subject": new_name, "count": len(clean)}


@router.post("/api/ai/clean/{name}/apply")
async def apply_clean(name: str, changes: dict):
    """
    Phê duyệt các thay đổi từ Review Center (Clean draft).
    changes format: { q_id: { "action": "accept_ai"|"keep_old"|"delete", "aiAns": "...", "questionText": "..." } }
    Hoặc cho Duplicates: { "ids": [id1, id2], "action": "merge" }
    """
    storage.create_named_backup(f"{name}_pre_clean_apply")
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Subject not found")
        
    id_map = {str(q.get("id")): q for q in data[name] if isinstance(q, dict)}
    updated = 0
    deleted = 0
    
    # 1. Xử lý các thay đổi đơn lẻ (Conflicts / AI Changes)
    for q_id, decision in changes.items():
        if q_id == "duplicates" or not isinstance(decision, dict):
            continue
            
        if q_id in id_map:
            action = decision.get("action")
            if action == "accept_ai":
                if decision.get("aiAns"):
                    id_map[q_id]["ans"] = decision["aiAns"]
                if decision.get("questionText"):
                    id_map[q_id]["questionText"] = decision["questionText"]
                id_map[q_id]["is_audited"] = True
                updated += 1
            elif action == "delete":
                # Đánh dấu để xóa sau
                id_map[q_id]["_to_delete"] = True
                deleted += 1
            elif action == "keep_old":
                id_map[q_id]["is_audited"] = True
                updated += 1

    # 2. Xử lý trùng lặp (nếu có gửi kèm)
    if "duplicates" in changes:
        for dupe_decision in changes["duplicates"]:
            keep_id = dupe_decision.get("keep")
            remove_ids = dupe_decision.get("remove", [])
            for rid in remove_ids:
                if rid in id_map:
                    id_map[rid]["_to_delete"] = True
                    deleted += 1

    # Thực hiện xóa các item đã đánh dấu
    if deleted > 0:
        data[name] = [q for q in data[name] if not q.get("_to_delete")]

    await storage.save_quiz_data(data)
    storage.delete_draft(name, "clean")
    
    return {
        "message": f"Đã cập nhật {updated} câu, xóa {deleted} câu trùng lặp.",
        "updated": updated,
        "deleted": deleted
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/audit/subjects/{name}/scan")
async def start_audit(name: str, req: AuditRequest, background_tasks: BackgroundTasks):
    if state.is_crawler_running:
        raise HTTPException(status_code=400, detail="Crawler đang chạy")
    state.audit_results = None
    # Xóa bản nháp cũ để không bị show dữ liệu cũ khi đang chạy scan mới
    storage.delete_draft(name, "audit")
    background_tasks.add_task(_audit_worker, name, req.urls)
    return {"message": "Audit started"}


@router.get("/api/audit/subjects/{name}/results")
async def get_audit_results(name: str):
    return storage.load_draft(name, "audit") or state.audit_results


@router.post("/api/audit/subjects/{name}/apply")
async def apply_audit(name: str, changes: dict):
    storage.create_named_backup(f"{name}_pre_audit")
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404)
    id_map = {str(q.get("id")): q for q in data[name] if isinstance(q, dict)}
    updated = 0
    for q_id, new_ans in changes.items():
        if q_id in id_map:
            # Extract answer value: handle both string and dict (full audit object) formats
            ans_value = new_ans
            if isinstance(new_ans, dict):
                ans_value = new_ans.get("suggested", "?")
            if isinstance(ans_value, str) and ans_value.strip():
                id_map[q_id]["ans"] = ans_value.strip()
                id_map[q_id]["is_audited"] = True
                updated += 1
    await storage.save_quiz_data(data)
    state.audit_results = None
    storage.delete_draft(name, "audit")
    return {"message": f"Applied {updated} changes"}


@router.post("/api/audit/subjects/{name}/smart/start")
async def start_smart(name: str, req: AuditRequest, background_tasks: BackgroundTasks):
    if state.is_crawler_running:
        raise HTTPException(status_code=400, detail="Crawler đang chạy")
    # Xóa bản nháp cũ trừ khi yêu cầu skip_mining (vì skip_mining cần dùng lại draft cũ)
    if not req.skip_mining:
        storage.delete_draft(name, "smart_audit")
    background_tasks.add_task(_smart_refine_worker, name, req.urls, req.skip_mining)
    return {"message": "Smart Refine started"}


@router.get("/api/audit/subjects/{name}/smart/results")
async def get_smart_results(name: str):
    return storage.load_draft(name, "smart_audit")


@router.post("/api/audit/subjects/{name}/smart/apply")
async def apply_smart(name: str, changes: dict):
    storage.create_named_backup(f"{name}_pre_smart")
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404)
    id_map = {str(q.get("id")): q for q in data[name] if isinstance(q, dict)}
    updated = 0
    for q_id, ans in changes.items():
        if q_id in id_map:
            # Extract answer value: handle both string and dict (full smart audit object) formats
            ans_value = ans
            if isinstance(ans, dict):
                ans_value = ans.get("final_ans", "?")
            if isinstance(ans_value, str) and ans_value.strip():
                id_map[q_id]["ans"] = ans_value.strip()
                id_map[q_id]["is_smart_audited"] = True
                updated += 1
    await storage.save_quiz_data(data)
    storage.delete_draft(name, "smart_audit")
    return {"message": f"Applied {updated} changes"}


@router.get("/api/audit/subjects/{name}/threads")
async def get_threads(name: str):
    data = storage.load_quiz_data()
    actual_key = name
    if actual_key not in data:
        return []
    cfg = storage.load_config()
    sources = cfg.get("SOURCES", [])
    cfg_urls = [s["url"] for s in sources if s.get("subject") == name and s.get("enabled")]
    await broadcast_log(f"🔍 [get_threads] Mapping: {name} -> {actual_key} | Tìm thấy {len(cfg_urls)} sources trong config")
    threads = {}
    for q in data[actual_key]:
        if not isinstance(q, dict):
            continue
        fn = os.path.basename(q.get("img", ""))
        for url in cfg_urls:
            slug = url.rstrip("/").split("/")[-1]
            safe_slug = re.sub(r"[^a-zA-Z0-9]", "_", slug)
            if safe_slug in fn:
                threads.setdefault(url, {"url": url, "slug": slug, "count": 0})["count"] += 1
                break
    await broadcast_log(f"📍 [get_threads] Kết quả: {len(threads)} threads có ảnh trùng khớp.")
    return list(threads.values())


# ═══════════════════════════════════════════════════════════════════════════════
# DEPLOYMENT ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/deploy/preview/{subject}")
async def get_deploy_preview(subject: str):
    info = deploy_svc.get_subject_package_info(subject)
    if "error" in info:
        raise HTTPException(status_code=404, detail=info["error"])
    return info

@router.post("/api/deploy/push/{subject}")
async def push_to_github(subject: str, background_tasks: BackgroundTasks, force: bool = False, rename_images: bool = False):
    background_tasks.add_task(deploy_svc.build_and_push_to_github, subject, force, rename_images)
    return {"message": "Deployment process started in background"}


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET
# ═══════════════════════════════════════════════════════════════════════════════

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    state.active_websockets.append(websocket)
    for log in state.logs[-100:]:
        await websocket.send_text(log)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        try:
            state.active_websockets.remove(websocket)
        except ValueError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND WORKER FUNCTIONS (chạy trong BackgroundTasks)
# ═══════════════════════════════════════════════════════════════════════════════

async def _trash_filter_worker(subject_name: str):
    data = storage.load_quiz_data()
    if subject_name not in data:
        return
    
    trash_items = []
    for q in data[subject_name]:
        if not isinstance(q, dict): continue
        img_exists = os.path.exists(q.get("img", ""))
        is_bad = False
        reason = ""
        
        if not q.get("img") or not img_exists:
            is_bad = True
            reason = "Missing image"
        elif not q.get("ans") or q["ans"] == "?":
            is_bad = True
            reason = "No answer"
            
        if is_bad:
            trash_items.append({**q, "reason": reason, "type": "trash"})
            
    if trash_items:
        storage.save_draft(subject_name, "trash", trash_items)
        await broadcast_log(f"🗑️ Trash Filter: Tìm thấy {len(trash_items)} câu rác trong {subject_name}. Vui lòng Review.")
    else:
        await broadcast_log(f"✅ Trash Filter: {subject_name} không có câu rác.")


async def _ai_classify_worker(subject_name: str, source_id: str = None):
    try:
        data = storage.load_quiz_data()
        questions = data.get(subject_name, [])
        to_process = [i for i, q in enumerate(questions)
                      if isinstance(q, dict) and (not source_id or q.get("source_id") == source_id)]
        state.ai_progress = {"current": 0, "total": len(to_process), "status": "Classifying..."}
        batch_size = 3
        for i in range(0, len(to_process), batch_size):
            chunk = to_process[i:i + batch_size]
            paths = [questions[idx]["img"] for idx in chunk if questions[idx].get("img")]
            prompt = "Classify each image: 'TF' (True/False) or 'MCQ' (A/B/C/D). Return JSON: {\"results\": [{\"type\": \"TF\" or \"MCQ\"}]}"
            results = await ai_svc.extract_text_from_images(paths, prompt)
            for j, res in enumerate(results):
                if j < len(chunk):
                    questions[chunk[j]]["type"] = res.get("type", "MCQ")
            state.ai_progress["current"] += len(chunk)
            await storage.save_quiz_data(data, backup=False)
            await asyncio.sleep(5)
        state.ai_progress["status"] = "Done"
        await broadcast_log(f"Classification xong: {subject_name}")
    except Exception as e:
        state.ai_progress["status"] = "Error"
        await broadcast_log(f"Classify error: {e}")


async def _ai_clean_worker(subject_name: str, limit: int = None, mode: str = "scan"):
    try:
        data = storage.load_quiz_data()
        all_qs = data.get(subject_name, [])

        # Ensure IDs
        changed = False
        for q in all_qs:
            if isinstance(q, dict) and not q.get("id"):
                q["id"] = str(uuid.uuid4())[:8]
                changed = True
        if changed:
            await storage.save_quiz_data(data, backup=False)

        questions = all_qs[:limit] if limit else all_qs
        total = len(questions)
        state.ai_progress["total"] = total

        # Phase 1: Hash check - run in executor to avoid blocking event loop
        exact_dupes = []
        if mode == "scan":
            def _compute_hashes():
                hashes = {}
                dupes = []
                for q in questions:
                    img = q.get("img", "")
                    if os.path.exists(img):
                        with open(img, "rb") as f:
                            h = hashlib.md5(f.read()).hexdigest()
                        if h in hashes:
                            dupes.append((hashes[h], q.get("id")))
                        else:
                            hashes[h] = q.get("id")
                return dupes

            loop = asyncio.get_running_loop()
            exact_dupes = await loop.run_in_executor(None, _compute_hashes)
            await broadcast_log(f"Hash done: {len(exact_dupes)} exact dupes")

        # Phase 2: Local OCR
        if mode in ("scan", "scan_only_ocr"):
            state.ai_progress["status"] = "Phase 1: OCR..."
            await broadcast_log(f"🔍 Phase 1: Bắt đầu Local OCR cho {total} câu hỏi...")
            try:
                import easyocr
                ocr_reader = easyocr.Reader(["vi", "en"], gpu=False)
            except Exception as e:
                await broadcast_log(f"⚠️ EasyOCR Error: {e}. Sẽ bỏ qua OCR cục bộ.")
                ocr_reader = None

            ocr_count = 0
            for i, q in enumerate(questions):
                state.ai_progress["current"] = i
                if not q.get("questionText") and os.path.exists(q.get("img", "")):
                    if ocr_reader:
                        loop = asyncio.get_running_loop()
                        try:
                            results = await loop.run_in_executor(None, ocr_reader.readtext, q["img"], 0)
                            q["questionText"] = " ".join(results)
                            ocr_count += 1
                        except Exception:
                            pass
                if i % 50 == 0 and i > 0:
                    await storage.save_quiz_data(data, backup=False)
                    await broadcast_log(f"... Đã xử lý OCR {i}/{total} câu.")

            await broadcast_log(f"✅ OCR Cục bộ hoàn tất. Đã nhận diện thêm {ocr_count} câu.")

        if mode == "scan_only_ocr":
            return
            
        # Phase 3: AI text refinement -> Save to DRAFT
        state.ai_progress["status"] = "Phase 2: AI Refinement..."
        cfg = storage.load_config()
        
        # Intelligent provider selection: Use Groq if available (better for text)
        groq_key = cfg.get("GROQ_API_KEY", "").strip()
        gemini_key = cfg.get("GEMINI_API_KEY", "").strip()
        use_provider = "groq" if groq_key else "gemini"
        
        # Batch sizes: 8 is a safe number to stay under Groq's 6000 TPM limit
        batch_size = 8
        
        # We only refine items that don't have aiAns OR we are in reanalyze mode
        pending = [i for i, q in enumerate(questions) if q.get("questionText")]
        if mode != "reanalyze":
            pending = [i for i in pending if not questions[i].get("aiAns")]
            
        await broadcast_log(f"🤖 Phase 2: AI Refinement - Tổng {len(pending)} câu (Batch size: {batch_size})")
        await broadcast_log(f"⚙️ Provider ưu tiên: {use_provider.upper()}")
        
        ai_suggestions = []
        consecutive_failures = 0
        
        for i in range(0, len(pending), batch_size):
            state.ai_progress["current"] = total - len(pending) + i
            state.ai_progress["status"] = f"Phase 2: AI Refinement ({i}/{len(pending)})"
            chunk_indices = pending[i:i + batch_size]
            texts = [questions[idx].get("questionText", "") for idx in chunk_indices]
            
            prompt = "Fix OCR errors, remove UI noise. Return JSON only, no markdown. Format: [{\"questionText\": \"text\", \"aiAns\": \"A/B/C/D\"}]. Data: " + json.dumps(texts)
            results = await ai_svc.ask_text_only(prompt)
            
            if not results:
                consecutive_failures += 1
                await broadcast_log(f"❌ Batch {i//batch_size + 1} thất bại. Thất bại liên tiếp: {consecutive_failures}")
                if consecutive_failures >= 5:
                    await broadcast_log(f"🛑 DỪNG: Quá nhiều lỗi API. Vui lòng:")
                    await broadcast_log(f"  1. Nâng cấp API tier (Gemini, Groq) để tăng quota")
                    await broadcast_log(f"  2. Chờ quota reset (thường 1 giờ)")
                    await broadcast_log(f"  3. Sử dụng Review Center để sửa thủ công")
                    break
                # Exponential backoff on failures
                wait_seconds = 30 + (consecutive_failures * 15)
                await broadcast_log(f"⏳ Đợi {wait_seconds}s trước retry...")
                await asyncio.sleep(wait_seconds)
                continue
            
            consecutive_failures = 0  # Reset on success

            for j, res in enumerate(results):
                if j < len(chunk_indices):
                    idx = chunk_indices[j]
                    orig_q = questions[idx]
                    
                    # Create a suggestion item for the Review Center
                    suggestion = {
                        "id": orig_q.get("id"),
                        "img": orig_q.get("img"),
                        "old_text": orig_q.get("questionText"),
                        "questionText": res.get("questionText", ""),
                        "aiAns": res.get("aiAns", ""),
                        "type": "change"
                    }
                    ai_suggestions.append(suggestion)
            
            await broadcast_log(f"--- Đã tinh chỉnh {min(i + batch_size, len(pending))}/{len(pending)} câu ({use_provider.upper()}).")
            
            if i + batch_size < len(pending):
                # VERY conservative delay: Groq TPM + Gemini RPM
                if use_provider == "groq":
                    wait_seconds = 25  # ~240 tokens/sec at 6000 TPM, 1 Q ≈ 100 tokens → 25s safe
                else:
                    wait_seconds = 30  # Gemini: 15 RPM = 1 request/4s, be VERY safe with 30s
                await broadcast_log(f"⏳ Đợi {wait_seconds}s...")
                await asyncio.sleep(wait_seconds)


        if ai_suggestions:
            storage.save_draft(subject_name, "clean", ai_suggestions)
            await broadcast_log(f"📦 Đã lưu {len(ai_suggestions)} gợi ý vào Review Center. Vui lòng phê duyệt.")
        else:
            await broadcast_log(f"ℹ️ Không có gợi ý mới từ AI cho {subject_name}.")

        if mode == "reanalyze":
            return

        # Phase 4: Fuzzy duplicate check
        await broadcast_log(f"🧠 Phase 3: Đang tìm kiếm câu hỏi trùng lặp (Fuzzy Similarity > 85%)...")
        def _norm(text):
            if not text:
                return ""
            text = text.lower()
            for pat in [r"fuoverflow(\.com)?", r"choose 1 answer", r"there are \d+ questions",
                        r"question: \d+", r"q: \d+", r"back", r"next"]:
                text = re.sub(pat, "", text)
            return " ".join(re.sub(r"[^\w\s]", "", text).split())

        dupe_ids = {d[1] for d in exact_dupes}
        fuzzy_dupes = []
        for i in range(total):
            if questions[i].get("id") in dupe_ids:
                continue
            t1 = _norm(questions[i].get("questionText", ""))
            if len(t1) < 10:
                continue
            for j in range(i + 1, total):
                if questions[j].get("id") in dupe_ids:
                    continue
                t2 = _norm(questions[j].get("questionText", ""))
                if t2 and SequenceMatcher(None, t1, t2).ratio() > 0.85:
                    fuzzy_dupes.append((questions[i].get("id"), questions[j].get("id")))

        # --- CONSOLIDATE ALL RESULTS INTO ONE DRAFT ---
        all_results = []
        # 1. AI Text/Ans changes
        for sug in ai_suggestions:
            all_results.append({**sug, "type": "change"})
            
        # 2. Exact Dupes
        for d1, d2 in exact_dupes:
            all_results.append({"ids": [d1, d2], "type": "dupe", "subtype": "exact"})
            
        # 3. Fuzzy Dupes
        for d1, d2 in fuzzy_dupes:
            all_results.append({"ids": [d1, d2], "type": "dupe", "subtype": "fuzzy"})

        if all_results:
            storage.save_draft(subject_name, "clean", all_results)
            await broadcast_log(f"✨ AI Clean Hoàn tất: {len(ai_suggestions)} thay đổi, {len(exact_dupes) + len(fuzzy_dupes)} trùng lặp.")
            await broadcast_log(f"📦 Vui lòng mở Review Center để duyệt kết quả.")
        else:
            await broadcast_log(f"✅ AI Clean: Không tìm thấy vấn đề nào cần xử lý.")

        state.ai_progress["status"] = "Done"
    except Exception as e:
        await broadcast_log(f"❌ AI Clean Error: {e}")
        state.ai_progress["status"] = "Error"


async def _audit_worker(subject_name: str, urls: list):
    """Comment mining từ FuOverflow."""
    try:
        from collections import Counter
        from bs4 import BeautifulSoup
        from fuoverflow_scraper_v6 import create_driver, login, BASE_URL

        await broadcast_log(f"🚀 Audit: {subject_name} ({len(urls)} threads)")
        data = storage.load_quiz_data()
        actual_key = subject_name
        questions = data.get(actual_key, [])
        await broadcast_log(f"📋 [Audit] Subject: {subject_name} | Key: {actual_key} | Total items: {len(questions)}")

        loop = asyncio.get_running_loop()
        driver = await loop.run_in_executor(None, create_driver)
        await loop.run_in_executor(None, login, driver)

        all_votes = {}
        for url in urls:
            slug = url.rstrip("/").split("/")[-1]
            await broadcast_log(f"🔍 Thread: {slug}")
            await loop.run_in_executor(None, driver.get, url)
            await asyncio.sleep(3)
            soup = BeautifulSoup(driver.page_source, "lxml")
            links = soup.find_all("a", href=lambda h: h and "/attachments/" in h)

            for i, a in enumerate(links, 1):
                att_h = a.get("href", "")
                side_href = a.get("data-lb-sidebar-href", att_h)
                if not side_href.startswith("http"):
                    side_href = BASE_URL + side_href
                aid_match = re.search(r"\.(\d+)/?$", att_h) or re.search(r"/(\d+)/?$", att_h)
                target_q = None
                if aid_match:
                    aid = aid_match.group(1)
                    target_q = next((q for q in questions if str(q.get("att_id")) == aid), None)
                if not target_q:
                    safe_slug = re.sub(r"[^a-zA-Z0-9]", "_", slug)
                    img_part = f"{safe_slug}_q{str(i).zfill(3)}"
                    target_q = next((q for q in questions if img_part in q.get("img", "")), None)
                if not target_q:
                    continue

                await loop.run_in_executor(None, driver.get, side_href)
                await asyncio.sleep(1.5)
                s_soup = BeautifulSoup(driver.page_source, "lxml")
                txts = [el.get_text(strip=True).upper() for el in s_soup.select(".message-body .bbWrapper, .comment-body .bbWrapper")]
                votes = []
                for t in txts:
                    m = re.search(r"(?:ĐÁP\s*ÁN|ANSWER|ANS|CHỌN|->>|=>)\s*[:=]*\s*([A-D\s,]+)", t)
                    if m:
                        letters = re.findall(r"[A-D]", m.group(1))
                        f = "".join(sorted(set(letters)))
                        if f:
                            votes.append(f)
                    elif len(t) <= 12:
                        letters = re.findall(r"[A-D]", t)
                        f = "".join(sorted(set(letters)))
                        if f:
                            votes.append(f)

                if votes:
                    counts = Counter(votes)
                    common = counts.most_common(2)
                    q_id = str(target_q.get("id"))
                    all_votes[q_id] = {
                        "suggested": common[0][0],
                        "votes": dict(counts),
                        "has_conflict": len(common) > 1 and common[0][1] == common[1][1],
                        "url": side_href,
                        "old_ans": target_q.get("ans", "?"),
                        "img": target_q.get("img", ""),
                    }
                    await broadcast_log(f"🗳️ Vote Q-{q_id}: {dict(counts)} -> {common[0][0]}")

        await loop.run_in_executor(None, driver.quit)

        diff = [
            {"id": qid, "img": v["img"], "old_ans": v["old_ans"],
             "suggested": v["suggested"], "stats": v["votes"],
             "has_conflict": v["has_conflict"], "url": v["url"]}
            for qid, v in all_votes.items()
            if v["old_ans"] != v["suggested"] or v["has_conflict"]
        ]
        state.audit_results = diff
        storage.save_draft(subject_name, "audit", diff)
        await broadcast_log(f"✅ Audit xong! {len(diff)} câu cần xem xét.")
    except Exception as e:
        await broadcast_log(f"❌ Audit error: {e}")


async def _smart_refine_worker(subject_name: str, urls: list, skip_mining: bool = False):
    try:
        await broadcast_log(f"🪄 Smart Refine: {subject_name}")
        if not urls:
            cfg = storage.load_config()
            sources = cfg.get("SOURCES", [])
            urls = [s["url"] for s in sources if s.get("subject") == subject_name and s.get("enabled")]

        # Phase 1: Comment mining (reuse audit logic)
        if not skip_mining:
            await _audit_worker(subject_name, urls)
        else:
            await broadcast_log(f"⏭️ Skipping Phase 1: Dùng dữ liệu audit có sẵn cho {subject_name}")
        all_votes_raw = storage.load_draft(subject_name, "audit") or []
        vote_map = {item["id"]: item for item in all_votes_raw}

        # Phase 2: Filter suspicious
        data = storage.load_quiz_data()
        actual_key = subject_name
        questions = data.get(actual_key, [])
        suspicious = []
        for q in questions:
            if not isinstance(q, dict):
                continue
            q_id = str(q.get("id"))
            vote_info = vote_map.get(q_id)
            reason = ""
            is_sus = False
            if not vote_info:
                is_sus, reason = True, "Không có comment"
            elif q.get("ans") != vote_info.get("suggested"):
                is_sus, reason = True, f"Xung đột: Máy ({q.get('ans')}) vs CĐ ({vote_info['suggested']})"
            elif vote_info.get("has_conflict"):
                is_sus, reason = True, "Cộng đồng tranh cãi"
            elif not q.get("ans") or q.get("ans") == "?":
                is_sus, reason = True, "Thiếu đáp án"
            if is_sus:
                suspicious.append({
                    "id": q_id, "img": q.get("img", ""),
                    "questionText": q.get("questionText", ""),
                    "current_ans": q.get("ans", ""),
                    "comment_ans": vote_info["suggested"] if vote_info else "N/A",
                    "comment_votes": vote_info["stats"] if vote_info else {},
                    "reason": reason,
                })

        if not suspicious:
            await broadcast_log(f"✨ Không có câu nào nghi vấn trong {subject_name}")
            storage.save_draft(subject_name, "smart_audit", [])
            return

        # Phase 3: AI verification (text batch)
        await broadcast_log(f"🧠 AI thẩm định {len(suspicious)} câu nghi vấn...")
        final_results = []
        batch_size = 8
        for i in range(0, len(suspicious), batch_size):
            batch = suspicious[i:i + batch_size]
            batch_ctx = [{"id": b["id"], "question": b["questionText"][:500],
                          "current": b["current_ans"], "community_suggested": b["comment_ans"]} for b in batch]
            prompt = (f"Thẩm định đáp án trắc nghiệm. Chọn đáp án đúng nhất (A/B/C/D hoặc TF). "
                      f"Return JSON: {{\"results\": [{{\"id\": \"...\", \"final_ans\": \"...\", \"explanation\": \"...\"}}]}}. "
                      f"Data: {json.dumps(batch_ctx, ensure_ascii=False)}")
            try:
                ai_res = await ai_svc.ask_text_only(prompt)
                for item in batch:
                    match = next((r for r in ai_res if str(r.get("id")) == str(item["id"])), None)
                    item["ai_ans"] = match.get("final_ans", "N/A") if match else "N/A"
                    item["explanation"] = match.get("explanation", "") if match else ""
                    final_results.append(item)
            except Exception as e:
                await broadcast_log(f"❌ AI batch error: {e}")
                for item in batch:
                    item["ai_ans"] = "Error"
                    final_results.append(item)
            await asyncio.sleep(5)

        storage.save_draft(subject_name, "smart_audit", final_results)
        await broadcast_log(f"🎉 Smart Refine xong! {len(final_results)} câu đã thẩm định.")
    except Exception as e:
        await broadcast_log(f"❌ Smart Refine error: {e}")

@router.post("/api/ai-audit/{subject}")
async def start_ai_only_audit(subject: str, background_tasks: BackgroundTasks):
    """API bắt đầu AI-only Audit cho toàn bộ môn học"""
    background_tasks.add_task(_ai_batch_audit_worker, subject)
    return {"ok": True, "message": f"Đã bắt đầu AI Audit cho {subject}"}


@router.get("/api/ai-audit/{subject}/results")
async def get_ai_audit_results(subject: str):
    """Lấy kết quả AI Audit từ file nháp"""
    results = storage.load_draft(subject, "ai_audit")
    return results


async def _ai_batch_audit_worker(subject_name: str):
    """
    Worker xử lý Audit toàn bộ môn học bằng AI (Batch + Rate Limit)
    """
    try:
        await broadcast_log(f"🚀 Bắt đầu AI-only Audit: {subject_name}")
        
        # 1. Load dữ liệu
        data = storage.load_quiz_data()
        questions = data.get(subject_name, [])
        if not questions:
            await broadcast_log(f"❌ Không tìm thấy dữ liệu cho môn {subject_name}")
            return

        total = len(questions)
        await broadcast_log(f"📋 Tìm thấy {total} câu hỏi. Đang chia batch...")

        # 2. Chia batch (Tối ưu lên 30 câu / batch để nhanh hơn gấp 3 lần)
        batch_size = 30
        final_results = []
        
        import re
        ans_regex = re.compile(r"Q(\d+):\s*([ABCD])", re.IGNORECASE)

        for i in range(0, total, batch_size):
            batch = questions[i:i + batch_size]
            current_batch_idx = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size
            
            await broadcast_log(f"🧠 Đang xử lý Batch {current_batch_idx}/{total_batches} ({len(batch)} câu)...")

            # Tạo Prompt
            prompt_lines = [
                "Bạn là AI giải trắc nghiệm chuyên nghiệp.",
                "Hãy đọc các câu hỏi sau và trả lời đáp án ĐÚNG nhất (A, B, C, hoặc D).",
                "YÊU CẦU: Trả lời đúng format 'Q[ID]: [ANSWER]'. Ví dụ: Q1: A",
                "KHÔNG giải thích, KHÔNG thêm văn bản thừa.",
                "---"
            ]
            
            for q in batch:
                q_id = q.get("id", "0")
                q_text = q.get("questionText", "")
                # Thêm Options nếu có
                options_str = ""
                if "options" in q and isinstance(q["options"], list):
                    options_str = "\n".join(q["options"])
                
                prompt_lines.append(f"Q{q_id}: {q_text}\n{options_str}")

            prompt = "\n\n".join(prompt_lines)

            # 3. Gửi AI với Retry
            ai_raw = ""
            for retry in range(2):
                try:
                    # Sử dụng hàm ask_raw_text mới để lấy nội dung thô
                    ai_raw = await ai_svc.ask_raw_text(prompt)
                    if ai_raw:
                        break
                except Exception as e:
                    await broadcast_log(f"⚠️ Batch {current_batch_idx} fail (lần {retry+1}): {e}")
                    await asyncio.sleep(2)

            # 4. Parse kết quả bằng Regex
            ai_answers = {}
            if ai_raw:
                # regex: Q(\d+): ([ABCD])
                matches = ans_regex.findall(ai_raw)
                for qid_str, ans in matches:
                    ai_answers[str(qid_str)] = ans.upper()

            # 5. So sánh và lưu
            for q in batch:
                qid = str(q.get("id"))
                current_ans = str(q.get("ans", "")).strip().upper()
                ai_ans = ai_answers.get(qid, "N/A")
                
                status = "ok" if current_ans == ai_ans else "conflict"
                if ai_ans == "N/A": 
                    status = "error"
                
                final_results.append({
                    "id": qid,
                    "questionText": q.get("questionText", ""),
                    "current_ans": current_ans,
                    "ai_ans": ai_ans,
                    "status": status
                })

            # 6. Progress & Rate Limit (5s)
            progress = int((len(final_results) / total) * 100)
            await broadcast_log(f"📊 {subject_name}: {progress}% ({len(final_results)}/{total}) - Batch {current_batch_idx} OK")
            
            storage.save_draft(subject_name, "ai_audit", final_results)
            await asyncio.sleep(5)

        await broadcast_log(f"✅ Đã hoàn thành AI-only Audit cho {subject_name}! Tổng: {len(final_results)} câu.")
        
    except Exception as e:
        import traceback
        await broadcast_log(f"❌ Critical AI Audit Error: {str(e)}")
        print(traceback.format_exc())
