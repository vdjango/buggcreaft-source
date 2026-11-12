"""One local entry point for isolated platform, mirror and dependency processes."""

import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path


def run_platform(*, workers: int = 1) -> int:
    if not 0 <= workers <= 16:
        raise ValueError("workers_must_be_between_0_and_16")
    environment = dict(os.environ)
    hub = int(environment.get("TRANSLATION_PORT", "18448"))
    mirror = int(environment.get("TRANSLATION_MIRROR_PORT", "18450"))
    api = int(environment.get("BUGG_API_PORT", "18449"))
    if len({hub, mirror, api}) != 3 or any(not 1 <= p <= 65535 for p in (hub, mirror, api)):
        raise ValueError("platform_ports_must_be_distinct")
    environment.setdefault("BUGG_PLATFORM_AUTH_URL", f"http://127.0.0.1:{hub}")
    environment.setdefault("BUGG_PLATFORM_AUTH_TOKEN", secrets.token_urlsafe(32))
    environment.setdefault("BUGG_SEARCH_API_URL", f"http://127.0.0.1:{api}")
    environment.setdefault("BUGG_CACHE_INGEST_TOKEN", secrets.token_urlsafe(32))
    environment.setdefault("TRANSLATION_FACTS_URL", f"http://127.0.0.1:{api}")
    environment.setdefault("TRANSLATION_FACTS_TOKEN", environment["BUGG_CACHE_INGEST_TOKEN"])
    root = Path(__file__).resolve().parents[2]
    commands = [
        [sys.executable, "-m", "mc_translate"],
        *(
            []
            if int(environment.get("TRANSLATION_MIRROR_CONTROL_PORT", "0"))
            else [[sys.executable, "-m", "mc_translate.mirror"]]
        ),
        [sys.executable, "-m", "bugg_source", "serve", "--port", str(api)],
        *[[sys.executable, "-m", "bugg_source", "worker"] for _ in range(workers)],
    ]
    if environment.get("BUGG_CLOUD_PROFILE_FILE"):
        commands.append([sys.executable, "-m", "bugg_source", "archive-worker"])
    if (
        environment.get("BUGG_SEARCH_ENABLED") == "1"
        and environment.get("TRANSLATION_FEDERATION_ROLE", "hub") != "mirror"
    ):
        environment.setdefault("BUGG_SEARCH_SOURCE_URL", f"http://127.0.0.1:{hub}")
        commands.append([sys.executable, "-m", "bugg_source", "search-worker"])
    processes: list[subprocess.Popen] = []
    stopping = False

    def stop(signum, frame) -> None:
        nonlocal stopping
        stopping = True

    handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=root, env=environment))
        while not stopping:
            if any(process.poll() is not None for process in processes):
                return 1
            time.sleep(0.2)
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                if os.name != "nt":
                    process.send_signal(signal.SIGTERM)
                else:
                    process.terminate()
        deadline = time.monotonic() + 10
        for process in processes:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
