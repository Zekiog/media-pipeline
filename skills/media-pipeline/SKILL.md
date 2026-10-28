---
name: media-pipeline
description: Process video, audio and images locally - normalize formats, transcribe speech, OCR images and translate the extracted text. Use when media arrives in the inbox, when a Telegram photo/document/video needs text, or when any media file has to be inspected.
---

# Media Pipeline

One local stack for every media type this host receives. CPU only, no GPU, no
torch, ARM-native. Delivery pattern follows Z-Rea: one CLI, one MCP server,
one skill, installed into the agent's own subdirectory and triggered when
media lands.

## Repository

    /home/opc/work/prime/media-pipeline

    bin/mp              CLI: probe | process | translate | list | watch | selftest | doctor
    bin/mp-mcp          MCP server (stdio JSON-RPC), tools:
                         media_engines, media_probe, media_process,
                         media_translate, media_batch
    inbox/              drop zone - anything here is processed automatically
    work/               intermediates (extracted audio)
    outbox/<job>/       results: normalized.mp4, thumbnail.jpg, transcript.txt,
                        transcript.srt, transcript.vtt, ocr.txt,
                        translation.<lang>.txt, manifest.json
    failures/           inputs that could not be processed

## Engines

| Job | Engine | Notes |
|---|---|---|
| format probe | ffprobe | format, duration, streams |
| video normalize | FFmpeg | h264 + aac, yuv420p, faststart |
| thumbnail | FFmpeg | 1 frame at 1s, scaled to 1280 wide |
| audio extract | FFmpeg | 16 kHz mono PCM for the model |
| speech to text | faster-whisper (CTranslate2) | CPU int8, model `tiny` by default |
| image OCR | RapidOCR (PP-OCRv4, ONNX) | no torch, ~15 MB models |
| translate | LiteLLM proxy | needs `MP_LITELLM_KEY`; otherwise the agent translates |

## Standard flow

1. Drop the file into `inbox/`. The `mp-inbox.path` systemd unit fires and
   runs `mp watch`, which processes every file and moves it to `outbox/` or
   `failures/`.
2. Read `outbox/<job>/manifest.json` for the machine readable result.
3. Send `outbox/<job>/transcript.txt` or `ocr.txt` through translation.

## Commands

    # what works on this host
    /home/opc/work/prime/media-pipeline/bin/mp selftest

    # metadata only
    /home/opc/work/prime/media-pipeline/bin/mp probe FILE

    # full pipeline
    /home/opc/work/prime/media-pipeline/bin/mp process FILE

    # process everything waiting in the inbox
    /home/opc/work/prime/media-pipeline/bin/mp watch

    # extract text then translate it
    /home/opc/work/prime/media-pipeline/bin/mp translate FILE --target tr

    # check the whole installation: engines, skill files, MCP registration,
    # the inbox trigger and the provider chain. Exit code 1 on a critical fault.
    /home/opc/work/prime/media-pipeline/bin/mp doctor

## Rules

- Never print a secret. `MP_LITELLM_KEY` and the Telegram token stay in env
  files, never in output, never in the repository.
- Transcription quality follows the model. `tiny` is fast and rough; use
  `base` for normal speech, `small` only when the file is short and the
  machine is idle.
- An empty transcript is not proof that the media has no speech. Check
  `probe.duration_s` first; a zero duration means the audio track was never
  decoded, and that is a bug in the pipeline, not a silent video.
- When translation is unavailable, translate the extracted text yourself and
  write it next to the transcript. Do not report the text as translated when
  it was not.
- Keep the repository in English. Report to the owner in plain Turkish text.

## Reference

- `references/engines.md` - engine install notes, ARM constraints, model sizes
- `references/triggers.md` - inbox watch, systemd path unit, Telegram bridge
