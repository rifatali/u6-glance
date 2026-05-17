"""
Glance LED 64x32 App Router

Rendert je nach Zeit-Slot eine andere App im echten LED-Ticker-Stil:
  - U6 Nord (scrollender Ziel-Name + Minuten)
  - U6 Sued (scrollender Ziel-Name + Minuten)
  - Uhrzeit + Datum
  - Wetter Berlin (Pixel-Art Icon + Temperatur)

Jede App bekommt einen 15-Sekunden-Slot. Der Server rendert pro Abfrage
genau das, was im aktuellen Slot gerade dran ist. Text der breiter als
64 px ist scrollt horizontal -- der Scroll-Offset haengt von time.time()
ab, sodass aufeinanderfolgende Glance-Polls weitergeschoben kommen.
"""

import io
import os
import time
import threading
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

# --- Panel ------------------------------------------------------------------

PANEL_W = 64
PANEL_H = 32

# --- Slot timing ------------------------------------------------------------

SLOT_SECONDS = 15        # how long each app stays on
SCROLL_SPEED_PX_PER_S = 16.0
SCROLL_PAUSE_END_S = 0.5    # pause at end of cycle before restarting

# --- Colors -----------------------------------------------------------------

BG          = (0, 0, 0)
AMBER       = (255, 160, 0)
AMBER_DIM   = (180, 110, 0)
WHITE       = (255, 255, 255)
U6_PURPLE   = (139, 71, 137)
RED         = (240, 60, 60)
YELLOW      = (255, 220, 60)
GREEN       = (60, 220, 90)
SKY         = (90, 140, 200)
CLOUD       = (170, 180, 195)
RAIN        = (90, 140, 220)
SNOW        = (220, 230, 255)
SUN         = (255, 200, 60)
MOON        = (220, 230, 255)
THUNDER     = (255, 230, 90)
EMPTY_TEXT  = (130, 90, 0)

# --- Fonts ------------------------------------------------------------------

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


FONT_TINY    = _load_font(FONT_CANDIDATES_SANS_NARROW, 8)   # ~6 px tall
FONT_SMALL   = _load_font(FONT_CANDIDATES_SANS_NARROW, 10)  # ~8 px tall
FONT_BODY    = _load_font(FONT_CANDIDATES_SANS_NARROW, 12)
FONT_BIG     = _load_font(FONT_CANDIDATES_SANS_NARROW, 16)
FONT_MONO    = _load_font(FONT_CANDIDATES_MONO, 11)
FONT_CLOCK   = _load_font(FONT_CANDIDATES_MONO, 18)

# --- Draw helpers -----------------------------------------------------------

def new_image():
    img = Image.new("RGB", (PANEL_W, PANEL_H), BG)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    return img, draw


def text_bbox(font, text):
    """Return (w, h, x_offset, y_offset)."""
    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], -bbox[0], -bbox[1]


def draw_centered(draw, x_center, y, text, font, color):
    w, h, ox, oy = text_bbox(font, text)
    draw.text((x_center - w // 2 + ox, y), text, fill=color, font=font)


def draw_text_scrolling(img, region, text, font, color, t=None, gap_px=12, speed=None):
    """Draw text inside a (x, y, w, h) region. If the text is wider than the
       region, scroll it leftward at SCROLL_SPEED_PX_PER_S; otherwise center
       it within the region."""
    rx, ry, rw, rh = region
    tw, th, ox, oy = text_bbox(font, text)
    if t is None:
        t = time.time()
    speed = speed if speed is not None else SCROLL_SPEED_PX_PER_S

    if tw <= rw:
        # Centered, no scroll
        text_x = rx + (rw - tw) // 2 + ox
        text_y = ry + (rh - th) // 2 + oy - 1
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        draw.text((text_x, text_y), text, fill=color, font=font)
        return

    # Build the text once on a transparent overlay (height = rh).
    overlay = Image.new("RGBA", (tw + ox + 2, rh), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.fontmode = "1"
    od.text((ox, (rh - th) // 2 + oy - 1), text, fill=color, font=font)

    cycle_len = tw + gap_px
    offset = int((t * speed) % cycle_len)

    # Stripe = strictly clipped canvas the size of the scroll region.
    # Pixels outside the stripe are discarded.
    stripe = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    stripe.paste(overlay, (-offset, 0), overlay)
    stripe.paste(overlay, (-offset + cycle_len, 0), overlay)
    img.paste(stripe, (rx, ry), stripe)


def crop_to_region(img, region):
    """Mask the image so only the given region ist visible (everything outside
       becomes BG)."""
    rx, ry, rw, rh = region
    canvas = Image.new("RGB", (PANEL_W, PANEL_H), BG)
    sub = img.crop((rx, ry, rx + rw, ry + rh))
    canvas.paste(sub, (rx, ry))
    return canvas


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# ============================================================================
# BVG client (shared by both U6 apps)
# ============================================================================

BVG_BASE = "https://v6.bvg.transport.rest"
STATION_QUERY = "Schwartzkopffstr"
LINE_FILTER = "U6"

DIR_NORTH_MATCH = ("Alt-Tegel", "Kurt-Schumacher-Platz", "Kurt-Schumacher")
DIR_SOUTH_MATCH = ("Alt-Mariendorf",)

BVG_CACHE_TTL = 20

_bvg_lock = threading.Lock()
_bvg_cache = {"stop_id": None, "deps": [], "ts": 0.0}


def _bvg_resolve_stop_id():
    with _bvg_lock:
        if _bvg_cache["stop_id"]:
            return _bvg_cache["stop_id"]
    r = requests.get(
        f"{BVG_BASE}/locations",
        params={"query": STATION_QUERY, "results": 5,
                "poi": "false", "addresses": "false"},
        timeout=10,
    )
    r.raise_for_status()
    for hit in r.json():
        if hit.get("type") == "stop" and "Schwartzkopff" in hit.get("name", ""):
            with _bvg_lock:
                _bvg_cache["stop_id"] = hit["id"]
            return hit["id"]
    raise RuntimeError(f"Station not found: {STATION_QUERY}")


def bvg_departures():
    now = time.time()
    with _bvg_lock:
        if now - _bvg_cache["ts"] < BVG_CACHE_TTL and _bvg_cache["deps"]:
            return _bvg_cache["deps"]
    stop_id = _bvg_resolve_stop_id()
    r = requests.get(
        f"{BVG_BASE}/stops/{stop_id}/departures",
        params={
            "duration": 90, "subway": "true",
            "suburban": "false", "tram": "false", "bus": "false",
            "ferry": "false", "express": "false", "regional": "false",
            "results": 60, "language": "de",
        },
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    deps = payload.get("departures", payload) if isinstance(payload, dict) else payload
    with _bvg_lock:
        _bvg_cache["deps"] = deps
        _bvg_cache["ts"] = now
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


def bvg_next_for_direction(terminus_match):
    """Return (when_dt, direction_str) for the next train of the given direction,
       or (None, None) if nothing pending."""
    deps = bvg_departures()
    now_utc = datetime.now(timezone.utc)
    best = None
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
        if best is None or when < best[0]:
            best = (when, direction)
    return best if best else (None, None)


def clean_direction(direction):
    """Strip 'U ' / 'S+U ' prefixes from BVG direction strings."""
    d = direction.strip()
    for prefix in ("S+U ", "S ", "U "):
        if d.startswith(prefix):
            return d[len(prefix):]
    return d


def minutes_until(when_dt):
    return max(0, int((when_dt - datetime.now(timezone.utc)).total_seconds() / 60))


# ============================================================================
# App: U6 (one variant per direction; the router calls them in turn)
# ============================================================================

def _draw_u6_badge(draw, x, y):
    """Draw a 14x12 violet 'U6' badge at (x, y)."""
    draw.rectangle([x, y, x + 13, y + 11], fill=U6_PURPLE)
    draw_centered(draw, x + 7, y + 1, "U6", FONT_SMALL, WHITE)


def _render_u6(terminus_match, _t=None):
    img, draw = new_image()
    t = _t if _t is not None else time.time()

    when, direction = bvg_next_for_direction(terminus_match)

    # Top row: U6 badge + scrolling destination
    _draw_u6_badge(draw, 0, 1)
    ziel = clean_direction(direction) if direction else "keine Abfahrt"
    draw_text_scrolling(img, (16, 0, PANEL_W - 16, 13),
                        ziel, FONT_SMALL,
                        AMBER if direction else EMPTY_TEXT,
                        t=t)

    # Bottom row: time
    if when is None:
        draw_centered(draw, PANEL_W // 2, 17, "--", FONT_BIG, EMPTY_TEXT)
    else:
        mins = minutes_until(when)
        label = "jetzt" if mins == 0 else f"{mins} min"
        color = RED if mins <= 2 else (YELLOW if mins <= 5 else AMBER)
        draw_centered(draw, PANEL_W // 2, 16, label, FONT_BIG, color)
    return img


def render_u6_north(_t=None):
    return _render_u6(DIR_NORTH_MATCH, _t=_t)


def render_u6_south(_t=None):
    return _render_u6(DIR_SOUTH_MATCH, _t=_t)


# ============================================================================
# App: Clock
# ===========================================================================

BERLIN_TZ_OFFSET_S = 2 * 3600   # CEST (summer); for winter use 3600
WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def render_clock(_t=None):
    img, draw = new_image()
    t = _t if _t is not None else time.time()
    berlin = datetime.fromtimestamp(t, tz=timezone(timedelta(seconds=BERLIN_TZ_OFFSET_S)))

    # Blinking colon: visible for first half-second, hidden second half
    colon = ":" if (int(t * 2) % 2 == 0) else " "
    time_str = berlin.strftime(f"%H{colon}%M")
    draw_centered(draw, PANEL_W // 2, 0, time_str, FONT_CLOCK, AMBER)

    date_str = f"{WEEKDAYS_DE[berlin.weekday()]} {berlin.strftime('%d.%m.')}"
    draw_centered(draw, PANEL_W // 2, 21, date_str, FONT_SMALL, AMBER_DIM)
    return img


# ============================================================================
# App: Weather (Open-Meteo, no API key)
# ============================================================================

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=52.5200&longitude=13.4050"
    "&current=temperature_2m,weather_code,is_day,wind_speed_10m"
    "&daily=temperature_2m_max,temperature_2m_min"
    "&timezone=Europe/Berlin"
)

WEATHER_CACHE_TTL = 600   # 10 min
_weather_lock = threading.Lock()
_weather_cache = {"data": None, "ts": 0.0}


def fetch_weather():
    now = time.time()
    with _weather_lock:
        if now - _weather_cache["ts"] < WEATHER_CACHE_TTL and _weather_cache["data"]:
            return _weather_cache["data"]
    r = requests.get(WEATHER_URL, timeout=10)
    r.raise_for_status()
    data = r.json()
    with _weather_lock:
        _weather_cache["data"] = data
        _weather_cache["ts"] = now
    return data


# --- 16x16 pixel-art weather icons -----------------------------------------
# Each icon ist a list of 16 strings, 16 chars wide. Character -> color:
#   '.' transparent, 's' sun, 'c' cloud, 'r' rain, 'n' snow,
#   't' thunder, 'm' moon

PIXEL_PALETTE = {
    's': SUN, 'c': CLOUD, 'r': RAIN, 'n': SNOW,
    't': THUNDER, 'm': MOON, 'k': (50, 50, 60),
    'b': SKY,
}

ICON_SUN = [
    "................",
    ".......ss.......",
    "....s..ss..s....",
    ".....s.ss.s.....",
    ".....sssss......",
    "...ssssssssss...",
    "..sssssssssss...",
    "..sssssssssss...",
    "...ssssssss.....",
    "....ssssss......",
    "...s.ssss..s....",
    "..s..ssss...s...",
    ".....s.ss.......",
    "................",
    "................",
    "................",
]

ICON_MOON = [
    "................",
    ".....mmmm.......",
    "....m...mm......",
    "...m.....mm.....",
    "..m......mm.....",
    "..m......m......",
    "..m.....m.......",
    "..mm...m........",
    "...mmmmm........",
    "................",
    "................",
    "................",
    "................",
    "................",
    "................",
    "................",
]

ICON_CLOUD = [
    "................",
    "................",
    ".....ccccc......",
    "....cccccccc....",
    "...ccccccccccc..",
    "..cccccccccccc..",
    ".cccccccccccccc.",
    ".cccccccccccccc.",
    "..cccccccccccc..",
    "................",
    "................",
    "................",
    "................",
    "................",
    "................",
    "................",
]

ICON_SUN_CLOUD = [
    "................",
    "....s...........",
    ".s..ss..s.......",
    "..s.ss.s........",
    "..ssssss........",
    "ssssssss........",
    "..ssssss.ccc....",
    "..s.ss..cccccc..",
    ".....s.ccccccc..",
    ".......cccccccc.",
    "......cccccccccc",
    "......cccccccccc",
    ".......ccccccc..",
    "................",
    "................",
    "................",
]

ICON_RAIN = [
    "................",
    "....ccccc.......",
    "...cccccccc.....",
    "..cccccccccc....",
    "..cccccccccc....",
    "...cccccccc.....",
    "..rrrrrrrrrr....",
    ".r..r..r..r.....",
    "r..r..r..r......",
    "..r..r..r.r.....",
    ".r..r..r..r.....",
    "r..r..r..r......",
    "................",
    "................",
    "................",
    "................",
]

ICON_SNOW = [
    "................",
    "....ccccc.......",
    "...cccccccc.....",
    "..cccccccccc....",
    "..cccccccccc....",
    "...cccccccc.....",
    "................",
    ".n.n..n..n.n....",
    "..n.n..n.n......",
    ".n..nnnn..n.....",
    "..nnnnnnnn......",
    ".n..nnnn..n.....",
    "..n.n..n.n......",
    ".n.n..n..n.n....",
    "................",
    "................",
]

ICON_THUNDER = [
    "................",
    "...ccccccc......",
    "..ccccccccc.....",
    ".ccccccccccc....",
    ".ccccccccccc....",
    "..ccccccccc.....",
    "....tttttt......",
    "...tttttt.......",
    "..ttttt.........",
    ".ttttttttt......",
    "....tttttt......",
    "...ttttt........",
    "..tttt..........",
    ".tttt...........",
    "................",
    "................",
]

ICON_FOG = [
    "................",
    "................",
    ".cccccccccccc...",
    "................",
    ".ccccccccccccc..",
    "................",
    ".cccccccccccc...",
    "................",
    ".ccccccccccccc..",
    "................",
    ".cccccccccccc...",
    "................",
    ".ccccccccccccc..",
    "................",
    "................",
    "................",
]


def draw_icon(img, x, y, icon, palette=PIXEL_PALETTE):
    for j, rwo in enumerate(icon):
        for i, ch in enumerate(rwo):
            if ch in palette:
                img.putpixel((x + i, y + j), palette[ch])


# WMO code -> icon
def weather_icon_for(code, is_day):
    if code == 0:
        return ICON_SUN if is_day else ICON_MOON
    if code in (1, 2):
        return ICON_SUN_CLOUD if is_day else ICON_MOON
    if code in (3,):
        return ICON_CLOUD
    if code in (45, 48):
        return ICON_FOG
    if code in (51, 53, 55, 56, 57):
        return ICON_RAIN
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return ICON_RAIN
    if code in (71, 73, 75, 77, 85, 86):
        return ICON_SNOW
    if code in (95, 96, 99):
        return ICON_THUNDER
    return ICON_CLOUD


def render_weather(_t=None):
    img, draw = new_image()
    try:
        data = fetch_weather()
        current = data.get("current", {})
        daily = data.get("daily", {})
        temp = int(round(current.get("temperature_2m", 0)))
        code = int(current.get("weather_code", 3))
        is_day = bool(current.get("is_day", 1))
        max_t = int(round((daily.get("temperature_2m_max") or [temp])[0]))
        min_t = int(round((daily.get("temperature_2m_min") or [temp])[0]))
    except Exception as e:
        print(f"[warn] weather fetch failed: {e}")
        draw_centered(draw, PANEL_W // 2, 12, "Wetter?", FONT_BODY, EMPTY_TEXT)
        return img

    # Left half: 16x16 icon vertically centered
    draw_icon(img, 0, 8, weather_icon_for(code, is_day))

    # Right half: big temperature + min/max
    temp_str = f"{temp}\xb0"
    draw_centered(draw, 16 + (PANEL_W - 16) // 2, -1, temp_str, FONT_BIG, AMBER)
    minmax = f"{min_t}\xb0/{max_t}\xb0"
    draw_centered(draw, 16 + (PANEL_W - 16) // 2, 19, minmax, FONT_SMALL, AMBER_DIM)
    return img


# ============================================================================
# Router
# ============================================================================

APPS = [
    ("u6_north", render_u6_north),
    ("u6_south", render_u6_south),
    ("weather",  render_weather),
    ("clock",    render_clock),
]


def current_app_index():
    return (int(time.time()) // SLOT_SECONDS) % len(APPS)


def render_png():
    try:
        name, fn = APPS[current_app_index()]
        img = fn()
    except Exception as e:
        print(f"[warn] app render failed: {e}")
        img, draw = new_image()
        draw_centered(draw, PANEL_W // 2, 12, "Fehler", FONT_BODY, RED)
    return png_bytes(img)


def render_png_for_app(name):
    for n, fn in APPS:
        if n == name:
            return png_bytes(fn())
    raise KeyError(name)


# ============================================================================
# Flask app
# ============================================================================

try:
    from flask import Flask, send_file, jsonify
    app = Flask(__name__)

    @app.get("/")
    def index():
        return send_file(io.BytesIO(render_png()), mimetype="image/png", max_age=5)

    @app.get("/<name>.png")
    def app_png(name):
        try:
            return send_file(io.BytesIO(render_png_for_app(name)),
                             mimetype="image/png", max_age=5)
        except KeyError:
            return ("unknown app", 404)

    @app.get("/healthz")
    def health():
        return {"ok": True, "ts": int(time.time())}

    @app.get("/apps")
    def list_apps():
        return jsonify([n for n, _ in APPS])
except ImportError:
    app = None

if __name__ == "__main__":
    if app is None:
        raise SystemExit("flask not installed")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
