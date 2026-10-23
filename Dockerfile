FROM python:3.11-slim

# Set environment
ENV PYTHONUNBUFFERED=1 \
    SDL_AUDIODRIVER=dummy \
    PORT=8001 \
    WS_PORT=8766 \
    HOST=0.0.0.0

WORKDIR /app

# Install runtime audio libraries so pygame can initialize safely
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose HTTP dashboard and WebSocket stream ports
EXPOSE 8001 8766

# Start server
CMD ["python", "server.py"]
