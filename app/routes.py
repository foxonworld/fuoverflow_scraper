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
    for f in files:
        parts = f.replace(".json", "").rsplit("_", 1)
        if len(parts) == 2:
            result.append({"subject": parts[0], "type": parts[1], "file": f, "mtime": os.path.getmtime(os.path.join(D, f))})
    return {"drafts": result}


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


@router.post("/api/ai/clean/{name}/export_v2")
async def export_v2(name: str):
    if not state.ai_results:
        raise HTTPException(status_code=400, detail="Chạy AI scan trước")
    data = storage.load_quiz_data()
    if name not in data:
        raise HTTPException(status_code=404, detail="Not found")
    dupe_ids = {str(p[1]) for p in state.ai_results.get("exact", []) + state.ai_results.get("fuzzy", [])}
    clean = [q for q in data[name] if str(q.get("id")) not in dupe_ids]
    new_name = f"{name}_v2"
    data[new_name] = clean
    await storage.save_quiz_data(data)
    return {"message": f"Đã tạo {new_name}", "new_subject": new_name, "count": len(clean)}


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/audit/subjects/{name}/scan")
async def start_audit(name: str, req: AuditRequest, background_tasks: BackgroundTasks):
    if state.is_crawler_running:
        raise HTTPException(status_code=400, detail="Crawler đang chạy")
    state.audit_results = None
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
            id_map[q_id]["ans"] = new_ans
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
    background_tasks.add_task(_smart_refine_worker, name, req.urls)
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
            id_map[q_id]["ans"] = ans
            id_map[q_id]["is_smart_audited"] = True
            updated += 1
    await storage.save_quiz_data(data)
    storage.delete_draft(name, "smart_audit")
    return {"message": f"Applied {updated} changes"}


@router.get("/api/audit/subjects/{name}/threads")
async def get_threads(name: str):
    data = storage.load_quiz_data()
    base_name = name.split("_")[0].upper()
    actual_key = next((k for k in data if k.upper().startswith(base_name)), name)
    if actual_key not in data:
        return []
    cfg = storage.load_config()
    sources = cfg.get("SOURCES", [])
    cfg_urls = [s["url"] for s in sources if base_name in s.get("subject", "").upper() and s.get("enabled")]
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
    return list(threads.values())


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
    storage.create_named_backup(f"{subject_name}_pre_trash")
    data = storage.load_quiz_data()
    if subject_name not in data:
        return
    before = len(data[subject_name])
    clean = [q for q in data[subject_name]
             if isinstance(q, dict) and q.get("img") and os.path.exists(q["img"])
             and q.get("ans") and q["ans"] != "?"]
    removed = before - len(clean)
    if removed > 0:
        data[subject_name] = clean
        await storage.save_quiz_data(data)
        await broadcast_log(f"🗑️ Trash Filter: Đã xóa {removed} câu rác khỏi {subject_name}")
    else:
        await broadcast_log(f"✅ Trash Filter: {subject_name} đã sạch")


async def _ai_classify_worker(subject_name: str, source_id: str = None):
    try:
        data = storage.load_quiz_data()
        questions = data.get(subject_name, [])
        to_process = [i for i, q in enumerate(questions)
                      if isinstance(q, dict) and (not source_id or q.get("source_id") == source_id)]
        state.ai_progress = {"current": 0, "total": len(to_process), "status": "Classifying..."}
        batch_size = 5
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
            await asyncio.sleep(0.5)
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
            try:
                import easyocr
                ocr_reader = easyocr.Reader(["vi", "en"], gpu=False)
            except Exception:
                ocr_reader = None

            for i, q in enumerate(questions):
                state.ai_progress["current"] = i
                if not q.get("questionText") and os.path.exists(q.get("img", "")):
                    if ocr_reader:
                        loop = asyncio.get_running_loop()
                        try:
                            results = await loop.run_in_executor(None, ocr_reader.readtext, q["img"], 0)
                            q["questionText"] = " ".join(results)
                        except Exception:
                            pass
                if i % 20 == 0:
                    await storage.save_quiz_data(data, backup=False)

        if mode == "scan_only_ocr":
            await broadcast_log(f"✅ OCR xong cho {subject_name}")
            return

        # Phase 3: AI text refinement
        state.ai_progress["status"] = "Phase 2: AI Refinement..."
        cfg = storage.load_config()
        batch_size = 20 if cfg.get("AI_PROVIDER") == "gemini" else 5
        pending = [i for i, q in enumerate(questions) if not q.get("aiAns") and q.get("questionText")]
        for i in range(0, len(pending), batch_size):
            chunk = pending[i:i + batch_size]
            texts = [questions[idx].get("questionText", "") for idx in chunk]
            prompt = "Fix OCR errors, remove UI noise. Return JSON: [{\"questionText\": \"text\", \"aiAns\": \"ans\"}]. Data: " + json.dumps(texts)
            results = await ai_svc.extract_text_from_images([], prompt)
            for j, res in enumerate(results):
                if j < len(chunk):
                    questions[chunk[j]]["questionText"] = res.get("questionText", "")
                    questions[chunk[j]]["aiAns"] = res.get("aiAns", "")
            await storage.save_quiz_data(data, backup=False)
            await asyncio.sleep(1)

        if mode == "reanalyze":
            pass  # Skip fuzzy if reanalyzing from existing data

        # Phase 4: Fuzzy duplicate check
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

        state.ai_results = {"exact": exact_dupes, "fuzzy": fuzzy_dupes, "total_scanned": total}
        storage.save_draft(subject_name, "clean", state.ai_results)
        state.ai_progress["status"] = "Done"
        await storage.save_quiz_data(data)
        await broadcast_log(f"✨ AI Clean xong! {len(exact_dupes) + len(fuzzy_dupes)} dupes tìm thấy.")
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
        actual_key = subject_name if subject_name in data else next(
            (k for k in data if k.upper().startswith(subject_name.split("_")[0].upper())), subject_name)
        questions = data.get(actual_key, [])

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


async def _smart_refine_worker(subject_name: str, urls: list):
    try:
        await broadcast_log(f"🪄 Smart Refine: {subject_name}")
        if not urls:
            cfg = storage.load_config()
            sources = cfg.get("SOURCES", [])
            base = subject_name.split("_")[0].upper()
            urls = [s["url"] for s in sources if base in s.get("subject", "").upper() and s.get("enabled")]

        # Phase 1: Comment mining (reuse audit logic)
        await _audit_worker(subject_name, urls)
        all_votes_raw = storage.load_draft(subject_name, "audit") or []
        vote_map = {item["id"]: item for item in all_votes_raw}

        # Phase 2: Filter suspicious
        data = storage.load_quiz_data()
        base = subject_name.split("_")[0].upper()
        actual_key = subject_name if subject_name in data else next(
            (k for k in data if k.upper().startswith(base)), subject_name)
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
                    "comment_votes": vote_info["votes"] if vote_info else {},
                    "reason": reason,
                })

        if not suspicious:
            await broadcast_log(f"✨ Không có câu nào nghi vấn trong {subject_name}")
            storage.save_draft(subject_name, "smart_audit", [])
            return

        # Phase 3: AI verification (text batch)
        await broadcast_log(f"🧠 AI thẩm định {len(suspicious)} câu nghi vấn...")
        final_results = []
        batch_size = 30
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
