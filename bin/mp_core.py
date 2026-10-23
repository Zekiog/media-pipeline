#!/usr/bin/env python3
"""media-pipeline core: probe / process / translate for video, audio and images.

CPU-only, no torch, ARM-native. Engines:
  FFmpeg          - format normalization, audio extraction, thumbnails
  faster-whisper  - speech to text (int8)
  RapidOCR        - PP-OCRv4 text detection on images (ONNX)
  LiteLLM proxy   - translation and image description (optional)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
INBOX = Path(os.environ.get("MP_INBOX", ROOT / "inbox"))
WORK = Path(os.environ.get("MP_WORK", ROOT / "work"))
OUTBOX = Path(os.environ.get("MP_OUTBOX", ROOT / "outbox"))
FAILURES = Path(os.environ.get("MP_FAILURES", ROOT / "failures"))
LOGS = Path(os.environ.get("MP_LOGS", ROOT / "logs"))

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".flac", ".opus", ".wma"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic"}

MAX_INPUT_BYTES = int(os.environ.get("MP_MAX_INPUT_BYTES", str(512 * 1024 * 1024)))
FFMPEG_TIMEOUT = int(os.environ.get("MP_FFMPEG_TIMEOUT", "900"))
WHISPER_MODEL = os.environ.get("MP_WHISPER_MODEL", "tiny")
WHISPER_COMPUTE = os.environ.get("MP_WHISPER_COMPUTE", "int8")
LITELLM_BASE = os.environ.get("MP_LITELLM_BASE", "http://127.0.0.1:4000/v1")
LITELLM_KEY = os.environ.get("MP_LITELLM_KEY", "")
LITELLM_MODEL = os.environ.get("MP_LITELLM_MODEL", "")
TRANSLATE_TARGET = os.environ.get("MP_TRANSLATE_TARGET", "tr")


def ensure_dirs() -> None:
    for d in (INBOX, WORK, OUTBOX, FAILURES, LOGS):
        d.mkdir(parents=True, exist_ok=True)


def media_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def run(cmd: list[str], timeout: int = FFMPEG_TIMEOUT, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a command without a shell. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"


def ffprobe_json(path: Path) -> dict[str, Any]:
    if not have("ffprobe"):
        return {"error": "ffprobe not installed"}
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    rc, out, err = run(cmd, timeout=120)
    if rc != 0:
        return {"error": err.strip()[:400] or f"ffprobe rc={rc}"}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"error": "ffprobe json parse failed"}


def summarize_probe(data: dict[str, Any]) -> dict[str, Any]:
    if "error" in data:
        return data
    fmt = data.get("format", {})
    streams = []
    for s in data.get("streams", []):
        streams.append({
            "index": s.get("index"),
            "type": s.get("codec_type"),
            "codec": s.get("codec_name"),
            "width": s.get("width"),
            "height": s.get("height"),
            "duration_s": _duration(s.get("duration")),
            "sample_rate": s.get("sample_rate"),
            "channels": s.get("channels"),
            "language": (s.get("tags") or {}).get("language"),
        })
    return {
        "format": fmt.get("format_name"),
        "duration_s": _duration(fmt.get("duration")),
        "size_bytes": int(fmt.get("size") or 0),
        "bit_rate": fmt.get("bit_rate"),
        "streams": streams,
    }


def _duration(v: Any) -> float | None:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def extract_audio(src: Path, dst_wav: Path) -> tuple[bool, str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(dst_wav),
    ]
    rc, _, err = run(cmd, timeout=FFMPEG_TIMEOUT)
    return rc == 0, err.strip()[:500]


def normalize_video(src: Path, dst: Path, crf: int = 28, preset: str = "veryfast") -> tuple[bool, str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k",
        str(dst),
    ]
    rc, _, err = run(cmd, timeout=FFMPEG_TIMEOUT)
    return rc == 0, err.strip()[:500]


def thumbnail(src: Path, dst: Path, at_s: float = 1.0) -> tuple[bool, str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(at_s), "-i", str(src), "-frames:v", "1",
        "-vf", "scale=1280:-2", str(dst),
    ]
    rc, _, err = run(cmd, timeout=180)
    return rc == 0, err.strip()[:500]


def transcribe(audio: Path, model_size: str | None = None) -> dict[str, Any]:
    """Speech to text with faster-whisper. Returns text plus segment list."""
    size = model_size or WHISPER_MODEL
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        return {"error": f"faster_whisper import failed: {exc}"}
    try:
        model = WhisperModel(size, device="cpu", compute_type=WHISPER_COMPUTE)
        segments, info = model.transcribe(str(audio), language=None, vad_filter=True)
        segs = []
        text_parts = []
        for s in segments:
            segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
            text_parts.append(s.text.strip())
        return {
            "text": " ".join(p for p in text_parts if p).strip(),
            "segments": segs,
            "language": info.language,
            "language_probability": round(float(info.language_probability), 3),
            "duration_s": round(float(info.duration), 2),
            "model": size,
        }
    except Exception as exc:
        return {"error": f"transcription failed: {type(exc).__name__}: {exc}"}


def segments_to_srt(segs: list[dict[str, Any]]) -> str:
    def ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    out = []
    for i, s in enumerate(segs, 1):
        out.append(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n")
    return "\n".join(out)


def segments_to_vtt(segs: list[dict[str, Any]]) -> str:
    def ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    out = ["WEBVTT", ""]
    for s in segs:
        out.append(f"{ts(s['start'])} --> {ts(s['end'])}")
        out.append(s["text"])
        out.append("")
    return "\n".join(out)


_ocr_engine = None


def ocr_image(path: Path) -> dict[str, Any]:
    """Text detection with RapidOCR (PP-OCRv4, ONNX). No torch needed."""
    global _ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        return {"error": f"rapidocr import failed: {exc}"}
    try:
        if _ocr_engine is None:
            _ocr_engine = RapidOCR()
        result, _ = _ocr_engine(str(path))
    except Exception as exc:
        return {"error": f"ocr failed: {type(exc).__name__}: {exc}"}
    lines = []
    confs = []
    for item in (result or []):
        try:
            box, text, conf = item[0], item[1], item[2]
            lines.append(text)
            confs.append(float(conf))
        except Exception:
            continue
    return {
        "text": "\n".join(lines),
        "lines": len(lines),
        "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
    }


def llm_complete(prompt: str, system: str = "", model: str | None = None,
                 timeout: int = 120) -> dict[str, Any]:
    """Call an OpenAI-compatible endpoint (LiteLLM proxy by default)."""
    import urllib.request
    import urllib.error

    if not LITELLM_KEY:
        return {"error": "no API key configured (MP_LITELLM_KEY)"}
    m = model or LITELLM_MODEL
    if not m:
        return {"error": "no model configured (MP_LITELLM_MODEL)"}
    body = {
        "model": m,
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{LITELLM_BASE}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return {"text": data["choices"][0]["message"]["content"], "model": data.get("model", m)}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def translate_text(text: str, target: str = TRANSLATE_TARGET,
                   source: str = "auto") -> dict[str, Any]:
    if not text.strip():
        return {"text": "", "skipped": "empty input"}
    prompt = (
        f"Translate the following text into {target}. "
        f"Reply with the translation only, no notes.\n\n{text}"
    )
    r = llm_complete(prompt, system="You are a precise translator. Output only the translation.")
    if "error" in r:
        return r
    return {"text": r["text"].strip(), "target": target, "source": source}


def describe_image(path: Path) -> dict[str, Any]:
    """Describe an image via a vision-capable model on the LiteLLM proxy."""
    import base64
    import mimetypes

    if not LITELLM_KEY:
        return {"error": "no API key configured (MP_LITELLM_KEY)"}
    m = LITELLM_MODEL
    if not m:
        return {"error": "no model configured (MP_LITELLM_MODEL)"}
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode()
    body = {
        "model": m,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image in detail. Then list any visible text verbatim."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{LITELLM_BASE}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode())
        return {"text": data["choices"][0]["message"]["content"], "model": data.get("model", m)}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
