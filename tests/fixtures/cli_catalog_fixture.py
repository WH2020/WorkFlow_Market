"""Synthetic stdio-only CLI; never opens a credential or network connection."""
import json
import os
from pathlib import Path
import sys
import time

mode = sys.argv[1]
assert Path.cwd().name.startswith("agent4market-model-catalog-")
assert all(os.environ[name] == str(Path.cwd()) for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"))
assert Path(os.environ["CODEX_HOME"]).is_dir()
assert not any(name in os.environ for name in ("OPENAI_API_KEY", "NODE_OPTIONS", "AGENT4MARKET_KEY", "CLAUDE_CONFIG_DIR"))
assert "--strict-config" in sys.argv and "plugins" in sys.argv
assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:1"
initialized = False
notified = False


def row(identifier):
    return {"id": "opaque-" + identifier, "model": identifier, "displayName": "Synthetic " + identifier,
            "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": v} for v in ("low", "medium", "high", "ultra")],
            "isDefault": identifier == "first", "hidden": False}


for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialize":
        assert not initialized and request["params"]["clientInfo"]["name"] == "Agent4Market"
        initialized = True
        result = {"userAgent": "synthetic"}
    elif method == "initialized":
        assert initialized and "id" not in request
        notified = True
        continue
    else:
        assert method == "model/list" and initialized and notified
        assert request["params"]["includeHidden"] is False
        if mode == "timeout":
            time.sleep(30)
        if mode == "overflow":
            print("x" * (3 * 1024 * 1024), flush=True)
            break
        if mode == "bad-json":
            print("not-json", flush=True)
            break
        if mode == "error":
            print(json.dumps({"id": request["id"], "error": {"message": "SYNTHETIC_PRIVATE_CANARY"}}), flush=True)
            break
        cursor = request["params"].get("cursor")
        result = {"data": [row("first" if not cursor else "second")], "nextCursor": "next" if not cursor or mode == "repeat" else None}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
