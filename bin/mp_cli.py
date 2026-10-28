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
import os
import shutil
import subprocess
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


def _run(cmd: list, timeout: int = 30) -> tuple:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip()
    except Exception as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def doctor() -> dict:
    """Check the whole installation: engines, skill, MCP registration, trigger."""
    import shutil as _shutil
    import tempfile

    checks: list[dict] = []

    def ekle(ad: str, ok: bool, detay: str, kritik: bool = True) -> None:
        checks.append({"ad": ad, "ok": bool(ok), "detay": detay, "kritik": kritik})

    # engines
    for arac in ("ffmpeg", "ffprobe"):
        ekle(f"engine:{arac}", C.have(arac), "bulundu" if C.have(arac) else "PATH'te yok")
    for mod, ad in (("faster_whisper", "engine:faster-whisper"),
                    ("rapidocr_onnxruntime", "engine:rapidocr"),
                    ("PIL", "engine:pillow")):
        try:
            __import__(mod)
            ekle(ad, True, "import ok")
        except Exception as exc:
            ekle(ad, False, f"import hatasi: {type(exc).__name__}: {exc}")

    # venv butunlugu
    venv_py = str(C.ROOT / ".venv" / "bin" / "python")
    if os.path.exists(venv_py):
        rc, out = _run([venv_py, "-m", "pip", "check"], timeout=120)
        ekle("venv:pip-check", rc == 0, out[:300] if out else "temiz")
    else:
        ekle("venv:pip-check", False, f"venv yok: {venv_py}")

    # skill dosyalari
    skill_dirs = [Path.home() / ".local/share/prime-agent/0.9.6/skills/media-pipeline",
                  C.ROOT / "skills/media-pipeline"]
    bulunan = None
    for sd in skill_dirs:
        if (sd / "SKILL.md").is_file():
            bulunan = sd
            break
    ekle("skill:SKILL.md", bulunan is not None,
         str(bulunan) if bulunan else f"bulunamadi: {skill_dirs[0]}")
    if bulunan:
        for ref in ("engines.md", "triggers.md", "recipes.md"):
            ekle(f"skill:references/{ref}", (bulunan / "references" / ref).is_file(),
                 "var" if (bulunan / "references" / ref).is_file() else "eksik")

    # MCP kaydi (settings.json) + handshake
    ayar = Path.home() / ".prime/agent/settings.json"
    kayitli = False
    detay = "settings.json okunamadi"
    if ayar.is_file():
        try:
            veri = json.loads(ayar.read_text())
            giris = (veri.get("mcpServers") or {}).get("media-pipeline") or {}
            kayitli = bool(giris.get("command"))
            detay = giris.get("command", "mcpServers.media-pipeline yok")
        except Exception as exc:
            detay = f"settings.json hatasi: {type(exc).__name__}"
    ekle("mcp:kayit", kayitli, detay)

    server = C.ROOT / "bin/mp-mcp"
    if server.is_file():
        istek = ("{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":"
                 "{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},"
                 "\"clientInfo\":{\"name\":\"doctor\",\"version\":\"1\"}}}\n"
                 "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}\n")
        try:
            p = subprocess.run([str(server)], input=istek, capture_output=True, text=True, timeout=60)
            elde = p.stdout or ""
            ekle("mcp:handshake", "media_engines" in elde,
                 "tools/list cevap verdi" if "media_engines" in elde else "cevap yok")
        except Exception as exc:
            ekle("mcp:handshake", False, f"{type(exc).__name__}: {exc}")
    else:
        ekle("mcp:handshake", False, f"sunucu yok: {server}")

    # tetikleyici
    for unit, tip in (("mp-inbox.path", "path"), ("mp-inbox.service", "service")):
        if _shutil.which("systemctl"):
            rc, out = _run(["systemctl", "--user", "is-enabled", unit], timeout=20)
            ekle(f"systemd:{unit}:enabled", rc == 0, out or "bilinmiyor")
            rc2, out2 = _run(["systemctl", "--user", "is-active", unit], timeout=20)
            ekle(f"systemd:{unit}:active", rc2 == 0, out2 or "bilinmiyor", kritik=False)

    # prime-tg entegrasyonu
    ptg = Path.home() / "bin/prime-tg"
    ekle("prime-tg:binary", ptg.is_file(), str(ptg) if ptg.is_file() else "bulunamadi")
    mp_bin = C.ROOT / "bin/mp"
    ekle("prime-tg:mp-erisilebilir", mp_bin.is_file() and os.access(mp_bin, os.X_OK),
         str(mp_bin) if mp_bin.is_file() else "bulunamadi")

    # saglayici zinciri
    saglayicilar = C._saglayicilar()
    ekle("llm:saglayici", bool(saglayicilar), ", ".join(saglayicilar) or "yok (none)")

    # disk
    try:
        st = os.statvfs(str(C.ROOT))
        bos_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
        ekle("disk:bos", bos_gb > 1.0, f"{bos_gb:.1f} GB bos", kritik=False)
    except Exception:
        pass

    kritik_hata = [c for c in checks if c["kritik"] and not c["ok"]]
    return {
        "ok": not kritik_hata,
        "kritik_hata_sayisi": len(kritik_hata),
        "checks": checks,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


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

    # Ayni icerik daha once islendiyse tekrar islemeyelim: Telegram akisinda
    # prime-tg ve mp watch ayni dosyayi gorur.
    digest = C.dosya_hash(src)
    oncekiler = C.seen_yukle()
    if digest in oncekiler:
        return {"input": str(src), "skipped": "duplicate",
                "previous_output": oncekiler[digest].get("output_dir"),
                "seen_at": oncekiler[digest].get("seen_at")}

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
    kayit = C.seen_yukle()
    kayit[digest] = {"output_dir": str(job), "seen_at": int(started),
                     "input_name": src.name}
    C.seen_kaydet(kayit)
    return result


def cmd_watch() -> dict:
    C.ensure_dirs()
    results = []
    for p in sorted(C.INBOX.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        r = process_file(p)
        results.append(r)
        if r.get("skipped") == "duplicate":
            # Zaten islenmis: dosyayi inbox'ta birakmadan tasima, eski ciktiyi goster.
            results[-1] = r
            continue
        dest = C.FAILURES if "error" in r else C.OUTBOX
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(p), str(dest / p.name))
        except Exception as exc:
            r["move_error"] = str(exc)
    islenen = [r for r in results if not r.get("skipped")]
    return {"processed": len(islenen), "skipped": len(results) - len(islenen),
            "results": results}


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

    p_doc = sub.add_parser("doctor", help="check the whole installation")
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
    if a.cmd == "doctor":
        d = doctor()
        out(d)
        return 0 if d["ok"] else 1
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
