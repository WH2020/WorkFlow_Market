from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_platform.business_backend import (
    read_account_360,
    read_account_timeline,
    read_today_focus,
    search_accounts,
)
from agent_platform.sales_store import MANIFEST_PATH, MIGRATIONS


ACCOUNT_COUNT = 10_000
ACTIVITY_COUNT = 100_000
SAMPLES = 20


def _measure(operation: Callable[[], object]) -> dict[str, float]:
    durations: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        operation()
        durations.append((time.perf_counter() - started) * 1000)
    ordered = sorted(durations)
    return {
        "minimum_ms": round(ordered[0], 2),
        "median_ms": round(ordered[len(ordered) // 2], 2),
        "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 2),
        "maximum_ms": round(ordered[-1], 2),
    }


def _create_fixture(root: Path) -> None:
    database = root / "data" / "agent4market.db"
    database.parent.mkdir(parents=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    migration = manifest["migrations"][0]
    script = (MIGRATIONS / migration["file"]).read_text(encoding="utf-8")
    connection = sqlite3.connect(database)
    connection.executescript(script)
    connection.execute(
        "INSERT INTO schema_migrations(version,name,script_sha256,applied_at,application_version,result) VALUES (1,?,?,?,?, 'applied')",
        (migration["name"], migration["sha256"], "2026-08-21T00:00:00Z", manifest["application_version"]),
    )
    connection.execute(
        "INSERT INTO store_metadata(key,value,updated_at) VALUES ('schema_version','1','2026-08-21T00:00:00Z')"
    )
    accounts = [
        (
            f"account-{index:05d}",
            f"性能样本客户 {index:05d}",
            f"性能样本客户 {index:05d}",
            "华东" if index % 2 else "华北",
            "脑机" if index % 3 else "具身",
            f"销售-{index % 20:02d}",
            "proposal" if index % 2 else "discovery",
            "good" if index % 5 else "attention",
            1,
            "2026-01-01T00:00:00Z",
            f"2026-08-{(index % 20) + 1:02d}T10:00:00Z",
        )
        for index in range(ACCOUNT_COUNT)
    ]
    connection.executemany(
        """INSERT INTO accounts(
               account_id,name,normalized_name,region,sector,owner,lifecycle_stage,health,
               version,created_at,updated_at
             ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        accounts,
    )
    activities = [
        (
            f"activity-{index:06d}",
            f"account-{index % ACCOUNT_COUNT:05d}",
            f"2026-08-{(index % 20) + 1:02d}T09:{index % 60:02d}:00Z",
            "电话",
            "跟进",
            f"性能样本互动 {index:06d}",
            "verified",
            1,
            "2026-08-21T00:00:00Z",
            "2026-08-21T00:00:00Z",
        )
        for index in range(ACTIVITY_COUNT)
    ]
    connection.executemany(
        """INSERT INTO activities(
               activity_id,account_id,occurred_at,channel,activity_type,summary,evidence_status,
               version,created_at,updated_at
             ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        activities,
    )
    connection.executemany(
        "INSERT INTO actions(action_id,account_id,action_text,due_at,status,origin,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                f"action-{index:05d}", f"account-{index:05d}", "跟进客户", "2026-08-20",
                "open", "manual", 1, "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z",
            )
            for index in range(2_000)
        ],
    )
    connection.executemany(
        "INSERT INTO risks(risk_id,account_id,risk_text,status,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        [
            (
                f"risk-{index:05d}", f"account-{index:05d}", "性能样本风险", "open", 1,
                "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z",
            )
            for index in range(1_000)
        ],
    )
    connection.commit()
    connection.close()
    database_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()
    (root / "data" / "storage-backend.json").write_text(
        json.dumps(
            {
                "backend": "sqlite",
                "schema_version": 1,
                "database_relative_path": "data/agent4market.db",
                "migration_batch_id": "customer-operations-benchmark",
                "database_sha256_at_cutover": database_sha256,
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="agent4market-a3-benchmark-") as temporary:
        root = Path(temporary).resolve()
        _create_fixture(root)
        operations: dict[str, Callable[[], object]] = {
            "customer_list": lambda: search_accounts(root, query="性能样本", limit=20),
            "customer_360": lambda: read_account_360(root, "account-00001"),
            "customer_timeline": lambda: read_account_timeline(root, "account-00001", limit=20),
            "today_focus": lambda: read_today_focus(
                root, limit=20, now=datetime(2026, 8, 21, tzinfo=timezone.utc)
            ),
        }
        for operation in operations.values():
            operation()
        results = {name: _measure(operation) for name, operation in operations.items()}
        print(
            json.dumps(
                {
                    "fixture": {"accounts": ACCOUNT_COUNT, "activities": ACTIVITY_COUNT},
                    "samples_per_operation": SAMPLES,
                    "target_p95_ms": 2_000,
                    "results": results,
                    "passed": all(result["p95_ms"] <= 2_000 for result in results.values()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
