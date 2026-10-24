# Engines, ARM constraints, model sizes

## Why no torch

This host is 2 vCPU ARM Neoverse-N1 with 11 GB RAM and a root disk that runs
around 85 percent full. A torch install costs 2-3 GB and buys nothing here:
faster-whisper runs on CTranslate2, and RapidOCR runs on ONNX Runtime. Both
have aarch64 wheels, both work on CPU, and the whole virtualenv stays near
500 MB including the models.

## Verified versions on this host

    faster-whisper        1.2.1
    ctranslate2          4.8.3
    onnxruntime          1.31.0
    rapidocr-onnxruntime 1.4.4 (works with opencv-python-headless)
    pillow               13.0.0
    numpy                2.4.6
    ffmpeg               /usr/local/bin/ffmpeg (no drawtext filter, no freetype)

## Trap: libGL

`opencv-python` pulls libGL.so.1 and fails to import on a headless host.
Install `opencv-python-headless` instead and force-reinstall it over the
regular package. rapidocr imports cv2 at module load, so the whole OCR path
dies with an ImportError that looks unrelated to OCR.

## Whisper model sizes

| Model | Download | Speed on 2 vCPU ARM | Use for |
|---|---|---|---|
| tiny | 75 MB | ~10x realtime | quick scan, short clips |
| base | 145 MB | ~5x realtime | normal speech |
| small | 480 MB | ~2x realtime | clear speech, short files |
| medium | 1.5 GB | slower | only when the disk allows |
| large-v3 | 3 GB | slow | not for this host |

`MP_WHISPER_MODEL` selects the default. `MP_WHISPER_COMPUTE=int8` is what
keeps it fast.

## OCR limits

PP-OCRv4 detects and reads printed text well (measured mean confidence 0.97
on a clean synthetic image). It struggles with handwriting, very low contrast,
rotated text and text over busy backgrounds. For those cases use a vision
model (see the provider table below) and treat the result as a description
rather than an exact transcription.

## FFmpeg limits on this host

The system ffmpeg build has no `drawtext` filter, so text cannot be burned
into a frame from the command line. Generate text images with Pillow instead.

## Translation and vision providers

The provider chain is selected with `MP_LLM_PROVIDER`:

| Value | Behaviour |
|---|---|
| `auto` (default) | Pollinations (keyless), then Gemini, then LiteLLM |
| `pollinations` | keyless Pollinations only |
| `gemini` | Google AI Studio only |
| `litellm` | LiteLLM proxy only |
| `none` | no provider, the agent translates instead |

| Provider | Key needed | Vision | Notes |
|---|---|---|---|
| Pollinations | no | no | classic API, model `openai-fast` (gpt-oss-20b), text only |
| Google AI Studio | yes | yes | free tier, `gemini-2.0-flash`, `MP_GEMINI_KEY` |
| LiteLLM proxy | yes | yes | `MP_LITELLM_KEY` and `MP_LITELLM_MODEL` |

Pollinations needs no account and no key, so translation works out of the
box. Its classic endpoint is `https://text.pollinations.ai/openai` and it
answers a POST with an OpenAI compatible body. The free tier allows one reply
every 30 seconds per IP, so a second call inside that window returns HTTP 402
with `FREE_LIMIT` and a `Retry after Ns` hint. The client waits for that
window once and retries; a second limit is reported as an error instead of
looping.

The classic Pollinations model list is text only (`vision: false`), so image
descriptions need a key: set `MP_GEMINI_KEY` for the Google AI Studio free
tier, or point `MP_LITELLM_BASE` and `MP_LITELLM_KEY` at the LiteLLM proxy.
Printed text in images does not need a vision model at all: RapidOCR already
reads it on CPU.
