FROM python:3.12-slim

# ffmpeg + ffprobe are needed for the video spoofer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render (and most hosts) inject the port to listen on via $PORT.
ENV PORT=9102 BIND=0.0.0.0
EXPOSE 9102

# gunicorn = production server. Long timeout so video re-encodes don't get killed.
CMD gunicorn --workers 2 --threads 4 --timeout 600 --bind 0.0.0.0:$PORT server:app
