"""ARM headless media pipeline - proven recipes, traps and a readiness check.

Learned while building a local media pipeline on a 2 vCPU aarch64 host with a
90 percent full root disk. Import this module when you need speech to text,
OCR, video processing or a directory trigger on such a host.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess

__all__ = [
    "ARM_TRAPS",
    "ENGINE_RECIPES",
    "INSTALL_COMMANDS",
    "check_engines",
    "install_commands",
    "reference_repo",
]

ARM_TRAPS = [
    {
        "id": "libgl-headless-trap",
        "trap": "opencv-python does not import on a headless host: "
                "libGL.so.1: cannot open shared object file.",
        "fix": "install opencv-python-headless and force-reinstall it over the regular package. "
               "RapidOCR imports cv2 at load time, so the OCR path dies with an unrelated looking error.",
        "verified": "af-arm 2026-10-23",
    },
    {
        "id": "system-python-too-old",
        "trap": "/usr/bin/python3 can be far older than expected (3.6 on Oracle Linux 8). "
                "A '#!/usr/bin/env python3' shebang then breaks on 'from __future__ import annotations'.",
        "fix": "ship a POSIX sh wrapper that prefers the project venv, then python3.11, then python3.",
        "verified": "af-arm 2026-10-23",
    },
    {
        "id": "ffmpeg-without-drawtext",
        "trap": "a minimal ffmpeg build has no drawtext filter (no freetype), "
                "so 'No such filter' breaks any text-in-frame fixture.",
        "fix": "generate text images with Pillow instead; check the filter list before designing a fixture.",
        "verified": "af-arm 2026-10-23",
    },
    {
        "id": "bot-ignores-own-messages",
        "trap": "a bridge that drops messages whose sender is a bot also drops your own test messages, "
                "silently.",
        "fix": "keep the file_id from the sendPhoto response and drive download plus processing directly; "
               "the fetch is still a real API call.",
        "verified": "af-arm 2026-10-23",
    },
    {
        "id": "path-unit-fires-on-empty",
        "trap": "a systemd .path unit fires on every directory change, including when nothing is pending.",
        "fix": "make the worker idempotent and tolerate an empty queue.",
        "verified": "af-arm 2026-10-23",
    },
    {
        "id": "import-check-is-not-proof",
        "trap": "a successful import proves nothing about the pipeline.",
        "fix": "run one real file end to end and read the extracted text and its confidence.",
        "verified": "af-arm 2026-10-23",
    },
]

ENGINE_RECIPES = {
    "speech_to_text": {
        "library": "faster-whisper",
        "backend": "CTranslate2, int8, CPU",
        "why": "no torch, aarch64 wheels, roughly 500 MB venv in total",
        "model_sizes": {"tiny": "75 MB", "base": "145 MB", "small": "480 MB", "medium": "1.5 GB"},
    },
    "image_ocr": {
        "library": "rapidocr-onnxruntime",
        "backend": "PP-OCRv4 through ONNX Runtime",
        "measured_confidence": "0.97 to 0.98 on clean synthetic images, 0.98 on a real Telegram JPEG",
        "weak_spots": ["handwriting", "low contrast", "rotated text", "text over busy backgrounds"],
    },
    "media_format": {"library": "FFmpeg", "notes": "always check for optional filters such as drawtext"},
    "images": {"library": "Pillow"},
    "directory_trigger": {
        "library": "systemd path unit",
        "notes": "inotify based, needs no inotify-tools package and no root",
    },
}

INSTALL_COMMANDS = [
    "/usr/bin/python3.11 -m venv .venv",
    "uv pip install --python .venv/bin/python faster-whisper pillow onnxruntime rapidocr-onnxruntime",
    "uv pip install --python .venv/bin/python --force-reinstall opencv-python-headless",
]

REFERENCE_REPO = "/home/opc/work/prime/media-pipeline"


def reference_repo() -> str:
    """Path of the reference implementation that these lessons came from."""
    return REFERENCE_REPO


def check_engines() -> dict:
    """Report which media engines are actually usable on this host."""
    result = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "tesseract": bool(shutil.which("tesseract")),
    }
    for module, key in (
        ("faster_whisper", "faster_whisper"),
        ("ctranslate2", "ctranslate2"),
        ("onnxruntime", "onnxruntime"),
        ("rapidocr_onnxruntime", "rapidocr"),
        ("PIL", "pillow"),
    ):
        try:
            mod = importlib.import_module(module)
            result[key] = True
            version = getattr(mod, "__version__", None)
            if version:
                result[key + "_version"] = version
        except Exception as exc:
            result[key] = f"missing: {type(exc).__name__}: {exc}"
    result["ready"] = all(result.get(k) is True for k in ("ffmpeg", "ffprobe", "faster_whisper", "rapidocr", "pillow"))
    return result


def install_commands(python: str = "/usr/bin/python3.11") -> list:
    """Exact install commands for the light media stack."""
    cmds = list(INSTALL_COMMANDS)
    return [c if not c.startswith("/usr/bin/python3.11") else c.replace("/usr/bin/python3.11", python) for c in cmds]
