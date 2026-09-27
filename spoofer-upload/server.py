#!/usr/bin/env python3
"""
Spoofer — standalone image + video spoofer.

What it does
  * Serves index.html behind a single password (set APP_PASSWORD in .env).
  * Image spoofing is 100% client-side (canvas + piexifjs inside index.html).
    The only thing the server does with a photo is decode HEIC (browsers can't),
    in memory, never written to disk.
  * Video spoofing runs here through ffmpeg: randomized near-invisible visual
    and audio transforms, timeline trim, fresh Apple/iPhone metadata.

Requirements
  * Python 3.10+ and the packages in requirements.txt
  * ffmpeg + ffprobe on PATH (only needed for video)

Config (.env, plain KEY=VALUE — copy .env.example)
  APP_PASSWORD  required, the login password
  PORT          default 9102
  BIND          default 0.0.0.0  (use 127.0.0.1 if a reverse proxy sits in front)
  GIF_FPS       default 15   GIF output frame rate
  GIF_WIDTH     default 480  GIF output width
"""
import hashlib
import io
import os
import random
import secrets
import subprocess
import tempfile
from datetime import datetime, timedelta

from flask import Flask, Response, abort, redirect, request, send_file

HERE = os.path.dirname(os.path.abspath(__file__))

_env_path = os.path.join(HERE, ".env")
if os.path.exists(_env_path):
    for _l in open(_env_path, encoding="utf-8").read().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, v = _l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
PORT = int(os.getenv("PORT", "9102"))
BIND = os.getenv("BIND", "0.0.0.0")
# Set SPOOFER_NO_AUTH=1 only for a localhost self-test; never in the unit file.
NO_AUTH = os.getenv("SPOOFER_NO_AUTH") == "1"
COOKIE = "spoofer_auth"
TOKEN = hashlib.sha256(("spoofer::" + APP_PASSWORD).encode()).hexdigest()

GIF_FPS = int(os.getenv("GIF_FPS", "15"))
GIF_WIDTH = int(os.getenv("GIF_WIDTH", "480"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


def authed():
    if NO_AUTH:
        return True
    return APP_PASSWORD != "" and secrets.compare_digest(
        request.cookies.get(COOKIE, ""), TOKEN)


LOGIN = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#08090b"><title>Spoofer</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;min-height:100svh;display:grid;place-items:center;background:#08090b;
 color:#eef0f3;padding:24px;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
form{width:100%;max-width:330px;text-align:center}
h1{font-size:20px;margin:0 0 22px;letter-spacing:-.02em}
input{width:100%;height:54px;font-size:16px;text-align:center;background:#111216;
 color:#eef0f3;border:1px solid #2a2d35;border-radius:14px;padding:0 14px}
input:focus{outline:none;border-color:#ff4500}
button{width:100%;height:54px;margin-top:12px;font-size:16px;font-weight:700;
 color:#fff;background:#ff4500;border:none;border-radius:14px}
.e{color:#ff6b6b;font-size:13px;margin-top:14px}
</style></head><body><form method="post" action="/login">
<h1>Spoofer</h1>
<input type="password" name="pw" placeholder="Password" autocomplete="current-password" autofocus>
<button type="submit">Unlock</button>__E__
</form></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = (request.form.get("pw") or "").strip()
        if APP_PASSWORD and secrets.compare_digest(pw, APP_PASSWORD):
            r = redirect("/")
            r.set_cookie(COOKIE, TOKEN, max_age=60 * 60 * 24 * 365, httponly=True,
                         samesite="Lax",
                         secure=request.headers.get("X-Forwarded-Proto") == "https")
            return r
        return Response(LOGIN.replace("__E__", '<div class="e">Wrong password</div>'),
                        mimetype="text/html", status=401)
    return Response(LOGIN.replace("__E__", ""), mimetype="text/html")


@app.route("/logout")
def logout():
    r = redirect("/login")
    r.delete_cookie(COOKIE)
    return r


@app.route("/")
def index():
    if not authed():
        return redirect("/login")
    r = send_file(os.path.join(HERE, "index.html"), mimetype="text/html")
    r.headers["Cache-Control"] = "no-store, must-revalidate"
    return r


# ----------------------------------------------------------------------------
# HEIC -> JPEG (iPhone photos; no browser decodes HEIC). Converted in memory.
# ----------------------------------------------------------------------------
@app.route("/api/heic2jpeg", methods=["POST"])
def heic2jpeg():
    if not authed():
        return Response("unauthorized", 401)
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
    f = request.files.get("file")
    if not f:
        return Response("missing file", 400)
    try:
        img = Image.open(f.stream).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return Response(buf.getvalue(), mimetype="image/jpeg")
    except Exception as e:
        return Response("heic decode failed: %s" % e, 500)


# ----------------------------------------------------------------------------
# Video spoofer — re-encodes an uploaded clip with randomized, near-invisible
# transforms so its hash/fingerprint changes (mirrors the image spoofer idea).
# ----------------------------------------------------------------------------
def _probe_dims(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            timeout=30,
        ).decode().strip().split("x")
        return int(out[0]), int(out[1])
    except Exception:
        return None, None


def _probe_fps(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate", "-of", "default=nk=1:nw=1", path],
            timeout=30,
        ).decode().strip()
        num, den = out.split("/")
        den = float(den)
        return float(num) / den if den else None
    except Exception:
        return None


def _has_audio(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            timeout=30,
        ).decode().strip()
        return bool(out)
    except Exception:
        return False


def _probe_audio_sr(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate", "-of", "default=nk=1:nw=1", path],
            timeout=30,
        ).decode().strip()
        return int(out)
    except Exception:
        return 44100


def _probe_duration(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", path],
            timeout=30,
        ).decode().strip()
        return float(out)
    except Exception:
        return None


def _even(n):
    n = int(round(n))
    return n - (n % 2)


APPLE_MODELS = ["iPhone 13", "iPhone 13 Pro", "iPhone 14", "iPhone 14 Pro",
                "iPhone 15", "iPhone 15 Pro", "iPhone 15 Pro Max", "iPhone 16 Pro"]
IOS_VERSIONS = ["16.6.1", "17.3.1", "17.4.1", "17.5.1", "17.6.1", "18.0.1", "18.1.1"]


@app.route("/api/spoofvideo", methods=["POST"])
def spoofvideo():
    if not authed():
        return Response('{"error": "unauthorized"}', status=401,
                        mimetype="application/json")
    f = request.files.get("video")
    if not f:
        abort(400, "no video uploaded")
    boosted = request.args.get("mode", "hf") == "boosted"
    mirror = request.args.get("mirror", "0") == "1"
    out_fmt = request.args.get("out", "gif").lower()  # "gif" (default) or "mp4"

    in_ext = os.path.splitext(f.filename or "")[1].lower() or ".mp4"
    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=in_ext)
    tmp_in.close()
    tmp_out = tmp_in.name + (".out.gif" if out_fmt == "gif" else ".out.mp4")
    f.save(tmp_in.name)

    try:
        w, h = _probe_dims(tmp_in.name)
        fps = _probe_fps(tmp_in.name)
        dur = _probe_duration(tmp_in.name)
        has_audio = _has_audio(tmp_in.name)
        sr = _probe_audio_sr(tmp_in.name) if has_audio else 44100

        # All transforms are VERY tiny — invisible/inaudible, but enough to break
        # the hash + visual + audio fingerprint. Boosted = slightly larger ranges.
        b = 1.6 if boosted else 1.0
        zoom = random.uniform(1.008, 1.018) if boosted else random.uniform(1.004, 1.010)
        speed = round(random.uniform(0.987, 1.013) if boosted else random.uniform(0.993, 1.007), 4)
        hue_deg = round(random.uniform(-3, 3) if boosted else random.uniform(-1.5, 1.5), 2)
        bright = round(random.uniform(-0.03, 0.03) * b, 3)
        contrast = round(random.uniform(0.98, 1.02) if not boosted else random.uniform(0.96, 1.04), 3)
        sat = round(random.uniform(0.97, 1.03) if not boosted else random.uniform(0.95, 1.05), 3)
        gamma = round(random.uniform(0.97, 1.03), 3)
        noise = random.randint(3, 6) if boosted else random.randint(1, 3)
        crf = random.randint(20, 24)
        fps_jitter = random.uniform(-0.004, 0.004)

        # Audio spoof params.
        pitch = round(random.uniform(0.99, 1.01), 4)        # tiny pitch shift
        treble_g = round(random.uniform(-1.2, 1.2), 2)       # subtle EQ
        bass_g = round(random.uniform(-1.2, 1.2), 2)
        tone_freq = random.randint(17000, 19500)             # near-inaudible tone
        tone_vol = round(random.uniform(0.002, 0.009), 4)

        # Trim a few frames off the start and end (shifts the whole timeline).
        spf = (1.0 / fps) if (fps and fps > 0) else (1.0 / 30.0)
        st = round(random.randint(2, 5) * spf, 3)
        et = round(random.randint(2, 5) * spf, 3)
        out_dur = round(dur - st - et, 3) if dur else None
        if out_dur is not None and out_dur < 0.3:
            st, et, out_dur = 0.0, 0.0, dur  # clip too short to trim

        # Shared visual spoof chain (no pixel-format suffix here).
        vbase = []
        if mirror:
            vbase.append("hflip")
        if w and h:
            sw, sh = _even(w * zoom), _even(h * zoom)
            ox, oy = _even((sw - w) / 2), _even((sh - h) / 2)
            vbase.append(f"scale={sw}:{sh}")
            vbase.append(f"crop={w}:{h}:{ox}:{oy}")
        vbase.append(f"eq=brightness={bright}:contrast={contrast}:saturation={sat}:gamma={gamma}")
        vbase.append(f"hue=h={hue_deg}")
        vbase.append(f"noise=alls={noise}:allf=t")
        vbase.append(f"setpts={round(1/speed, 5)}*PTS")
        vbase_str = ",".join(vbase)

        cmd = ["ffmpeg", "-y"]
        if st:
            cmd += ["-ss", f"{st}"]
        cmd += ["-i", tmp_in.name]

        if out_fmt == "gif":
            # Spoofed animated GIF (silent), palette for clean colours.
            vf_gif = (f"{vbase_str},fps={GIF_FPS},scale={GIF_WIDTH}:-2:flags=lanczos,"
                      "split[a][b];[a]palettegen=stats_mode=diff[p];"
                      "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
            cmd += ["-vf", vf_gif, "-loop", "0", "-an"]
            if out_dur:
                cmd += ["-t", f"{out_dur}"]
            cmd += [tmp_out]
            mimetype = "image/gif"
        else:
            vf_str = vbase_str + ",format=yuv420p"
            created = (datetime.utcnow() - timedelta(days=random.randint(1, 540),
                                                     minutes=random.randint(0, 1440)))
            iso = created.strftime('%Y-%m-%dT%H:%M:%S')
            model = random.choice(APPLE_MODELS)
            ios = random.choice(IOS_VERSIONS)
            if has_audio:
                atempo_val = round(speed / pitch, 5)
                audio_chain = (f"asetrate={int(sr*pitch)},aresample={sr},"
                               f"atempo={atempo_val},treble=g={treble_g},bass=g={bass_g}")
                tone_dur = out_dur if out_dur else (dur or 60)
                cmd += ["-f", "lavfi", "-t", f"{tone_dur}",
                        "-i", f"sine=frequency={tone_freq}:sample_rate={sr}"]
                fc = (f"[0:v]{vf_str}[v];"
                      f"[0:a]{audio_chain}[aa];"
                      f"[1:a]volume={tone_vol}[tn];"
                      f"[aa][tn]amix=inputs=2:duration=first:normalize=0[a]")
                cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                        "-c:a", "aac", "-b:a", "128k"]
            else:
                cmd += ["-vf", vf_str, "-an"]
            if out_dur:
                cmd += ["-t", f"{out_dur}"]
            if fps and fps > 0:
                cmd += ["-r", f"{round(fps * (1 + fps_jitter), 4)}"]
            cmd += [
                "-map_metadata", "-1",
                "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-metadata", "make=Apple",
                "-metadata", f"model={model}",
                "-metadata", "com.apple.quicktime.make=Apple",
                "-metadata", f"com.apple.quicktime.model={model}",
                "-metadata", f"com.apple.quicktime.software={ios}",
                "-metadata", f"com.apple.quicktime.creationdate={iso}+0000",
                "-metadata", f"creation_time={iso}",
                "-movflags", "+faststart+use_metadata_tags",
                tmp_out,
            ]
            mimetype = "video/mp4"

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=600)
        except FileNotFoundError:
            abort(500, "ffmpeg is not installed on this server (needed for video spoofing)")
        if proc.returncode != 0 or not os.path.exists(tmp_out):
            err = proc.stderr.decode(errors="ignore")[-400:]
            abort(500, f"ffmpeg failed: {err}")

        with open(tmp_out, "rb") as fh:
            data = fh.read()
        return Response(data, mimetype=mimetype)
    finally:
        for p in (tmp_in.name, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass


# ----------------------------------------------------------------------------
# PWA bits: icon, manifest, service worker (lets phones "Add to Home Screen").
# ----------------------------------------------------------------------------
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">'
        '<rect width="180" height="180" rx="40" fill="#ff4500"/>'
        '<text x="90" y="128" font-family="Helvetica,Arial" font-size="112"'
        ' font-weight="bold" text-anchor="middle" fill="#fff">S</text></svg>')


@app.route("/icon.svg")
def icon_svg():
    return Response(ICON, mimetype="image/svg+xml")


@app.route("/icon-180.png")
def icon_png():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (180, 180), "#ff4500")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 112)
    except Exception:
        f = ImageFont.load_default()
    d.text((90, 90), "S", fill="#fff", font=f, anchor="mm")
    b = io.BytesIO()
    img.save(b, format="PNG")
    return Response(b.getvalue(), mimetype="image/png")


@app.route("/manifest.webmanifest")
def manifest():
    return Response(
        '{"name":"Spoofer","short_name":"Spoofer","start_url":"/","scope":"/",'
        '"display":"standalone","background_color":"#08090b","theme_color":"#08090b",'
        '"icons":[{"src":"/icon-180.png","sizes":"180x180","type":"image/png"}]}',
        mimetype="application/manifest+json")


@app.route("/sw.js")
def sw():
    # Network-first: a deploy is picked up immediately, the cache only covers
    # opening the app with no signal.
    return Response("""
const C='spoofer-v1';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith((async()=>{
    try{const f=await fetch(e.request);(await caches.open(C)).put(e.request,f.clone());return f;}
    catch(err){const h=await caches.match(e.request);if(h)return h;throw err;}
  })());
});
""", mimetype="application/javascript", headers={"Cache-Control": "no-store"})


@app.route("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


if __name__ == "__main__":
    if not APP_PASSWORD and not NO_AUTH:
        print("WARNING: APP_PASSWORD is empty in .env — nobody can log in")
    app.run(host=BIND, port=PORT, threaded=True)
