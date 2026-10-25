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
import re
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
# LLM saglayici zinciri: "auto" = once anahtarsiz Pollinations, sonra anahtarli LiteLLM.
LLM_PROVIDER = os.environ.get("MP_LLM_PROVIDER", "auto")
POLLINATIONS_BASE = os.environ.get("MP_POLLINATIONS_BASE", "https://text.pollinations.ai/openai")
POLLINATIONS_MODEL = os.environ.get("MP_POLLINATIONS_MODEL", "openai")
POLLINATIONS_WAIT = int(os.environ.get("MP_POLLINATIONS_WAIT", "35"))
# Google AI Studio (Gemini) ucretsiz tier: vision destekler, anahtar gerekir.
GEMINI_KEY = os.environ.get("MP_GEMINI_KEY", "")
GEMINI_MODEL = os.environ.get("MP_GEMINI_MODEL", "gemini-2.0-flash")


def dosya_hash(path: Path) -> str:
    """sha256 of a file, used to recognise media that was already processed."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


def seen_yukle() -> dict:
    """Map of sha256 -> result dir for media that was already processed."""
    dosya = WORK / "processed.json"
    if not dosya.is_file():
        return {}
    try:
        return json.loads(dosya.read_text())
    except Exception:
        return {}


def seen_kaydet(kayit: dict) -> None:
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK / "processed.json").write_text(json.dumps(kayit, ensure_ascii=False, indent=1))
    except Exception:
        pass


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


def _openai_call(base: str, model: str, body: dict, auth: str | None,
                 timeout: int = 120, wait_on_limit: bool = True,
                 yol: str = "/chat/completions") -> dict:
    """OpenAI-uyumlu chat/completions cagrisi.

    Pollinations ucretsiz tier'da 402 FREE_LIMIT doner: IP basina 30 saniyede
    bir yanit. Bu durumda hata metnindeki "Retry after Ns" degeri kadar
    beklenip bir kez daha denenir.
    """
    import urllib.request
    import urllib.error

    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = auth
    deneme = 0
    while True:
        url = f"{base.rstrip('/')}{yol}" if yol else base.rstrip("/")
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
            return {"text": data["choices"][0]["message"]["content"],
                    "model": data.get("model", model)}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            bekle = 0
            try:
                hata = json.loads(raw)
                if e.code == 402 and hata.get("error", {}).get("code") == "FREE_LIMIT":
                    m = re.search(r"Retry after (\d+)s", hata["error"].get("message", ""))
                    bekle = int(m.group(1)) if m else 20
            except Exception:
                pass
            if wait_on_limit and bekle and deneme < 1:
                deneme += 1
                time.sleep(min(bekle, POLLINATIONS_WAIT))
                continue
            return {"error": f"HTTP {e.code}: {raw[:300]}"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}


def _gemini_call(model: str, metin: str, resim_b64: str | None = None,
                 mime: str = "image/png", timeout: int = 120) -> dict:
    """Google AI Studio (Gemini) native generateContent cagrisi.

    Ucretsiz tier: gunde 20 istek. Gorsel girdi inline_data ile gonderilir.
    """
    import base64
    import urllib.request
    import urllib.error

    parcalar = [{"text": metin}]
    if resim_b64:
        parcalar.append({"inline_data": {"mime_type": mime, "data": resim_b64}})
    govde = {
        "contents": [{"role": "user", "parts": parcalar}],
        "generationConfig": {"temperature": 0.2},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           f":generateContent?key={GEMINI_KEY}")
    req = urllib.request.Request(url, data=json.dumps(govde).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        parca = data["candidates"][0]["content"]["parts"][0].get("text", "")
        return {"text": parca, "model": model}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _saglayicilar() -> list:
    """Kullanilacak saglayici sirasi. Pollinations anahtarsiz, LiteLLM anahtar ister."""
    sec = (LLM_PROVIDER or "auto").lower()
    if sec == "none":
        return []
    zincir = []
    if sec in ("auto", "pollinations"):
        zincir.append("pollinations")
    if sec in ("auto", "gemini") and GEMINI_KEY:
        zincir.append("gemini")
    if sec in ("auto", "litellm") and LITELLM_KEY:
        zincir.append("litellm")
    return zincir

def llm_complete(prompt: str, system: str = "", model: str | None = None,
                 timeout: int = 120) -> dict[str, Any]:
    """Call an OpenAI-compatible endpoint.

    Keyless Pollinations first, then the LiteLLM proxy when a key is set.
    """
    zincir = _saglayicilar()
    if not zincir:
        return {"error": "no provider configured (MP_LLM_PROVIDER / MP_LITELLM_KEY)"}
    mesajlar = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    hatalar = []
    for saglayici in zincir:
        if saglayici == "pollinations":
            m = model or POLLINATIONS_MODEL
            r = _openai_call(POLLINATIONS_BASE, m,
                             {"model": m, "messages": mesajlar, "temperature": 0.2},
                             auth=None, timeout=timeout, yol="")
        elif saglayici == "gemini":
            r = _gemini_call(GEMINI_MODEL, prompt)
        else:
            m = model or LITELLM_MODEL
            if not m:
                hatalar.append("litellm: MP_LITELLM_MODEL yok")
                continue
            r = _openai_call(LITELLM_BASE, m,
                             {"model": m, "messages": mesajlar, "temperature": 0.2},
                             auth=f"Bearer {LITELLM_KEY}", timeout=timeout,
                             wait_on_limit=False)
        if "error" not in r:
            return {"text": r["text"].strip(), "model": r.get("model"), "provider": saglayici}
        hatalar.append(f"{saglayici}: {r['error']}")
    return {"error": "; ".join(hatalar)}

def translate_text(text: str, target: str = TRANSLATE_TARGET,
                   source: str = "auto") -> dict[str, Any]:
    """Translate text with the configured provider chain."""
    if not text.strip():
        return {"text": "", "skipped": "empty input"}
    zincir = _saglayicilar()
    if not zincir:
        return {"error": "no translation provider configured (MP_LLM_PROVIDER=none)"}
    sistem = "You are a precise translator. Output only the translation."
    mesajlar = [{"role": "system", "content": sistem},
                {"role": "user", "content":
                 f"Translate the following text into {target}. "
                 f"Reply with the translation only, no notes.\n\n{text}"}]
    hatalar = []
    for saglayici in zincir:
        if saglayici == "pollinations":
            m = POLLINATIONS_MODEL
            r = _openai_call(POLLINATIONS_BASE, m,
                             {"model": m, "messages": mesajlar, "temperature": 0.2},
                             auth=None, timeout=120, yol="")
        elif saglayici == "gemini":
            r = _gemini_call(GEMINI_MODEL,
                             f"Translate the following text into {target}. "
                             f"Reply with the translation only, no notes.\n\n{text}")
        else:
            m = LITELLM_MODEL
            if not m:
                hatalar.append("litellm: MP_LITELLM_MODEL yok")
                continue
            r = _openai_call(LITELLM_BASE, m,
                             {"model": m, "messages": mesajlar, "temperature": 0.2},
                             auth=f"Bearer {LITELLM_KEY}", timeout=120,
                             wait_on_limit=False)
        if "error" not in r:
            return {"text": r["text"].strip(), "target": target, "source": source,
                    "provider": saglayici, "model": r.get("model")}
        hatalar.append(f"{saglayici}: {r['error']}")
    return {"error": "; ".join(hatalar), "target": target, "source": source}

def describe_image(path: Path) -> dict[str, Any]:
    """Describe an image with a vision model.

    Provider chain: keyless Pollinations vision first, then the LiteLLM proxy
    when an API key is configured. Pollinations allows one reply per 30 s per
    IP, so a rate limit is reported instead of blocking the job.
    """
    import base64
    import mimetypes

    zincir = _saglayicilar()
    if not zincir:
        return {"error": "no vision provider configured (MP_LLM_PROVIDER=none)"}
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode()
    mesajlar = [{"role": "user", "content": [
        {"type": "text", "text": "Describe this image in detail. Then list any visible text verbatim."},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]}]
    hatalar = []
    for saglayici in zincir:
        if saglayici == "pollinations":
            # Classic API'deki tek model metin-only (vision: false).
            hatalar.append("pollinations: vision desteklenmiyor (MP_GEMINI_KEY veya MP_LITELLM_KEY gerekli)")
            continue
        if saglayici == "gemini":
            r = _gemini_call(GEMINI_MODEL,
                             "Describe this image in detail. "
                             "Then list any visible text verbatim.",
                             resim_b64=b64, mime=mime, timeout=180)
        else:
            m = LITELLM_MODEL
            if not m:
                hatalar.append("litellm: MP_LITELLM_MODEL yok")
                continue
            r = _openai_call(LITELLM_BASE, m,
                             {"model": m, "messages": mesajlar, "temperature": 0.2},
                             auth=f"Bearer {LITELLM_KEY}", timeout=180,
                             wait_on_limit=False)
        if "error" not in r:
            return {"text": r["text"].strip(), "model": r.get("model"), "provider": saglayici}
        hatalar.append(f"{saglayici}: {r['error']}")
    return {"error": "; ".join(hatalar)}

