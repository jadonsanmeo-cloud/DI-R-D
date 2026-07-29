"""Start the local Next.js frontend once, or reuse the server on port 3000."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
FRONTEND_URL = "http://127.0.0.1:3000"


def _next_server_ready() -> bool:
    try:
        request = Request(FRONTEND_URL, headers={"User-Agent": "dev-preflight"})
        with urlopen(request, timeout=2) as response:
            content = response.read(256_000)
            powered_by = str(response.headers.get("X-Powered-By", "")).lower()
            return (
                response.status == 200
                and (
                    powered_by == "next.js"
                    or b"__NEXT_DATA__" in content
                )
            )
    except (OSError, URLError):
        return False


def main() -> int:
    print("[web-dev] checking http://127.0.0.1:3000", flush=True)
    if _next_server_ready():
        print("[web-dev] ready: reusing the existing Next.js server", flush=True)
        return 0

    env = os.environ.copy()
    env["API_BASE_URL"] = "http://127.0.0.1:8036"
    print("[web-dev] starting Next.js on port 3000", flush=True)
    process = subprocess.Popen(
        ["npm.cmd", "run", "dev", "--", "-p", "3000"],
        cwd=WEB_ROOT,
        env=env,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            print(
                f"[web-dev] failed: npm exited with status {exit_code}",
                file=sys.stderr,
                flush=True,
            )
            return exit_code or 1
        if _next_server_ready():
            print("[web-dev] ready: http://127.0.0.1:3000", flush=True)
            return process.wait()
        time.sleep(0.5)

    process.terminate()
    print(
        "[web-dev] failed: Next.js did not become ready within 90 seconds",
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
