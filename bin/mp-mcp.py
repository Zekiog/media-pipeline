#!/usr/bin/env python3
"""mp-mcp - MCP server exposing the media pipeline over stdio JSON-RPC.

Packaging follows the Z-Rea delivery pattern: one MCP server, one CLI, one
agent skill. Tools return JSON in text content plus structured content, and
every result carries the evidence and the limitations behind it.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mp_core as C  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "media-pipeline"
SERVER_VERSION = "0.1.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "media_engines",
        "description": "Report which media engines are available on this host and whether the pipeline is ready.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "media_probe",
        "description": "Probe a media file and return format, duration, size and stream metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the media file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "media_process",
        "description": "Process a media file: normalize video, extract audio, transcribe speech, OCR images. Writes results to the outbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the media file"},
                "transcribe": {"type": "boolean", "description": "Run speech to text (default true)"},
                "ocr": {"type": "boolean", "description": "Run OCR on images (default true)"},
                "translate": {"type": "boolean", "description": "Translate the extracted text (default false)"},
                "target_language": {"type": "string", "description": "Translation target, default tr"},
                "model": {"type": "string", "description": "Whisper model size: tiny, base, small. Default tiny"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "media_translate",
        "description": "Extract text from a media file, then translate it into the target language.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "target_language": {"type": "string", "description": "Default tr"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "media_batch",
        "description": "Process every file in the inbox and return one summary per input.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _limits() -> list[str]:
    lim = [
        "Speech to text runs on CPU with an int8 quantized Whisper model; accuracy is model dependent.",
        "OCR uses PP-OCRv4 through ONNX Runtime; handwritten or low contrast text may be missed.",
    ]
    if not C.LITELLM_KEY:
        lim.append("Translation needs MP_LITELLM_KEY; without it the agent must translate the extracted text itself.")
    return lim


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("evidence", {"server": SERVER_NAME, "version": SERVER_VERSION})
    payload.setdefault("limitations", _limits())
    return payload


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "media_engines":
        import mp_cli  # noqa: F401  (import for path side effects only)
        return _result({"engines": _engines()})

    if name == "media_probe":
        p = Path(args["path"]).expanduser().resolve()
        if not p.is_file():
            return _result({"error": f"not a file: {p}", "recovery": "check the path"})
        return _result({"path": str(p), "probe": C.summarize_probe(C.ffprobe_json(p))})

    if name == "media_process":
        import mp_cli
        p = Path(args["path"]).expanduser().resolve()
        if not p.is_file():
            return _result({"error": f"not a file: {p}", "recovery": "check the path"})
        r = mp_cli.process_file(
            p,
            do_stt=bool(args.get("transcribe", True)),
            do_ocr=bool(args.get("ocr", True)),
            do_translate=bool(args.get("translate", False)),
            target=args.get("target_language", C.TRANSLATE_TARGET),
        )
        return _result(r)

    if name == "media_translate":
        import mp_cli
        p = Path(args["path"]).expanduser().resolve()
        if not p.is_file():
            return _result({"error": f"not a file: {p}", "recovery": "check the path"})
        r = mp_cli.process_file(p, do_stt=True, do_ocr=True, do_translate=True,
                                target=args.get("target_language", C.TRANSLATE_TARGET))
        return _result({"path": str(p), "extracted_text_chars": r.get("extracted_text_chars"),
                        "translation": r.get("translation")})

    if name == "media_batch":
        import mp_cli
        C.ensure_dirs()
        results = []
        for p in sorted(C.INBOX.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                results.append(mp_cli.process_file(p))
        return _result({"count": len(results), "results": results})

    return _result({"error": f"unknown tool: {name}"})


def _engines() -> dict[str, Any]:
    eng: dict[str, Any] = {"ffmpeg": C.have("ffmpeg"), "ffprobe": C.have("ffprobe"),
                           "whisper_model": C.WHISPER_MODEL,
                           "llm_key_configured": bool(C.LITELLM_KEY)}
    for mod, key in (("faster_whisper", "faster_whisper"), ("rapidocr_onnxruntime", "rapidocr"), ("PIL", "pillow")):
        try:
            __import__(mod)
            eng[key] = True
        except Exception as exc:
            eng[key] = f"missing: {exc}"
    return eng


def send(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def respond(req_id: Any, result: Any) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def fail(req_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    send({"jsonrpc": "2.0", "id": req_id, "error": err})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(None, -32700, f"parse error: {exc}")
            continue
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            respond(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "tools/list":
            respond(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            tname = params.get("name")
            targs = params.get("arguments") or {}
            try:
                payload = call_tool(tname, targs)
            except Exception as exc:
                payload = _result({"error": f"{type(exc).__name__}: {exc}",
                                   "trace": traceback.format_exc()[-800:]})
            respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                "structuredContent": payload,
                "isError": "error" in payload,
            })
        elif method == "ping":
            respond(req_id, {})
        else:
            fail(req_id, -32601, f"method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
