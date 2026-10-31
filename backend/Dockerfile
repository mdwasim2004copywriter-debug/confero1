FROM python:3.11-slim

# faster-whisper/librosa/soundfile need ffmpeg at the OS level. build-essential
# (gcc etc.) is needed because webrtcvad-wheels has no prebuilt Linux wheel for
# this Python version and compiles a small C extension at install time.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# --extra-index-url is needed for the torch==...+cpu build pinned in
# requirements.txt (see the comment there), that build only exists on
# PyTorch's own index, not plain PyPI.
RUN pip install --no-cache-dir -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

COPY . .

# Railway/Render/Fly all inject $PORT at runtime; default to 8000 for
# plain `docker run` / local testing.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
