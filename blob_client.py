import json
import os
from typing import Optional

import httpx

BLOB_API = "https://blob.vercel-storage.com"
TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN", "")


def blob_enabled() -> bool:
    return bool(TOKEN)


def _headers() -> dict:
    if not TOKEN:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not set")
    return {"Authorization": f"Bearer {TOKEN}"}


def blob_put_bytes(pathname: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    response = httpx.put(
        f"{BLOB_API}/{pathname}",
        headers={**_headers(), "Content-Type": content_type, "x-content-type": content_type},
        content=content,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def blob_get_bytes(url: str) -> bytes:
    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def blob_delete(pathname: str) -> None:
    response = httpx.delete(f"{BLOB_API}/{pathname}", headers=_headers(), timeout=30)
    if response.status_code not in (200, 404):
        response.raise_for_status()


def blob_list(prefix: str) -> list[dict]:
    response = httpx.get(
        BLOB_API,
        headers=_headers(),
        params={"prefix": prefix},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("blobs", [])


def blob_get_json(pathname: str) -> Optional[dict]:
    try:
        blobs = blob_list(pathname)
        exact = next((b for b in blobs if b.get("pathname") == pathname), None)
        if not exact:
            return None
        raw = blob_get_bytes(exact["url"])
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def blob_put_json(pathname: str, data: dict) -> dict:
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    return blob_put_bytes(pathname, content, "application/json")
