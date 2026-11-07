---
name: media-pipeline
description: Process video, audio, and images on the local CPU. Normalize video, transcribe speech, OCR images, and translate the extracted text when asked.
---

# Media Pipeline

CPU transcription and OCR for files on this machine. FFmpeg, faster-whisper (int8), RapidOCR. Whisper model defaults to `tiny`.

## Layout

Paths are relative to the media-pipeline checkout.

    bin/mp              probe, process, translate, list, watch, selftest, doctor
    bin/mp-mcp          stdio MCP server
    inbox/              files for `mp watch`
    work/               16 kHz wav and work/processed.json
    outbox/<epoch>_<stem>/
                        normalized.mp4, thumbnail.jpg, transcript.txt,
                        transcript.srt, transcript.vtt, ocr.txt,
                        translation.<lang>.txt, manifest.json
    failures/           inputs `mp watch` could not process

`install.sh` copies this skill to `$MP_SKILLS_DIR/media-pipeline` when that directory already exists. The default is `$HOME/.local/share/prime-agent/0.9.6/skills`.

## Engines

| Job | Engine | Notes |
|---|---|---|
| probe | ffprobe | format, duration, streams |
| video | FFmpeg | H.264 + AAC, yuv420p, faststart, CRF 28; thumbnail at 1s, width 1280 |
| audio for the model | FFmpeg | 16 kHz mono PCM, kept under `work/` |
| speech to text | faster-whisper | CPU, `MP_WHISPER_COMPUTE` default `int8`, model `MP_WHISPER_MODEL` default `tiny` |
| image text | RapidOCR | PP-OCRv4 through ONNX Runtime, written to `ocr.txt` |
| translate | Pollinations, Gemini, or LiteLLM | only when requested; see `references/engines.md` |

## Commands

    bin/mp selftest
    bin/mp probe FILE
    bin/mp process FILE
    bin/mp process FILE --translate --target tr
    bin/mp translate FILE --target tr
    bin/mp watch
    bin/mp doctor

`process` leaves the input file where it is. `watch` moves it to `outbox/` or `failures/`. A file whose sha256 is already in `work/processed.json` stays in `inbox/`.

`doctor` exits 1 when a critical check fails. One of those checks is `$HOME/bin/prime-tg`, which this repository does not contain.

## MCP tools

`media_engines`, `media_probe`, `media_process`, `media_translate`, `media_batch`.

`media_process` accepts `path`, `transcribe`, `ocr`, `translate`, and `target_language`. `media_batch` processes `inbox/` with transcription and OCR on and translation off.

## Rules

- Leave `MP_LITELLM_KEY` and `MP_GEMINI_KEY` in the environment. Do not print them.
- `tiny` is the default. Set `MP_WHISPER_MODEL` to `base` or `small` when the recording needs it. `medium` and `large-v3` are large downloads.
- An empty transcript with `probe.duration_s` of 0 means the audio was not decoded.
- If translation returns an error, the text is not translated. Say so.
- Repository text stays in English.

## Reference

- `references/engines.md` for provider order, OCR limits, and the ARM host notes
- `references/triggers.md` for the inbox path unit and `mp watch`
- `references/recipes.md` for the venv, ffmpeg, and OCR commands
