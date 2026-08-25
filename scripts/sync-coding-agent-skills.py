from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "integrations" / "coding-agents" / "agent4market-sales-director"
TARGETS = (
    ROOT / ".agents" / "skills" / "agent4market-sales-director",
    ROOT / ".claude" / "skills" / "agent4market-sales-director",
)


def files(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"技能目录不存在或不安全：{root}")
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"技能目录不允许符号链接：{path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def sync(check_only: bool) -> list[str]:
    source = files(CANONICAL)
    if "SKILL.md" not in source:
        raise RuntimeError("标准技能缺少 SKILL.md")
    changed: list[str] = []
    for target in TARGETS:
        resolved_root = ROOT.resolve()
        resolved_target = target.resolve()
        if (
            target.is_symlink()
            or not resolved_target.is_relative_to(resolved_root)
            or any(parent.is_symlink() for parent in (target.parent, target.parent.parent))
        ):
            raise RuntimeError(f"目标技能目录不安全：{target}")
        current = files(target) if target.is_dir() else {}
        if current == source:
            continue
        if check_only:
            changed.append(target.relative_to(ROOT).as_posix())
            continue
        stale = sorted(set(current) - set(source))
        if stale:
            raise RuntimeError(
                f"目标技能包含标准源中不存在的文件，请人工确认后处理：{target} -> {stale}"
            )
        target.mkdir(parents=True, exist_ok=True)
        for relative, content in source.items():
            destination = target / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        changed.append(target.relative_to(ROOT).as_posix())
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 Codex CLI 与 Claude Code 的 Agent4Market 技能")
    parser.add_argument("--check", action="store_true", help="只检查，不修改文件")
    args = parser.parse_args()
    try:
        changed = sync(args.check)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.check and changed:
        print(f"技能副本未同步：{', '.join(changed)}", file=sys.stderr)
        return 1
    print("技能副本已同步。" if changed else "技能副本一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
