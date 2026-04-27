"""
Entry point của ứng dụng Lite.
Khởi tạo FastAPI, mount static, include router từ api.py.
"""
import os
import warnings

# Tắt cảnh báo không cần thiết từ Torch / EasyOCR
warnings.filterwarnings("ignore", category=UserWarning)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import APP_TITLE, HOST, PORT
from app.core.logger import logger
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    os.makedirs("data/backups", exist_ok=True)
    os.makedirs("drafts", exist_ok=True)
    logger.info(f"{APP_TITLE} started at http://{HOST}:{PORT}")
    yield
    # Shutdown (nothing needed)

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title=APP_TITLE, docs_url="/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Static files ─────────────────────────────────────────────────────────────
if os.path.exists("images"):
    app.mount("/images", StaticFiles(directory="images"), name="images")

# Serve tất cả file tĩnh khác qua /static (tương thích hệ thống cũ)
app.mount("/static", StaticFiles(directory="."), name="static")

# ─── Routes ───────────────────────────────────────────────────────────────────
app.include_router(router)

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    from app.utils.system import kill_port_owner

    kill_port_owner(PORT)

    print("\n" + "=" * 50)
    print(f"  DASHBOARD: http://localhost:{PORT}/admin.html")
    print(f"  QUIZ:      http://localhost:{PORT}/index.html")
    print(f"  API DOCS:  http://localhost:{PORT}/docs")
    print("=" * 50 + "\n")

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
