"""Independent publisher signature, verified before an installer is executed."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from . import windows_update_engine as engine

TRUST_FILE = "agent_platform/windows_update_trust.json"


def public_key(root):
    trust = engine.read_json(engine.regular(root, TRUST_FILE), 4096)
    engine.require(isinstance(trust, dict) and set(trust) == {"format", "algorithm", "public_key"}
                   and trust["format"] == 1 and trust["algorithm"] == "ed25519", "SIGNING_KEY_REQUIRED")
    engine.require(isinstance(trust["public_key"], str), "SIGNING_KEY_REQUIRED")
    try:
        raw = base64.b64decode(trust["public_key"], validate=True)
        engine.require(len(raw) == 32, "SIGNING_KEY_REQUIRED")
        from Crypto.Signature import eddsa
        return eddsa.import_public_key(raw)
    except (ValueError, ImportError):
        raise engine.UpdateFailure("SIGNING_KEY_REQUIRED") from None


def signed_record(version, assets):
    engine.require(engine.VERSION.fullmatch(version), "INVALID_VERSION")
    return {"application": "Agent4Market", "platform": "windows-x64", "version": version,
            **{name: {"sha256": assets[name]["sha256"], "bytes": assets[name]["bytes"]} for name in ("installer", "manifest")}}


def canonical(record):
    return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")


def verify(root, path, version, assets):
    try:
        document = engine.read_json(Path(path), 4096)
        engine.require(isinstance(document, dict) and set(document) == {"format", "signed", "signature"}
                       and document["format"] == 1 and document["signed"] == signed_record(version, assets), "INVALID_SIGNATURE")
        signature = base64.b64decode(document["signature"], validate=True)
        engine.require(len(signature) == 64, "INVALID_SIGNATURE")
        from Crypto.Signature import eddsa
        eddsa.new(public_key(root), "rfc8032").verify(canonical(document["signed"]), signature)
    except (ValueError, TypeError, KeyError, ImportError):
        raise engine.UpdateFailure("INVALID_SIGNATURE") from None
