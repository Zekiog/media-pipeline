# media-pipeline

Open-source media processing stack for Prime Agent, packaged after the
[Z-Rea](https://github.com/Zekiog/Z-Rea) delivery pattern: one CLI, one MCP
server, one agent skill, installed into the agent's own subdirectory and
triggered automatically when media arrives.

## What it processes

| Input | Engine | Output |
|---|---|---|
| video (mp4/mov/mkv/webm) | FFmpeg | normalized mp4 (h264+aac), audio wav, keyframe thumbnails |
| audio (wav/mp3/m4a/ogg/flac) | FFmpeg + faster-whisper | transcript (txt/srt/vtt), 16 kHz mono wav |
| image (png/jpg/webp/gif) | Pillow + RapidOCR (PP-OCRv4) | OCR text, resized/normalized png, description via LiteLLM vision |
| any media | LiteLLM proxy | translation of extracted text (EN/TR default) |

No GPU. No torch. ARM-native (aarch64). CPU-only, int8 quantized Whisper.

## Layout

    media-pipeline/
      bin/mp              # CLI (process / probe / watch / translate)
      bin/mp-mcp          # MCP server (stdio JSON-RPC)
      skills/media-pipeline/SKILL.md
      skills/media-pipeline/references/*.md
      inbox/              # drop zone: anything here is processed
      work/               # intermediate
      outbox/             # results
      failures/           # inputs that failed
      logs/

## Install into the agent's own subdirectory

    ./install.sh

Writes the skill to the agent skill directory, registers the MCP server in the
agent config, and creates the inbox/outbox layout under this repo.

## Trigger

Two triggers, both agent-scope:

1. Local inbox watch - systemd path unit `mp-inbox.path` fires when a file
   lands in `inbox/`.
2. Telegram - `prime-tg` saves an incoming photo/document/video into the
   inbox and the same path unit picks it up.

## License

MIT. Public repo content is English only.
