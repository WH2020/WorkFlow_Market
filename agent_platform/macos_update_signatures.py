"""Platform-separated macOS signatures under the pinned publisher trust key."""
import base64

from . import macos_update_engine as engine
from .windows_update_signatures import TRUST_FILE, public_key, canonical


def signed_record(version, assets):
    engine.require(engine.VERSION.fullmatch(version), 'INVALID_VERSION')
    return {'application': 'Agent4Market', 'platform': 'macos-universal', 'version': version,
            **{name: {'sha256': assets[name]['sha256'], 'bytes': assets[name]['bytes']}
               for name in ('programs', 'app', 'manifest')}}


def verify(root, path, version, assets):
    try:
        document = engine.read_json(path, 4096)
        engine.require(isinstance(document, dict) and set(document) == {'format', 'signed', 'signature'}
                       and document['format'] == 1 and document['signed'] == signed_record(version, assets), 'INVALID_SIGNATURE')
        signature = base64.b64decode(document['signature'], validate=True)
        engine.require(len(signature) == 64, 'INVALID_SIGNATURE')
        from Crypto.Signature import eddsa
        eddsa.new(public_key(root), 'rfc8032').verify(canonical(document['signed']), signature)
    except (ValueError, TypeError, KeyError, ImportError):
        raise engine.UpdateFailure('INVALID_SIGNATURE') from None
