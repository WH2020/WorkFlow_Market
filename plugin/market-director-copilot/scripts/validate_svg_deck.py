from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree


def validate(directory: Path, min_pages: int | None = None, max_pages: int | None = None) -> list[str]:
    errors: list[str] = []
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return ["manifest_missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"invalid_manifest:{exc}"]
    canvas = manifest.get("canvas") or {}
    if (canvas.get("width"), canvas.get("height")) != (1280, 720):
        errors.append("canvas_must_be_1280x720")
    pages = manifest.get("pages") or []
    if min_pages is not None and len(pages) < min_pages:
        errors.append(f"page_count_below_min:{len(pages)}<{min_pages}")
    if max_pages is not None and len(pages) > max_pages:
        errors.append(f"page_count_above_max:{len(pages)}>{max_pages}")
    for page in pages:
        path = directory / str(page.get("file", ""))
        if not path.exists():
            errors.append(f"page_missing:{path.name}")
            continue
        try:
            root = ElementTree.parse(path).getroot()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid_svg:{path.name}:{exc}")
            continue
        if root.get("viewBox") != "0 0 1280 720":
            errors.append(f"invalid_viewbox:{path.name}")
        for box in page.get("boxes", []):
            if box.get("x", 0) < 48 or box.get("y", 0) < 124 or box.get("x", 0) + box.get("width", 0) > 1232 or box.get("y", 0) + box.get("height", 0) > 660:
                errors.append(f"card_out_of_bounds:{path.name}")
        if root.findall(".//*[@data-clipped='true']"):
            errors.append(f"text_clipped:{path.name}")
        text = "".join(root.itertext())
        if any(marker in text for marker in ("[主题", "[结论", "[待确认")):
            errors.append(f"placeholder_text:{path.name}")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate SVG-first Bento Grid pages.")
    parser.add_argument("--svg-dir", type=Path, required=True)
    parser.add_argument("--min-pages", type=int)
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args()
    errors = validate(args.svg_dir.resolve(), args.min_pages, args.max_pages)
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
