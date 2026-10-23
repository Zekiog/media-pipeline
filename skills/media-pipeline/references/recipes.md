# Exact recipes

## Virtualenv without pip (uv)

    /usr/bin/python3.11 -m venv .venv
    uv pip install --python .venv/bin/python faster-whisper pillow onnxruntime rapidocr-onnxruntime
    uv pip install --python .venv/bin/python --force-reinstall opencv-python-headless

Import check, then a real file. The import check is not the proof.

    .venv/bin/python -c "import faster_whisper, ctranslate2, onnxruntime, rapidocr_onnxruntime, PIL; print('ok')"

## Speech to text

    from faster_whisper import WhisperModel
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, info = model.transcribe("audio16k.wav", vad_filter=True)

Audio must be 16 kHz mono PCM:

    ffmpeg -y -i input.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le audio16k.wav

## Image OCR

    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()
    result, _ = engine("image.png")

## Directory trigger without a package

    [Path]
    PathChanged=/path/to/inbox
    Unit=worker.service

    systemctl --user daemon-reload
    systemctl --user enable --now worker.path

## Telegram file download

    getFile with the file_id, then fetch
    https://api.telegram.org/file/bot<token>/<file_path>

Keep the token out of logs and out of error text.
