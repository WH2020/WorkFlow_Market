"""Synthetic-only benchmark for retained-chat refresh; never uses user stores."""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_platform import wechat_privacy, wechat_storage, wechat_store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=10000)
    parser.add_argument("--conversations", type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.conversations <= args.messages <= 200000:
        parser.error("Require 1 <= conversations <= messages <= 200000")
    with tempfile.TemporaryDirectory(prefix="a4m-wechat-benchmark-", dir=Path.home()) as directory:
        root = Path(directory)
        wechat_privacy.ensure_private_directory(root)
        source = root / "synthetic.jsonl"
        wechat_privacy.create_private_file(source)
        with source.open("w", encoding="utf-8") as output:
            for index in range(args.messages):
                output.write(json.dumps({"conversation": f"synthetic-{index % args.conversations}",
                    "message_id": f"synthetic-{index}", "sender": "synthetic-sender",
                    "time": 1788919200 + index, "content": "Synthetic benchmark text"}) + "\n")
        with patch.object(wechat_storage, "_private_base", return_value=root / "private-registry"):
            started = time.perf_counter()
            imported = wechat_store.import_export(root, source, source_name=source.name,
                                                 ownership_confirmed=True, account_id="synthetic-self")
            import_seconds = time.perf_counter() - started
            before = wechat_store.dashboard(root)
            reference = wechat_store._now() + timedelta(seconds=1)
            samples = []
            for _ in range(3):
                started = time.perf_counter()
                cleanup = wechat_store.cleanup_expired(root, reference=reference)
                after = wechat_store.dashboard(root)
                samples.append(time.perf_counter() - started)
            assert cleanup["purged_messages"] == 0
            assert before["message_count"] == after["message_count"] == args.messages
            print(json.dumps({"messages": imported["message_count"], "conversations": args.conversations,
                "import_seconds": round(import_seconds, 4), "refresh_seconds": [round(x, 4) for x in samples],
                "refresh_median_seconds": round(statistics.median(samples), 4),
                "revision_unchanged": before["revision"] == after["revision"]}))


if __name__ == "__main__":
    main()
