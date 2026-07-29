"""Run the local API on port 8036, or reuse a healthy Docker API."""

from __future__ import annotations

import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import uvicorn

API_URL = "http://127.0.0.1:8036"


def _data_intelligence_api_ready() -> bool:
    try:
        request = Request(f"{API_URL}/health", headers={"User-Agent": "dev-preflight"})
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if response.status != 200 or payload.get("status") != "ok":
            return False

        with urlopen(
            Request(
                f"{API_URL}/api/v1/runtime-capabilities",
                headers={"User-Agent": "dev-preflight"},
            ),
            timeout=2,
        ) as response:
            capabilities = json.loads(response.read().decode("utf-8"))
        return (
            response.status == 200
            and isinstance(capabilities.get("method_hub"), dict)
        )
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


def main() -> int:
    if _data_intelligence_api_ready():
        print(
            "[api-dev] ready: reusing the healthy API on "
            "http://127.0.0.1:8036",
            flush=True,
        )
        print(
            "[api-dev] stop the Docker API first when Python breakpoints are needed",
            flush=True,
        )
        try:
            while _data_intelligence_api_ready():
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
        print("[api-dev] existing API stopped; starting the host API", flush=True)

    print("[api-dev] starting host API on http://127.0.0.1:8036", flush=True)
    uvicorn.run(
        "data_intelligence_api.main:app",
        host="127.0.0.1",
        port=8036,
        reload=True,
        reload_dirs=[
            "packages/api/src",
            "packages/sdk/src",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
