FROM python:3.11-slim

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SDL_AUDIODRIVER=dummy \
    PORT=8001 \
    WS_PORT=8766 \
    HOST=0.0.0.0 \
    BGM=false \
    DATA_DIR=/app/data \
    TRUST_PROXY=true \
    COOKIE_SECURE=auto \
    TZ=Asia/Jakarta

WORKDIR /app

# Runtime audio library supaya pygame aman saat diinisialisasi (BGM tetap off di VPS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 \
    tzdata \
    passwd \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy hanya file yang dibutuhkan runtime (source code tidak ikut disajikan)
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser:appuser /app/data
COPY --chown=appuser:appuser server.py home.html login.html engine.html snake.html ./
COPY --chown=appuser:appuser mp3 ./mp3
COPY --chown=appuser:appuser sfx ./sfx

# Jalankan sebagai non-root
USER appuser

# Expose HTTP dashboard dan WebSocket relay
EXPOSE 8001 8766

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=3)"

# SIGINT membuat server menutup dengan rapi (ada handler KeyboardInterrupt)
STOPSIGNAL SIGINT

CMD ["python", "-u", "server.py"]
