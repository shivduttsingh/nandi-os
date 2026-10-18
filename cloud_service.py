from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


PROCESSES: list[subprocess.Popen[bytes]] = []
STOP = False


def _stop(*_: object) -> None:
    global STOP
    STOP = True
    for process in PROCESSES:
        if process.poll() is None:
            process.terminate()


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    port = os.getenv("PORT", "8501")
    worker = subprocess.Popen([sys.executable, "-m", "nandi_v2.cloud_worker"])
    web = subprocess.Popen([
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        "--server.address=0.0.0.0",
        f"--server.port={port}",
        "--server.headless=true",
    ])
    PROCESSES.extend([worker, web])

    while not STOP:
        web_code = web.poll()
        worker_code = worker.poll()
        if web_code is not None:
            _stop()
            return int(web_code)
        if worker_code is not None:
            _stop()
            return int(worker_code)
        time.sleep(2)

    for process in PROCESSES:
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
