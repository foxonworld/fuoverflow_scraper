"""
System utilities: Kill process đang chiếm port, kiểm tra tài nguyên.
"""
import os
import psutil


def kill_port_owner(port: int):
    """Kill process đang lắng nghe trên port (dùng khi khởi động server)."""
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                pid = conn.pid
                if pid and pid != os.getpid():
                    p = psutil.Process(pid)
                    print(f"⚠ Port {port} bị chiếm bởi PID {pid} ({p.name()}). Đang kill...")
                    p.terminate()
                    p.wait(timeout=3)
    except Exception as e:
        print(f"Lỗi kill_port_owner: {e}")
