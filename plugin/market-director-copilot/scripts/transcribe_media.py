from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_model(model_name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed. Run: .venv\\Scripts\\python.exe -m pip install -r requirements.txt") from exc
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def extract_video_frames(path: Path, frames_dir: Path, max_frames: int) -> list[str]:
    try:
        import av
    except ImportError:
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    output: list[str] = []
    try:
        container = av.open(str(path))
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            container.close()
            return output
        total = max(1, int(stream.frames or 0))
        wanted = set(min(total - 1, round(total * i / max(1, max_frames - 1))) for i in range(max_frames))
        for index, frame in enumerate(container.decode(stream)):
            if index not in wanted:
                continue
            target = frames_dir / f"frame-{index:05d}.jpg"
            frame.to_image().save(target, quality=90)
            output.append(str(target))
        container.close()
    except Exception:
        return output
    return output


def transcribe(path: Path, model_name: str, device: str, compute_type: str, language: str, beam_size: int, vad_filter: bool) -> dict:
    model = load_model(model_name, device, compute_type)
    segments, info = model.transcribe(str(path), language=language or None, beam_size=beam_size, vad_filter=vad_filter)
    rows = [{"start": round(segment.start, 3), "end": round(segment.end, 3), "text": segment.text.strip()} for segment in segments]
    return {"source": str(path), "language": info.language, "duration": info.duration, "segments": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe local audio/video with faster-whisper.")
    parser.add_argument("media", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--frames-dir", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--no-vad", action="store_true")
    parser.add_argument("--max-frames", type=int, default=6)
    args = parser.parse_args()
    media = args.media.resolve()
    if not media.exists():
        print(f"Media not found: {media}", file=sys.stderr)
        return 2
    try:
        result = transcribe(media, args.model, args.device, args.compute_type, args.language, args.beam_size, not args.no_vad)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 3
    if args.frames_dir and media.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
        result["frames"] = extract_video_frames(media, args.frames_dir, args.max_frames)
    target = args.output or media.with_suffix(media.suffix + ".transcript.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

