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
model through the LiteLLM proxy and treat the result as a description rather
than an exact transcription.

## FFmpeg limits on this host

The system ffmpeg build has no `drawtext` filter, so text cannot be burned
into a frame from the command line. Generate text images with Pillow instead.
