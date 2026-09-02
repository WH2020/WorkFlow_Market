"""
Agent4Market 阶段 A4: 建议持久化存储

行动建议在被用户接受之前属于"待审批工作状态"，不是已确认的业务记录，
因此不写入 sales_store 的 signals / action_suggestions 表（那些表由用户在
工作台批准后才落库）。这里使用独立的 SQLite 文件保存 A4 的工作状态，
进程重启后可以恢复，且不触碰主 schema 的迁移清单哈希门禁。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS a4_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS a4_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  account_name TEXT NOT NULL,
  signal_id TEXT,
  signal_type TEXT,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low')),
  suggested_actions_json TEXT NOT NULL CHECK (json_valid(suggested_actions_json)),
  context_json TEXT NOT NULL CHECK (json_valid(context_json)),
  status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'edited', 'ignored')),
  user_feedback TEXT,
  created_at TEXT NOT NULL,
  accepted_at TEXT,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS a4_recommendations_account_idx
  ON a4_recommendations(account_id, status, recommendation_id);

CREATE TABLE IF NOT EXISTS a4_accepted_actions (
  action_id TEXT PRIMARY KEY,
  recommendation_id TEXT NOT NULL REFERENCES a4_recommendations(recommendation_id) ON DELETE RESTRICT,
  account_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low')),
  due_at TEXT,
  assignee TEXT,
  created_from_signal INTEGER NOT NULL CHECK (created_from_signal IN (0, 1)),
  accepted_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS a4_accepted_actions_rec_idx
  ON a4_accepted_actions(recommendation_id, action_id);
"""

RECOMMENDATION_COLUMNS = (
    "recommendation_id",
    "account_id",
    "account_name",
    "signal_id",
    "signal_type",
    "title",
    "description",
    "priority",
    "suggested_actions_json",
    "context_json",
    "status",
    "user_feedback",
    "created_at",
    "accepted_at",
    "updated_at",
)


class A4StoreError(RuntimeError):
    """A4 存储错误，携带稳定的错误码供 UI 展示"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_store_path(project_root: Path | str | None = None) -> Path:
    """默认存储位置：项目本机运行时目录，数据不离开本机"""
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[1]
    return root / ".pi" / "director-runtime" / "a4-recommendations.db"


def _configure(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")


def _initialize_schema(connection: sqlite3.Connection) -> None:
    """初始化 schema 并记录版本"""
    connection.executescript(SCHEMA_SQL)
    now = _now()
    connection.execute(
        "INSERT OR REPLACE INTO a4_metadata(key, value, updated_at) VALUES (?, ?, ?)",
        ("schema_version", str(SCHEMA_VERSION), now),
    )
    connection.commit()


class A4Store:
    """A4 建议持久化存储 - 线程安全的写穿缓存"""

    def __init__(self, database_path: Path | str | None = None):
        if database_path is None:
            database_path = default_store_path()
        self.database_path = Path(database_path)
        self._lock = threading.RLock()
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """确保数据库存在并 schema 已初始化"""
        first_run = not self.database_path.exists()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            connection = sqlite3.connect(str(self.database_path))
            _configure(connection)
            try:
                if first_run:
                    _initialize_schema(connection)
                else:
                    # 验证 schema 版本
                    cursor = connection.execute(
                        "SELECT value FROM a4_metadata WHERE key = 'schema_version'"
                    )
                    row = cursor.fetchone()
                    if row is None or int(row[0]) != SCHEMA_VERSION:
                        raise A4StoreError(
                            "SCHEMA_VERSION_MISMATCH",
                            f"期望 schema 版本 {SCHEMA_VERSION}，数据库版本不匹配"
                        )
            finally:
                connection.close()

    def save_recommendation(self, recommendation: dict[str, Any]) -> None:
        """保存或更新建议"""
        now = _now()
        values = (
            recommendation["recommendation_id"],
            recommendation["account_id"],
            recommendation["account_name"],
            recommendation.get("signal_id"),
            recommendation.get("signal_type"),
            recommendation["title"],
            recommendation["description"],
            recommendation["priority"],
            json.dumps(recommendation["suggested_actions"], ensure_ascii=False),
            json.dumps(recommendation["context"], ensure_ascii=False),
            recommendation["status"],
            recommendation.get("user_feedback"),
            recommendation["created_at"],
            recommendation.get("accepted_at"),
            now,
        )

        with self._lock:
            connection = sqlite3.connect(str(self.database_path))
            _configure(connection)
            try:
                connection.execute(
                    f"""
                    INSERT INTO a4_recommendations({','.join(RECOMMENDATION_COLUMNS)})
                    VALUES ({','.join('?' for _ in RECOMMENDATION_COLUMNS)})
                    ON CONFLICT(recommendation_id) DO UPDATE SET
                      status = excluded.status,
                      user_feedback = excluded.user_feedback,
                      accepted_at = excluded.accepted_at,
                      updated_at = excluded.updated_at
                    """,
                    values,
                )
                connection.commit()
            finally:
                connection.close()

    def load_all_recommendations(self) -> list[dict[str, Any]]:
        """加载所有建议"""
        with self._lock:
            connection = sqlite3.connect(str(self.database_path))
            _configure(connection)
            try:
                connection.row_factory = sqlite3.Row
                cursor = connection.execute(
                    f"SELECT {','.join(RECOMMENDATION_COLUMNS)} FROM a4_recommendations"
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    rec = dict(row)
                    rec["suggested_actions"] = json.loads(rec.pop("suggested_actions_json"))
                    rec["context"] = json.loads(rec.pop("context_json"))
                    results.append(rec)
                return results
            finally:
                connection.close()

    def save_accepted_action(self, action: dict[str, Any]) -> None:
        """保存已接受的行动"""
        with self._lock:
            connection = sqlite3.connect(str(self.database_path))
            _configure(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO a4_accepted_actions(
                      action_id, recommendation_id, account_id, title, description,
                      priority, due_at, assignee, created_from_signal, accepted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action["action_id"],
                        action["recommendation_id"],
                        action["account_id"],
                        action["title"],
                        action["description"],
                        action["priority"],
                        action.get("due_at"),
                        action.get("assignee"),
                        1 if action["created_from_signal"] else 0,
                        action["accepted_at"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

    def get_adoption_stats(self) -> dict[str, Any]:
        """获取采纳率统计"""
        with self._lock:
            connection = sqlite3.connect(str(self.database_path))
            _configure(connection)
            try:
                cursor = connection.execute(
                    """
                    SELECT
                      COUNT(*) as total,
                      COALESCE(SUM(status = 'accepted'), 0) as accepted,
                      COALESCE(SUM(status = 'edited'), 0) as edited,
                      COALESCE(SUM(status = 'ignored'), 0) as ignored,
                      COALESCE(SUM(status = 'pending'), 0) as pending
                    FROM a4_recommendations
                    """
                )
                row = cursor.fetchone()
                total, accepted, edited, ignored, pending = row
                processed = total - pending
                adoption_rate = (accepted + edited) / processed if processed > 0 else 0.0
                return {
                    "total": total,
                    "accepted": accepted,
                    "edited": edited,
                    "ignored": ignored,
                    "pending": pending,
                    "adoption_rate": adoption_rate,
                }
            finally:
                connection.close()


def create_store(database_path: Path | str | None = None) -> A4Store:
    """创建 A4 存储实例"""
    return A4Store(database_path)
