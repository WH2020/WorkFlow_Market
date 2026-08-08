from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def http_json(base_url: str, path: str, token: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = f"{base_url.rstrip('/')}{path}{'?' + query if query else ''}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def local_media_url(url: str, base_url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    base = urllib.parse.urlparse(base_url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.netloc == base.netloc


def download_local_media(url: str, target: Path, base_url: str) -> Path:
    if not local_media_url(url, base_url):
        raise ValueError("external_media_url_blocked")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    request = urllib.request.Request(url, headers={"User-Agent": "market-director-copilot/0.1"})
    with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
        output.write(response.read())
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value))
    return value[:120] or "unknown"


def normalize_message(message: dict[str, Any], salesperson_id: str, session_id: str) -> dict[str, Any]:
    media_type = message.get("mediaType") or ""
    media = None
    if media_type or message.get("mediaUrl") or message.get("mediaLocalPath"):
        media = {
            "type": media_type or "unknown",
            "url": message.get("mediaUrl") or "",
            "local_path": message.get("mediaLocalPath") or "",
            "status": "pending",
        }
    return {
        "session_id": session_id,
        "salesperson_id": salesperson_id,
        "message_id": str(message.get("serverId") or message.get("localId") or ""),
        "timestamp": int(message.get("createTime") or 0),
        "sender": message.get("senderUsername") or "",
        "sent_by_me": bool(message.get("isSend")),
        "text": message.get("parsedContent") or message.get("content") or "",
        "quote": message.get("quote"),
        "media": media,
        "raw": message,
    }


def sync_session(base_url: str, token: str, spec: dict[str, Any], project: Path, since: int, limit: int, download_media: bool) -> list[dict[str, Any]]:
    session_id = spec["weflow_session_id"]
    salesperson_id = spec["salesperson_id"]
    path_id = urllib.parse.quote(session_id, safe="")
    result = http_json(base_url, f"/api/v1/sessions/{path_id}/messages", token, {"since": since, "limit": limit})
    messages = result.get("messages", []) if isinstance(result, dict) else []
    normalized = [normalize_message(item, salesperson_id, session_id) for item in messages]
    media_root = project / "data" / "weflow" / "media" / safe_name(session_id)
    for item in normalized:
        media = item.get("media")
        if not media or not download_media:
            continue
        url = media.get("url")
        if not url:
            media["status"] = "no_url"
            continue
        if not local_media_url(url, base_url):
            media["status"] = "external_blocked"
            continue
        suffix = Path(urllib.parse.urlparse(url).path).suffix or ".bin"
        target = media_root / f"{safe_name(item['message_id'])}{suffix}"
        try:
            download_local_media(url, target, base_url)
            media["local_path"] = str(target)
            media["sha256"] = sha256(target)
            media["status"] = "downloaded"
        except Exception as exc:  # noqa: BLE001
            media["status"] = "download_failed"
            media["error"] = str(exc)
    return normalized


def merge_messages(path: Path, messages: list[dict[str, Any]]) -> int:
    existing = read_json(path, [])
    by_id = {item.get("message_id"): item for item in existing if item.get("message_id")}
    for item in messages:
        if item.get("message_id"):
            by_id[item["message_id"]] = item
    merged = sorted(by_id.values(), key=lambda item: (item.get("timestamp", 0), item.get("message_id", "")))
    write_json(path, merged)
    return len(merged)


def update_state(path: Path, session_id: str, messages: list[dict[str, Any]]) -> None:
    state = read_json(path, {"sessions": {}})
    latest = max([item.get("timestamp", 0) for item in messages] or [0])
    current = state.setdefault("sessions", {}).setdefault(session_id, {})
    current["last_timestamp"] = max(int(current.get("last_timestamp", 0)), latest)
    current["synced_at"] = int(time.time())
    write_json(path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Incrementally sync approved WeFlow sessions into the Project.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--since", type=int, default=0)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--no-media", action="store_true")
    args = parser.parse_args()
    project = args.project.resolve()
    config = read_json(project / "config" / "market-director.json", {})
    token_env = config.get("weflow", {}).get("token_env", "WEFLOW_ACCESS_TOKEN")
    token = os.environ.get(token_env, "")
    if not token:
        print(f"Missing {token_env}; set the WeFlow access token in the process environment.", file=sys.stderr)
        return 2
    base_url = config.get("weflow", {}).get("base_url", "http://127.0.0.1:5031")
    state_path = project / "data" / "weflow" / "state.json"
    output_path = project / "data" / "weflow" / "normalized" / "messages.json"
    specs = read_json(project / "data" / "sales" / "salespeople.json", {"salespeople": {}}).get("salespeople", [])
    specs = [item for item in specs if item.get("active") and item.get("weflow_session_id")]
    all_messages: list[dict[str, Any]] = []
    for spec in specs:
        session_id = spec["weflow_session_id"]
        state = read_json(state_path, {"sessions": {}})
        overlap = int(config.get("weflow", {}).get("overlap_seconds", 120))
        watermark = args.since or max(0, int(state.get("sessions", {}).get(session_id, {}).get("last_timestamp", 0)) - overlap)
        try:
            messages = sync_session(base_url, token, spec, project, watermark, args.limit, not args.no_media)
            all_messages.extend(messages)
            update_state(state_path, session_id, messages)
            print(f"{session_id}: {len(messages)} messages")
        except Exception as exc:  # noqa: BLE001
            print(f"{session_id}: sync failed: {exc}", file=sys.stderr)
    count = merge_messages(output_path, all_messages)
    print(f"Stored {count} normalized messages in {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

