"""Synthetic inputs for native EXE acceptance; never access a WeChat process."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.runtime_root.resolve(), args.output.resolve()
    if not (root / "runtime/private-runtime.marker").is_file() or not output.is_relative_to(root):
        raise ValueError("Use only the explicitly prepared private runtime directory")
    # Production imports must resolve to the delivered copy, not the source tree.
    sys.path.insert(0, str(root))
    from agent_platform import wxdecipher
    assert Path(wxdecipher.__file__).resolve().is_relative_to(root)
    from wxdecipher_fixture import KEY_HEX, SELF, encrypt_fixture, make_database
    from test_wxdecipher_extensions import build_wal, encode_page, image_fixture, sqlite_wal_fixture, v2_fixture, wal_parts

    output.mkdir(parents=True, exist_ok=False)
    source = make_database(output / "synthetic-plain.db")
    encrypted = encrypt_fixture(source, output / "message_0.db")
    wal_root = output / "wal-fixture"
    wal_root.mkdir()
    wal_source, wal_file = sqlite_wal_fixture(wal_root, reserve=True, count=2, conversation="wxid_wal_synthetic_client")
    plaintext = wal_source.read_bytes()
    wal_database = wal_root / "message_1.db"
    wal_database.write_bytes(b"".join(encode_page(plaintext[pos:pos + 4096], pos // 4096 + 1) for pos in range(0, len(plaintext), 4096)))
    header, frames = wal_parts(wal_file.read_bytes())
    wal_selected = wal_root / "message_1.db-wal"
    wal_selected.write_bytes(build_wal(header, frames, encrypted=True))
    media_plain = output / "synthetic.png"
    media_plain.write_bytes(image_fixture())
    media_dat = output / "synthetic.dat"
    media_dat.write_bytes(v2_fixture(media_plain.read_bytes()))
    manifest = {"root": str(root), "database": str(encrypted), "key": KEY_HEX, "self_id": SELF,
                "wal_database": str(wal_database), "wal": str(wal_selected),
                "media": str(media_dat), "media_plain": str(media_plain)}
    (output / "fixture.json").write_text(json.dumps(manifest), encoding="utf-8")
    print(json.dumps({"status": "ok", "fixture": str(output / "fixture.json"), "production_root": str(root)}))


if __name__ == "__main__":
    main()
