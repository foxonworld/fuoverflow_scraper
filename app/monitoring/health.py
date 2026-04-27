import shutil
import psutil
from fastapi import APIRouter
from app.core.state import state

router = APIRouter()

@router.get("/health")
async def health_check():
    # Disk usage
    total, used, free = shutil.disk_usage("/")
    disk_percent = (used / total) * 100
    
    # Memory usage
    mem = psutil.virtual_memory()
    
    return {
        "status": "healthy",
        "disk": {
            "total_gb": total // (2**30),
            "free_gb": free // (2**30),
            "used_percent": round(disk_percent, 2)
        },
        "memory": {
            "total_gb": round(mem.total / (2**30), 2),
            "available_gb": round(mem.available / (2**30), 2),
            "used_percent": mem.percent
        },
        "crawler_running": state.process is not None and state.process.poll() is None
    }
