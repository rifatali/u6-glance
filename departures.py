"""
Glance LED -> Berlin U6 Schwartzkopffstr. departures (PNG)

384x32 schwarz mit amber-orangenem Text, ZWEI ZEILEN je Richtung.
Der Inhalt wechselt im 15-Sekunden-Rhythmus zwischen den beiden
Richtungen (Tegel/K-S-Platz <-> Mariendorf), damit Glance bei seiner
naechsten Abfrage abwechselnd die eine oder andere Richtung sieht.
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

DIR_NORTH_MATCH = ("Alt-Tegel", "Kurt-Schumacher-Platz", "Kurt-Schumacher")
DIR_SOUTH_MATCH = ("Alt-Mariendorf",)

# Bucket size: every BUCKET_SECONDS the displayed direction flips
BUCKET_SECONDS = 15

CACHE_TTL = 10        # short cache - we want fresh content per render
LOOKAHEAD_MIN = 90

PANEL_W = 384
PANEL_H = 32

BG          = (0, 0, 0)
AMBER       = (255, 160, 0)
EMPTY_TEXT  = (130, 90, 0)

COL_LINIE_X   = 2
COL_ZIEL_X    = 44
COL_ABFAHRT_R = 380
ROW_YS        = (1, 17)

FONT_CANDIDATES_SANS_NARROW = [
    "/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
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


FONT_ROW = _load_font(FONT_CANDIDATES_SANS_NARROW, 15)
FONT_MIN = _load_font(FONT_CANDIDATES_MONO, 15)

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
            "results": 60, "language": "de",
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


def _parse_ts(ts):
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def next_for_direction(deps, terminus_match, n=2):
    now_utc = datetime.now(timezone.utc)
    cands = []
    for d in deps:
        line = (d.get("line") or {}).get("name")
        if line != LINE_FILTER:
            continue
        direction = d.get("direction") or ""
        if not any(t in direction for t in terminus_match):
            continue
        when = _parse_ts(d.get("when") or d.get("plannedWhen"))
        if when is None or when < now_utc - timedelta(seconds=30):
            continue
        cands.append((when, direction, line))
    cands.sort(key=lambda c: c[0])
    return cands[:n]


def build_frame_rows(deps, terminus_match):
    """Return list of (line, ziel, mins) for the next 2 departures of one direction."""
    now_utc = datetime.now(timezone.utc)
    rows = []
    for when, direction, line in next_for_direction(deps, terminus_match, n=2):
        mins = max(0, int((when - now_utc).total_seconds() / 60))
        ziel = direction.strip()
        for prefix in ("S+U ", "S ", "U "):
            if ziel.startswith(prefix):
                ziel = ziel[len(prefix):]
                break
        rows.append((line, ziel, mins))
    return rows

# --- Rendering --------------------------------------------------------------

def _text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], -bbox[0]


def _draw_right_aligned(draw, x_right, y, text, font, color):
    w, ox = _text_width(draw, text, font)
    draw.text((x_right - w + ox, y), text, fill=color, font=font)


def _draw_truncated(draw, x, y, max_x, text, font, color):
    w, ox = _text_width(draw, text, font)
    if x + w <= max_x:
        draw.text((x - ox, y), text, fill=color, font=font)
        return
    s = text
    while len(s) > 1:
        s = s[:-1]
        candidate = s + "."
        cw, cox = _text_width(draw, candidate, font)
        if x + cw <= max_x:
            draw.text((x - cox, y), candidate, fill=color, font=font)
            return
    draw.text((x, y), "...", fill=color, font=font)


def render_frame(rows):
    """Render a single 384x32 frame for the given rows."""
    img = Image.new("RGB", (PANEL_W, PANEL_H), BG)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    for i, y in enumerate(ROW_YS):
        if i >= len(rows):
            if i == 0 and not rows:
                draw.text((COL_LINIE_X, y), "keine Daten",
                          fill=EMPTY_TEXT, font=FONT_ROW)
            break
        line, ziel, mins = rows[i]
        draw.text((COL_LINIE_X, y), line, fill=AMBER, font=FONT_ROW)
        ziel_right_limit = COL_ABFAHRT_R - 50
        _draw_truncated(draw, COL_ZIEL_X, y, ziel_right_limit,
                        ziel, FONT_ROW, AMBER)
        mins_text = f"{mins}'" if mins > 0 else "jetzt"
        _draw_right_aligned(draw, COL_ABFAHRT_R, y,
                            mins_text, FONT_MIN, AMBER)
    return img


def render_png():
    """Return a single 384x32 PNG. Which direction is shown depends on the
       current 15-second bucket - so the same URL alternates between
       directions over time."""
    try:
        deps = fetch_departures()
    except Exception as e:
        print(f"[warn] BVG fetch failed: {e}")
        deps = []

    bucket = (int(time.time()) // BUCKET_SECONDS) % 2
    terminus = DIR_NORTH_MATCH if bucket == 0 else DIR_SOUTH_MATCH
    rows = build_frame_rows(deps, terminus)
    img = render_frame(rows)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_png_for_direction(north=True):
    """Render a static PNG for one specific direction (used for the
       direction-specific URLs tegel.png / mariendorf.png)."""
    try:
        deps = fetch_departures()
    except Exception as e:
        print(f"[warn] BVG fetch failed: {e}")
        deps = []
    terminus = DIR_NORTH_MATCH if north else DIR_SOUTH_MATCH
    rows = build_frame_rows(deps, terminus)
    img = render_frame(rows)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# --- Flask app (optional) ---------------------------------------------------

try:
    from flask import Flask, send_file, jsonify
    app = Flask(__name__)

    @app.get("/")
    def index():
        return send_file(io.BytesIO(render_png()), mimetype="image/png", max_age=5)

    @app.get("/tegel.png")
    def tegel():
        return send_file(io.BytesIO(render_png_for_direction(north=True)),
                         mimetype="image/png", max_age=5)

    @app.get("/mariendorf.png")
    def mariendorf():
        return send_file(io.BytesIO(render_png_for_direction(north=False)),
                         mimetype="image/png", max_age=5)

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
        raise SystemExit("flask not installed")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
