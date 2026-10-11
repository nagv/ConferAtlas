"""Start the backend API and the Streamlit frontend from one terminal. Ctrl+C stops both."""

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    for port in (8000, 8501):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                print(f"Port {port} is already in use. Stop its current server before running this launcher.")
                return 1
    processes = []
    try:
        processes.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=ROOT))
        processes.append(subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1",
             "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"],
            cwd=ROOT / "frontend"))
        print("\nStarting ConferAtlas")
        print("Open http://localhost:8501/ once the startup messages appear.")
        print("API documentation: http://localhost:8000/docs")
        print("Keep this terminal open. Press Ctrl+C to stop both services.\n", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        print("A service stopped. Check its startup messages above.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping ConferAtlas.")
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
