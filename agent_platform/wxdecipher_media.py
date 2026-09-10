"""Explicitly uploaded media only: no paths, discovery, network or persistence.

V2 layout was cross-checked against the public wechatauto/media.py format notes.
The decoder is independently implemented and rejects unknown layouts. Image
validation is structural, not cryptographic authentication (DAT has no MAC).
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import re
import struct
import threading
import warnings
import wave
from typing import BinaryIO

from .wechat_store import WechatStoreError
from .wxdecipher_crypto import _aes

MAX_MEDIA_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_FRAME_PIXELS = 64_000_000
V2_MAGIC = b"\x07\x08V2\x08\x07"
MAGICS = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")
IMAGE_TYPES = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
               "GIF": ("gif", "image/gif"), "WEBP": ("webp", "image/webp"), "BMP": ("bmp", "image/bmp")}
_LOCK = threading.Lock()


def parse_image_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise WechatStoreError("IMAGE_KEY_FORMAT", "图片密钥须为 16 个 ASCII 字符，或 32 位十六进制")
    if re.fullmatch(r"[0-9a-fA-F]{32}", value):
        return bytes.fromhex(value)
    if re.fullmatch(r"[\x21-\x7e]{16}", value):
        return value.encode("ascii")
    raise WechatStoreError("IMAGE_KEY_FORMAT", "V2 图片密钥须为 16 个 ASCII 字符，或 32 位十六进制；它不是数据库密钥")


def parse_xor_key(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{1,3}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= 255:
        raise WechatStoreError("IMAGE_KEY_FORMAT", "图片 XOR 参数须为 0–255 的十进制整数，或留空自动反推")
    return value


def _looks_like_image(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in MAGICS) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def image_probe(data: bytes) -> bytes:
    """Return the first V2 ciphertext block only after validating segment bounds."""
    if len(data) < 31 or data[:6] != V2_MAGIC:
        raise WechatStoreError("UNSUPPORTED_MEDIA", "V2 图片恢复需要一份完整的 V2 DAT 副本")
    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    padded = (aes_size // 16 + 1) * 16
    if aes_size < 16 or padded > len(data) - 15 or xor_size > len(data) - 15 - padded:
        raise WechatStoreError("INVALID_MEDIA", "V2 图片分段长度无效或文件不完整")
    return data[15:31]


def _validate_image(data: bytes) -> dict:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as error:
        raise WechatStoreError("DEPENDENCY_MISSING", "媒体恢复需要 Pillow，请安装 requirements-wxdecipher.txt") from error
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(IMAGE_TYPES)) as selected:
                width, height = selected.size
                frames = getattr(selected, "n_frames", 1)
                if not 0 < width * height <= MAX_PIXELS or not 1 <= frames <= 200 or width * height * frames > MAX_FRAME_PIXELS:
                    raise WechatStoreError("MEDIA_LIMIT", "图片像素或动画帧数超过安全处理上限")
                format_name = selected.format
                selected.verify()
            with Image.open(io.BytesIO(data), formats=list(IMAGE_TYPES)) as selected:
                # Decode every bounded animation frame, not merely its magic.
                for frame in range(frames):
                    selected.seek(frame)
                    selected.load()
        extension, mime = IMAGE_TYPES[format_name]
        return {"extension": extension, "mime_type": mime, "previewable": True,
                "width": width, "height": height, "frames": frames, "validation": "image-decoded"}
    except WechatStoreError:
        raise
    except (ValueError, OSError, SyntaxError, EOFError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise WechatStoreError("INVALID_MEDIA", "恢复结果未通过完整图片解码校验；请检查图片密钥、XOR 参数和副本") from error


def _decode_v2(data: bytes, image_key: str, xor_key: int | None) -> bytes:
    image_probe(data)
    key = parse_image_key(image_key)
    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    padded = (aes_size // 16 + 1) * 16
    AES = _aes()
    prefix = AES.new(key, AES.MODE_ECB).decrypt(data[15:15 + padded])
    padding = prefix[-1]
    if not 1 <= padding <= 16 or prefix[-padding:] != bytes([padding]) * padding or len(prefix) - padding != aes_size:
        raise WechatStoreError("IMAGE_KEY_MISMATCH", "图片 AES 密钥或分段格式不匹配")
    prefix = prefix[:-padding]
    if not _looks_like_image(prefix):
        raise WechatStoreError("UNSUPPORTED_MEDIA", "解码头不是支持的图片；WXGF/HEVC 等容器暂不能在此恢复")
    raw_end = len(data) - xor_size
    tail = data[raw_end:]
    if xor_size and xor_key is None:
        # Only infer from a known full end marker. Never silently default to 0x88.
        ending = b"\xff\xd9" if prefix.startswith(b"\xff\xd8\xff") else (
            b"\x00\x00\x00\x00IEND\xaeB`\x82" if prefix.startswith(MAGICS[1]) else b"")
        if not ending or len(tail) < len(ending):
            raise WechatStoreError("IMAGE_XOR_REQUIRED", "此格式无法可靠反推 XOR 参数，请手动填写 0–255")
        xor_key = tail[-len(ending)] ^ ending[0]
        if bytes(value ^ xor_key for value in tail[-len(ending):]) != ending:
            raise WechatStoreError("IMAGE_XOR_REQUIRED", "图片尾标记无法确认 XOR 参数，请手动填写；不会猜测默认值")
    return prefix + data[15 + padded:raw_end] + bytes(value ^ (xor_key or 0) for value in tail)


def decode_media(data: bytes, *, image_key: str = "", xor_key: object = None) -> tuple[bytes, dict]:
    if not 1 <= len(data) <= MAX_MEDIA_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "单个媒体副本必须为 1 字节至 16 兆字节")
    xor_value = parse_xor_key(xor_key)
    decoder = "original"
    raw = data
    if data[:6] == V2_MAGIC:
        raw = _decode_v2(data, image_key, xor_value)
        decoder = "v2-aes-xor"
    elif data.startswith(b"\x07\x08"):
        raise WechatStoreError("UNSUPPORTED_MEDIA", "未知或尚未校准的 DAT 版本；当前支持旧版单字节 XOR 和 V2 AES 分段")
    elif not _looks_like_image(data):
        if data.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
            if len(data) < 14:
                raise WechatStoreError("INVALID_MEDIA", "SILK 文件不完整")
            return data, {"extension": "silk", "mime_type": "application/octet-stream", "previewable": False,
                          "decoder": "original", "validation": "signature-only",
                          "warning": "仅恢复原始 SILK 文件，未转码或验证音频帧；浏览器不能直接播放。"}
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            try:
                with wave.open(io.BytesIO(data), "rb") as audio:
                    expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                    if expected > MAX_MEDIA_BYTES or len(audio.readframes(audio.getnframes())) != expected:
                        raise ValueError()
            except (EOFError, ValueError, wave.Error) as error:
                raise WechatStoreError("INVALID_MEDIA", "WAV 音频结构或数据长度无效") from error
            return data, {"extension": "wav", "mime_type": "audio/wav", "previewable": False,
                          "decoder": "original", "validation": "pcm-length"}
        if data[4:8] == b"ftyp":
            offset, boxes = 0, set()
            while offset < len(data):
                if len(data) - offset < 8:
                    raise WechatStoreError("INVALID_MEDIA", "MP4 容器尾部不完整")
                size, kind = struct.unpack_from(">I4s", data, offset)
                header = 8
                if size == 1:
                    if len(data) - offset < 16:
                        raise WechatStoreError("INVALID_MEDIA", "MP4 扩展长度不完整")
                    size = struct.unpack_from(">Q", data, offset + 8)[0]
                    header = 16
                if size == 0:
                    size = len(data) - offset
                if not header <= size <= len(data) - offset:
                    raise WechatStoreError("INVALID_MEDIA", "MP4 容器长度无效")
                boxes.add(kind)
                offset += size
            if not {b"ftyp", b"moov", b"mdat"} <= boxes:
                raise WechatStoreError("INVALID_MEDIA", "MP4 缺少必要容器")
            return data, {"extension": "mp4", "mime_type": "video/mp4", "previewable": False,
                          "decoder": "original", "validation": "container-only",
                          "warning": "仅校验并恢复 MP4 容器，未验证音视频编码；不会自动打开播放器。"}
        candidates = []
        for magic in MAGICS:
            candidate = data[0] ^ magic[0]
            if bytes(value ^ candidate for value in data[:len(magic)]) == magic:
                candidates.append(candidate)
        # WEBP has a variable length between its two magic fields.
        candidate = data[0] ^ ord("R")
        if bytes(value ^ candidate for value in data[:4]) == b"RIFF" and bytes(value ^ candidate for value in data[8:12]) == b"WEBP":
            candidates.append(candidate)
        if not candidates:
            raise WechatStoreError("UNSUPPORTED_MEDIA", "未识别到支持的图片、MP4、WAV 或 SILK；不执行、不联网获取未知附件")
        raw = data.translate(bytes(value ^ candidates[0] for value in range(256)))
        decoder = "legacy-xor"
    report = _validate_image(raw)
    return raw, {**report, "decoder": decoder,
                 "warning": "图片已通过结构和解码检查；DAT 不带认证码，无法保证内容未被篡改。"}


@contextmanager
def restore_upload(stream: BinaryIO, length: int, *, ownership_confirmed: bool,
                   image_key: str = "", xor_key: object = None):
    """Hold the media budget until the caller has finished sending the bytes."""
    if ownership_confirmed is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请先确认所选媒体属于本人账号且有权处理")
    if not 1 <= length <= MAX_MEDIA_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "单个媒体副本必须为 1 字节至 16 兆字节")
    if not _LOCK.acquire(blocking=False):
        raise WechatStoreError("DECIPHER_BUSY", "另一个媒体副本正在处理，请稍后重试")
    try:
        data = stream.read(length)
        if len(data) != length:
            raise WechatStoreError("INVALID_MEDIA", "媒体上传提前中断")
        raw, report = decode_media(data, image_key=image_key, xor_key=xor_key)
        digest = hashlib.sha256(raw).hexdigest()
        yield raw, {**report, "filename": f"wechat-media-{digest[:16]}.{report['extension']}",
                    "bytes": len(raw), "sha256": digest}
    finally:
        _LOCK.release()
