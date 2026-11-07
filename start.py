"""Bootstrap a user-local environment, then serve only on loopback."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("请安装 Python 3.12 或以上版本，再运行 python3.12 start.py。")
    port = int(os.environ.get("OPEN_BAZI_PORT", "8765"))
    if "--serve" in sys.argv:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=port, access_log=False)
        return
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"端口 {port} 已被占用。如果已启动，请打开 http://127.0.0.1:{port} 。")
    location = Path(os.environ.get("OPEN_BAZI_VENV", str(Path.home() / ".open-bazi" / "venv")))
    python = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = (ROOT / "requirements.txt").read_bytes()
    stamp = location / "open-bazi-requirements.txt"
    if not python.exists():
        print("首次启动：正在创建独立运行环境。", flush=True)
        venv.create(location, with_pip=True)
    if not stamp.exists() or stamp.read_bytes() != requirements:
        print("正在安装开源依赖，需要联网。之后排盘可离线使用。", flush=True)
        subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        stamp.write_bytes(requirements)
    child = subprocess.Popen([str(python), str(ROOT / "start.py"), "--serve"], cwd=ROOT)
    def open_when_ready():
        for _ in range(60):
            if child.poll() is not None:
                return
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    webbrowser.open(f"http://127.0.0.1:{port}")
                    return
            except OSError:
                time.sleep(.3)
    threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"本地地址：http://127.0.0.1:{port} 。按 Ctrl+C 停止。", flush=True)
    try:
        child.wait()
    except KeyboardInterrupt:
        child.terminate()
        child.wait()


if __name__ == "__main__":
    main()
