# Spoofer

Standalone image + video spoofer. One password, no API keys, no other tools.

- **Images**: spoofed entirely in the browser (pixel-level randomization + fresh
  iPhone EXIF via piexifjs). Nothing is uploaded except HEIC files, which the
  server converts to JPEG in memory.
- **Videos**: uploaded to the server and re-encoded with ffmpeg using tiny random
  visual/audio transforms, a few trimmed frames, and fake Apple/iPhone metadata.
  Output is a spoofed GIF by default or an MP4.

## Files

| File | Purpose |
|---|---|
| `server.py` | Flask server: login, HEIC decode, video spoofing, PWA bits |
| `index.html` | The spoofer UI (all client-side image spoofing lives here) |
| `requirements.txt` | Python packages |
| `.env` | Config with a random starter password — change it |
| `.env.example` | Config template |
| `spoofer.service` | Example systemd unit |

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe` on PATH (only for video spoofing)
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - macOS: `brew install ffmpeg`

## Run

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# .env is included with a random starter password — open it and change APP_PASSWORD
./venv/bin/python server.py
```

Open `http://<host>:9102`, enter the password, drop photos or videos.

## Config (.env)

| Key | Default | Meaning |
|---|---|---|
| `APP_PASSWORD` | (empty = nobody can log in) | Login password |
| `PORT` | `9102` | Listen port |
| `BIND` | `0.0.0.0` | Use `127.0.0.1` behind a reverse proxy |
| `GIF_FPS` | `15` | Frame rate of GIF output |
| `GIF_WIDTH` | `480` | Width of GIF output |

## Behind HTTPS (optional)

Put Caddy or nginx in front and set `BIND=127.0.0.1`. The server marks the login
cookie `Secure` automatically when it sees `X-Forwarded-Proto: https`.

## Endpoints

- `GET /` — the app (redirects to `/login` until unlocked)
- `POST /login` — form field `pw`
- `GET /logout`
- `POST /api/heic2jpeg` — multipart `file`
- `POST /api/spoofvideo?mode=hf|boosted&mirror=0|1&out=gif|mp4` — multipart `video`
- `GET /healthz`
