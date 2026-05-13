"""
Glance LED -> Berlin U6 Schwartzkopffstr. departures (PNG)

Shows the next U6 trains in both directions as minutes-until-departure
on a 384x32 LED panel. Resilient against BVG returning departures out
of chronological order (real-time tracked ones come AFTER planned-only
ones in the response), and against terminus name changes (e.g. during
construction the north-bound terminus is "Kurt-Schumacher-Platz"
instead of "Alt-Tegel").
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

# Each direction is a tuple of acceptable terminus substrings — covers
# both the official northern terminus (Alt-Tegel) and the temporary
# construction-time turnaround (Kurt-Schumacher-Platz).
DIR_NORTH_MATCH = ("Alt-Tegel", "Kurt-Schumacher-Platz", "Kurt-Schumacher")
DIR_SOUTH_MATCH = ("Alt-Mariendorf",)
DIR_NORTH_LABEL = "Tegel"
DIR_SOUTH_LABEL = "Mariendorf"

CACHE_TTL = 20
LOOKAHEAD_MIN = 90

PANEL_W = 384
PANEL_H = 32

BG          = (0, 0, 0)
U6_BG       = (139, 71, 137)
U6_FG       = (255, 255, 255)
DIR_COLOR   = (255, 165, 0)
MIN_SOON    = (255, 51, 51)    # <= 2 min: red
MIN_NEAR    = (255, 255, 0)    # <= 5 min: yellow
MIN_FAR     = (0, 255, 85)     # otherwise: green
MIN_LABEL   = (170, 170, 170)
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
FONT_MIN   = _load_font(FONT_CANDIDATES_MONO, 16)   # big minutes
FONT_UNIT  = _load_font(FONT_CANDIDATES_SANS, 9)    # "min" label

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


def next_minutes_for_direction(deps, terminus_match, n=3):
    """Return the next n minutes-until-departure for matching trains."""
    now_utc = datetime.now(timezone.utc)
    candidates = []
    for d in deps:
        if (d.get("line") or {}).get("name") != LINE_FILTER:
            continue
        direction = d.get("direction") or ""
        if not any(t in direction for t in terminus_match):
            continue
        when = _parse_ts(d.get("when") or d.get("plannedWhen"))
        if when is None:
            continue
        if when < now_utc - timedelta(seconds=30):
            continue   # already in the past
        candidates.append(when)
    # Explicit sort — BVG mixes planned-only and real-time entries
    candidates.sort()
    out = []
    for when in candidates:
        mins = int((when - now_utc).total_seconds() / 60)
        out.append(max(0, mins))
        if len(out) >= n:
            break
    return out


def color_for_minutes(m):
    if m <= 2: return MIN_SOON
    if m <= 5: return MIN_NEAR
    return MIN_FAR

# --- Rendering --------------------------------------------------------------

def _draw_badge(draw, x, y, w=22, h=14):
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=U6_BG)
    bbox = draw.textbbox((0, 0), "U6", font=FONT_BADGE)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x + (w - tw) // 2 - bbox[0]
    ty = y + (h - th) // 2 - bbox[1] - 1
    draw.text((tx, ty), "U6", fill=U6_FG, font=FONT_BADGE)


def _draw_half(draw, x0, width, label, minutes):
    badge_w, badge_h = 22, 14
    _draw_badge(draw, x0 + 2, 1, w=badge_w, h=badge_h)

    draw.text((x0 + 2 + badge_w + 4, 1), f"> {label}",
              fill=DIR_COLOR, font=FONT_LABEL)

    if not minutes:
        draw.text((x0 + 2, 17), "keine Daten",
                  fill=EMPTY_TEXT, font=FONT_LABEL)
        return

    # Lay out up to 3 minute values across the half
    inner_x = x0 + 4
    inner_w = width - 26          # leave room for "min" on the right
    cells = len(minutes)
    cell_w = inner_w // cells
    for i, m in enumerate(minutes):
        text = str(m)
        bbox = draw.textbbox((0, 0), text, font=FONT_MIN)
        tw = bbox[2] - bbox[0]
        cx = inner_x + cell_w // 2 + i * cell_w - tw // 2 - bbox[0]
        draw.text((cx, 15), text, fill=color_for_minutes(m), font=FONT_MIN)

    draw.text((x0 + width - 22, 21), "min",
              fill=MIN_LABEL, font=FONT_UNIT)


def render_png():
    try:
        deps = fetch_departures()
        north = next_minutes_for_direction(deps, DIR_NORTH_MATCH, n=3)
        south = next_minutes_for_direction(deps, DIR_SOUTH_MATCH, n=3)
    except Exception as e:
        print(f"[warn] BVG fetch failed: {e}")
        north = south = []

    img = Image.new("RGB", (PANEL_W, PANEL_H), BG)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    half = PANEL_W // 2
    _draw_half(draw, 0, half, DIR_NORTH_LABEL, north)
    _draw_half(draw, half, half, DIR_SOUTH_LABEL, south)
    draw.line([(half, 2), (half, PANEL_H - 3)], fill=DIVIDER)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# --- Flask app (optional) ---------------------------------------------------

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
        raise SystemExit("flask not installed")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
