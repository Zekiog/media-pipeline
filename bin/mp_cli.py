#!/usr/bin/env python3
"""mp - media pipeline CLI.

Usage:
  mp probe FILE              show media metadata (ffprobe)
  mp process FILE            full pipeline: normalize, extract audio, transcribe, OCR
  mp translate FILE          extract text then translate it
  mp list                    list inbox / outbox
  mp watch                   process every file in the inbox once
  mp selftest                check which engines are available
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mp_core as C  # noqa: E402


def out(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def selftest() -> dict:
    eng = {
        "ffmpeg": C.have("ffmpeg"),
        "ffprobe": C.have("ffprobe"),
        "tesseract": C.have("tesseract"),
        "whisper_model": C.WHISPER_MODEL,
        "llm_key_configured": bool(C.LITELLM_KEY),
        "llm_model": C.LITELLM_MODEL or None,
        "llm_base": C.LITELLM_BASE,
        "llm_provider": C._saglayicilar(),
    }
    try:
        import faster_whisper  # noqa: F401
        eng["faster_whisper"] = True
    except Exception as exc:
        eng["faster_whisper"] = f"missing: {exc}"
    try:
        import rapidocr_onnxruntime  # noqa: F401
        eng["rapidocr"] = True
    except Exception as exc:
        eng["rapidocr"] = f"missing: {exc}"
    try:
        from PIL import Image  # noqa: F401
        eng["pillow"] = True
    except Exception as exc:
        eng["pillow"] = f"missing: {exc}"
    eng["ok"] = all(v is True for k, v in eng.items()
                    if k in ("ffmpeg", "ffprobe", "faster_whisper", "rapidocr", "pillow"))
    return eng


def process_file(src: Path, do_stt: bool = True, do_ocr: bool = True,
                 do_translate: bool = False, target: str = C.TRANSLATE_TARGET) -> dict:
    started = time.time()
    C.ensure_dirs()
    src = src.resolve()
    if not src.is_file():
        return {"error": f"not a file: {src}"}
    size = src.stat().st_size
    if size > C.MAX_INPUT_BYTES:
        return {"error": f"input too large: {size} bytes (limit {C.MAX_INPUT_BYTES})"}

    kind = C.media_kind(src)
    if kind == "unknown":
        return {"error": f"unsupported media type: {src.suffix}"}

    job = C.OUTBOX / f"{int(started)}_{src.stem}"
    work = C.WORK / f"{int(started)}_{src.stem}"
    job.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    result: dict = {
        "input": str(src),
        "kind": kind,
        "size_bytes": size,
        "output_dir": str(job),
        "started_at": started,
    }

    probe = C.summarize_probe(C.ffprobe_json(src))
    result["probe"] = probe

    if kind == "video":
        norm = job / "normalized.mp4"
        ok, err = C.normalize_video(src, norm)
        result["normalized_video"] = {"ok": ok, "path": str(norm), "error": err or None}
        thumb = job / "thumbnail.jpg"
        ok, err = C.thumbnail(src, thumb)
        result["thumbnail"] = {"ok": ok, "path": str(thumb), "error": err or None}

    if kind in ("video", "audio") and do_stt:
        wav = work / "audio16k.wav"
        ok, err = C.extract_audio(src, wav)
        if ok:
            tr = C.transcribe(wav)
            result["transcript"] = tr
            if "error" not in tr and tr.get("text"):
                (job / "transcript.txt").write_text(tr["text"], encoding="utf-8")
                segs = tr.get("segments") or []
                if segs:
                    (job / "transcript.srt").write_text(C.segments_to_srt(segs), encoding="utf-8")
                    (job / "transcript.vtt").write_text(C.segments_to_vtt(segs), encoding="utf-8")
        else:
            result["transcript"] = {"error": f"audio extraction failed: {err}"}

    if kind == "image" and do_ocr:
        ocr = C.ocr_image(src)
        result["ocr"] = ocr
        if "error" not in ocr and ocr.get("text"):
            (job / "ocr.txt").write_text(ocr["text"], encoding="utf-8")

    source_text = ""
    if kind in ("video", "audio"):
        source_text = (result.get("transcript") or {}).get("text", "")
    elif kind == "image":
        source_text = (result.get("ocr") or {}).get("text", "")
    result["extracted_text_chars"] = len(source_text)

    if do_translate and source_text.strip():
        tr = C.translate_text(source_text, target=target)
        result["translation"] = tr
        if "error" not in tr:
            (job / f"translation.{target}.txt").write_text(tr["text"], encoding="utf-8")

    (job / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["elapsed_s"] = round(time.time() - started, 2)
    return result


def cmd_watch() -> dict:
    C.ensure_dirs()
    results = []
    for p in sorted(C.INBOX.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        r = process_file(p)
        results.append(r)
        dest = C.FAILURES if "error" in r else C.OUTBOX
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(p), str(dest / p.name))
        except Exception as exc:
            r["move_error"] = str(exc)
    return {"processed": len(results), "results": results}


def main() -> int:
    ap = argparse.ArgumentParser(prog="mp", description="media pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe")
    p_probe.add_argument("file")

    p_proc = sub.add_parser("process")
    p_proc.add_argument("file")
    p_proc.add_argument("--no-stt", action="store_true")
    p_proc.add_argument("--no-ocr", action="store_true")
    p_proc.add_argument("--translate", action="store_true")
    p_proc.add_argument("--target", default=C.TRANSLATE_TARGET)

    p_tr = sub.add_parser("translate")
    p_tr.add_argument("file")
    p_tr.add_argument("--target", default=C.TRANSLATE_TARGET)

    sub.add_parser("list")
    sub.add_parser("watch")
    sub.add_parser("selftest")

    a = ap.parse_args()

    if a.cmd == "probe":
        out(C.summarize_probe(C.ffprobe_json(Path(a.file))))
        return 0
    if a.cmd == "process":
        r = process_file(Path(a.file), do_stt=not a.no_stt, do_ocr=not a.no_ocr,
                         do_translate=a.translate, target=a.target)
        out(r)
        return 0 if "error" not in r else 1
    if a.cmd == "translate":
        r = process_file(Path(a.file), do_stt=True, do_ocr=True, do_translate=True, target=a.target)
        out(r.get("translation", r))
        return 0 if "error" not in r.get("translation", {}) else 1
    if a.cmd == "list":
        out({
            "inbox": [p.name for p in C.INBOX.iterdir()] if C.INBOX.is_dir() else [],
            "outbox": [p.name for p in C.OUTBOX.iterdir()] if C.OUTBOX.is_dir() else [],
            "failures": [p.name for p in C.FAILURES.iterdir()] if C.FAILURES.is_dir() else [],
        })
        return 0
    if a.cmd == "watch":
        out(cmd_watch())
        return 0
    if a.cmd == "selftest":
        out(selftest())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
