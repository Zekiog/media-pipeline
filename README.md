# media-pipeline

Local CPU-only processing for video, audio, and images. FFmpeg reads containers and writes video and audio. faster-whisper transcribes speech on CPU with int8 weights. RapidOCR reads printed text in images through ONNX Runtime. Translation of the extracted text is optional.

Whisper defaults are model `tiny` and compute type `int8` (`MP_WHISPER_MODEL`, `MP_WHISPER_COMPUTE`). The default input limit is 512 MB (`MP_MAX_INPUT_BYTES`).

## What a job writes

| Input | Engine | Files |
|---|---|---|
| video: `.mp4` `.mov` `.mkv` `.webm` `.avi` `.m4v` `.mpg` `.mpeg` | FFmpeg (`libx264`, AAC 128k, yuv420p, faststart, CRF 28) | `normalized.mp4`, `thumbnail.jpg` (one frame at 1s, width 1280) |
| video, or audio: `.wav` `.mp3` `.m4a` `.aac` `.ogg` `.oga` `.flac` `.opus` `.wma` | FFmpeg, then faster-whisper | 16 kHz mono PCM under `work/`; `transcript.txt`, `transcript.srt`, `transcript.vtt` when there is speech |
| image: `.png` `.jpg` `.jpeg` `.webp` `.gif` `.bmp` `.tiff` `.tif` `.heic` | RapidOCR (PP-OCRv4) | `ocr.txt` |
| extracted text, when translation is requested | Pollinations, then Gemini, then a LiteLLM proxy | `translation.<lang>.txt` |

Each job is a directory `outbox/<epoch>_<stem>/` and includes `manifest.json`. The 16 kHz wav stays in `work/`.

`mp process` transcribes speech and runs OCR. It translates only with `--translate`. `mp translate` always translates. The default target language is `tr` (`MP_TRANSLATE_TARGET`).

`MP_LLM_PROVIDER` defaults to `auto`: Pollinations with no key (`MP_POLLINATIONS_MODEL`, default `openai`), then Gemini when `MP_GEMINI_KEY` is set (`MP_GEMINI_MODEL`, default `gemini-2.0-flash`), then LiteLLM when `MP_LITELLM_KEY` and `MP_LITELLM_MODEL` are set (`MP_LITELLM_BASE`, default `http://127.0.0.1:4000/v1`). `MP_LLM_PROVIDER=none` leaves translation unconfigured.

`requirements.txt` lists faster-whisper, pillow, onnxruntime, and rapidocr-onnxruntime. `mp selftest` imports Pillow. Image jobs write OCR text. FFmpeg and ffprobe have to be on `PATH`. `bin/mp` runs `.venv/bin/python` when it exists, then `python3.11`, then `python3`.

## CLI

```
bin/mp probe FILE
bin/mp process FILE [--no-stt] [--no-ocr] [--translate] [--target LANG]
bin/mp translate FILE [--target LANG]
bin/mp list
bin/mp watch
bin/mp selftest
bin/mp doctor
```

`probe` prints ffprobe metadata. `process` handles one file and leaves it in place. `list` prints names in `inbox/`, `outbox/`, and `failures/`. `watch` handles each non-hidden file in `inbox/` in filename order, then moves it to `outbox/` or, on error, to `failures/`. A sha256 already stored in `work/processed.json` stays in `inbox/`. `selftest` prints whether ffmpeg, ffprobe, faster-whisper, RapidOCR, and Pillow are usable, and exits 0. `doctor` adds the skill files, an MCP handshake, the user systemd units, `~/.prime/agent/settings.json`, and `$HOME/bin/prime-tg`. A failed critical check exits 1. `prime-tg` is not in this repository; a missing binary fails `doctor`.

## MCP server

`bin/mp-mcp` is a stdio JSON-RPC MCP server. Tools:

- `media_engines`
- `media_probe` (`path`)
- `media_process` (`path`, `transcribe`, `ocr`, `translate`, `target_language`)
- `media_translate` (`path`, `target_language`)
- `media_batch` (everything in `inbox/`, transcription and OCR on, translation off)

`mcp-server.json` in the repo points at `/home/opc/work/prime/media-pipeline`. `install.sh` rewrites `command` and `cwd` to this checkout.

## Skill

`skills/media-pipeline/SKILL.md` and the notes in `skills/media-pipeline/references/` describe the same commands.

## Install

`./install.sh`:

- creates `inbox/`, `work/`, `outbox/`, `failures/`, and `logs/`
- copies the skill into `$MP_SKILLS_DIR/media-pipeline` when that directory already exists (default `$HOME/.local/share/prime-agent/0.9.6/skills`)
- rewrites `mcp-server.json`
- when a `prime-agent` binary is on `PATH` or at `$HOME/.local/share/prime-agent/*/prime-agent`, runs `prime-agent mcp add media-pipeline --force --cwd <repo> -- <repo>/bin/mp-mcp`
- writes `$HOME/.config/systemd/user/mp-inbox.path` and `mp-inbox.service`, then enables the path unit if `systemctl` is available
- checks the MCP handshake and runs `bin/mp selftest`

The script overwrites the manifest, the copied skill files, and the user units. It can be run again.

## Inbox watch

The user path unit watches `inbox/` (`PathChanged`) and starts a oneshot service that runs `bin/mp watch` with a 1800s start timeout. `systemd/mp-inbox.path` and `systemd/mp-inbox.service` are the same units with `/home/opc/work/prime/media-pipeline` baked in. The service file written by `install.sh` also sets `After=default.target`.

`work/`, `outbox/`, `failures/`, `logs/`, and inbox contents other than `.gitkeep` are gitignored.

## License

MIT.
