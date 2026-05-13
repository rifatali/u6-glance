"""
Glance LED -> Berlin U6 Schwartzkopffstr. departures (PNG)

Two ways to use this:

  1) As a Flask server (live, polled by Glance directly):
         python departures.py            # http://0.0.0.0:8080/

  2) As a one-shot PNG generator (for GitHub Actions + Pages):
         python build.py                 # writes docs/u6.png

The image is 384x32 and shows absolute departure times (HH:MM) for the
next U6 trains in both directions, so a 5-minute CI refresh interval
does not lead to misleading "minutes until" values.
"""

import io
import os
import time
import threading
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

# --- Config -----------------------------------------------------------------

BVG_BASE = "https://v6.bvg.transport.rest"
STATION_QUERY = "Schwartzkopffstr"
LINE_FILTER = "U6"

DIR_NORTH = "Alt-Tegel"
DIR_SOUTH = "Alt-Mariendorf"
DIR_SHORT = {DIR_NORTH: "Tegel", DIR_SOUTH: "Mariendorf"}

CACHE_TTL = 20
LOOKAHEAD_MIN = 60

PANEL_W = 384
PANEL_H = 32

BERLIN_TZ = timezone(timedelta(hours=2))   # Berlin summer time (DST aware below)

BG          = (0, 0, 0)
U6_BG       = (139, 71, 137)
U6_FG       = (255, 255, 255)
DIR_COLOR   = (255, 165, 0)
TIME_NEXT   = (255, 255, 255)   # next departure: white
TIME_LATER  = (130, 220, 130)   # later ones: pale green
DIVIDER     = (40, 40, 40)
EMPTY_TEXT  = (160, 160, 160)

FONT_CANDIDATES_SANS = [
    "/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]
FONT_CANDIDATES_MONO = [
    "/usr/share/fonts/opentype/urw-base35/NimbusMonoPS-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "C:\\Windows\\Fonts\\consolab.ttf",
]


def _load_font(candidates, size):
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


FONT_LABEL = _load_font(FONT_CANDIDATES_SANS, 11)
FONT_BADGE = _load_font(FONT_CANDIDATES_SANS, 12)
FONT_TIME  = _load_font(FONT_CANDIDATES_MONO, 13)

# --- BVG client -------------------------------------------------------------

_cache_lock = threading.Lock()
_cache = {"stop_id": None, "deps": [], "ts": 0.0}


def resolve_stop_id():
    with _cache_lock:
        if _cache["stop_id"]:
            return _cache["stop_id"]
    r = requests.get(
        f"{BVG_BASE}/locations",
        params={"query": STATION_QUERY, "results": 5,
                "poi": "false", "addresses": "false"},
        timeout=10,
    )
    r.raise_for_status()
    for hit in r.json():
        if hit.get("type") == "stop" and "Schwartzkopff" in hit.get("name", ""):
            with _cache_lock:
                _cache["stop_id"] = hit["id"]
            return hit["id"]
    raise RuntimeError(f"Station not found: {STATION_QUERY}")


def fetch_departures():
    now = time.time()
    with _cache_lock:
        if now - _cache["ts"] < CACHE_TTL and _cache["deps"]:
            return _cache["deps"]
    stop_id = resolve_stop_id()
    r = requests.get(
        f"{BVG_BASE}/stops/{stop_id}/departures",
        params={
            "duration": LOOKAHEAD_MIN, "subway": "true",
            "suburban": "false", "tram": "false", "bus": "false",
            "ferry": "false", "express": "false", "regional": "false",
            "results": 30, "language": "de",
        },
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    deps = payload.get("departures", payload) if isinstance(payload, dict) else payload
    with _cache_lock:
        _cache["deps"] = deps
        _cache["ts"] = now
    return deps


def next_times_for_direction(deps, terminus, n=3):
    """Return list of HH:MM strings for the next n departures heading to `terminus`."""
    out = []
    now_utc = datetime.now(timezone.utc)
    for d in deps:
        if (d.get("line") or {}).get("name") != LINE_FILTER:
            continue
        if terminus not in (d.get("direction") or ""):
            continue
        ts = d.get("when") or d.get("plannedWhen")
        if not ts:
            continue
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < now_utc - timedelta(minutes=1):
            continue   # skip departures already in the past
        # BVG returns timestamps with proper tz offset for Berlin
        out.append(when.strftime("%H:%M"))
        if len(out) >= n:
            break
    return out

# --- Rendering --------------------------------------------------------------

def _draw_badge(draw, x, y, w=22, h=14):
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=U6_BG)
    bbox = draw.textbbox((0, 0), "U6", font=FONT_BADGE)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x + (w - tw) // 2 - bbox[0]
    ty = y + (h - th) // 2 - bbox[1] - 1
    draw.text((tx, ty), "U6", fill=U6_FG, font=FONT_BADGE)


def _draw_half(draw, x0, width, terminus, times):
    """Draw one direction tile into [x0, x0+width)."""
    badge_w, badge_h = 22, 14
    _draw_badge(draw, x0 + 2, 1, w=badge_w, h=badge_h)

    short = DIR_SHORT.get(terminus, terminus)
    draw.text((x0 + 2 + badge_w + 4, 1), f"> {short}",
              fill=DIR_COLOR, font=FONT_LABEL)

    if not times:
        draw.text((x0 + 2, 17), "keine Daten",
                  fill=EMPTY_TEXT, font=FONT_LABEL)
        return

    # Distribute up to 3 HH:MM times evenly across the half
    inner_x = x0 + 2
    inner_w = width - 4
    cells = len(times)
    cell_w = inner_w // cells
    for i, t in enumerate(times):
        bbox = draw.textbbox((0, 0), t, font=FONT_TIME)
        tw = bbox[2] - bbox[0]
        cx = inner_x + cell_w // 2 + i * cell_w - tw // 2 - bbox[0]
        color = TIME_NEXT if i == 0 else TIME_LATER
        draw.text((cx, 17), t, fill=color, font=FONT_TIME)


def render_png():
    try:
        deps = fetch_departures()
        north = next_times_for_direction(deps, DIR_NORTH, n=3)
        south = next_times_for_direction(deps, DIR_SOUTH, n=3)
    except Exception as e:
        print(f"[warn] BVG fetch failed: {e}")
        north = south = []

    img = Image.new("RGB", (PANEL_W, PANEL_H), BG)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    half = PANEL_W // 2
    _draw_half(draw, 0, half, DIR_NORTH, north)
    _draw_half(draw, half, half, DIR_SOUTH, south)
    draw.line([(half, 2), (half, PANEL_H - 3)], fill=DIVIDER)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# --- Flask app (optional, only used by `python departures.py`) --------------

try:
    from flask import Flask, send_file, jsonify
    app = Flask(__name__)

    @app.get("/")
    def index():
        return send_file(io.BytesIO(render_png()), mimetype="image/png", max_age=10)

    @app.get("/raw")
    def raw():
        try:
            return jsonify(fetch_departures())
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    @app.get("/healthz")
    def health():
        return {"ok": True, "ts": int(time.time())}
except ImportError:
    app = None

if __name__ == "__main__":
    if app is None:
        raise SystemExit("flask not installed — `pip install flask` to run as server")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
