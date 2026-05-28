"""
Glance LED 64x32 App Router - Color Pixel-Art Edition

Vier Apps, jede mit eigenem Farbschema:
  - U6 Nord/Sued: BVG-Violett-Badge + dynamische Minuten-Farben
  - Uhrzeit: dunkelblaue Nachtszene oder warme Tag-Toene
  - Wetter Berlin: Himmel-Gradient passend zu Wetter und Tageszeit,
    grosse bunte Icons, helle Temperatur-Anzeige

Jede App bekommt einen 15-Sekunden-Slot. Text der breiter als 64 px ist
scrollt; der Scroll-Offset haengt von time.time() ab.
"""

import io
import os
import math
import time
import threading
from datetime import datetime, timezone, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont

# --- Panel ------------------------------------------------------------------

PANEL_W = 64
PANEL_H = 32

# --- Slot timing ------------------------------------------------------------

SLOT_SECONDS = 15
SCROLL_SPEED_PX_PER_S = 14.0

# --- Color palette (RGB, all distinct vibrant tones) ------------------------

BLACK       = (0, 0, 0)
WHITE       = (245, 250, 255)

# U6 / transit
U6_PURPLE   = (140, 70, 145)
U6_PURPLE_HI= (175, 95, 180)
RED         = (240, 60, 60)
ORANGE      = (255, 140, 30)
YELLOW      = (255, 220, 60)
GREEN_OK    = (60, 220, 90)
DEEP_GREEN  = (30, 130, 60)

# Sky / weather
SKY_DAY_TOP    = (90, 165, 230)
SKY_DAY_MID    = (130, 195, 240)
SKY_DAY_BOT    = (200, 225, 245)
SKY_DUSK_TOP   = (255, 130, 60)
SKY_DUSK_MID   = (235, 90, 100)
SKY_DUSK_BOT   = (90, 60, 130)
SKY_NIGHT_TOP  = (10, 15, 50)
SKY_NIGHT_MID  = (25, 30, 80)
SKY_NIGHT_BOT  = (60, 50, 110)
SKY_STORM_TOP  = (50, 55, 75)
SKY_STORM_MID  = (75, 80, 105)
SKY_STORM_BOT  = (110, 115, 140)

SUN_CORE       = (255, 235, 130)
SUN_HALO       = (255, 195, 70)
MOON_CORE      = (235, 240, 255)
MOON_SHADOW    = (180, 195, 220)
STAR_COLOR     = (255, 255, 220)
CLOUD_LIGHT    = (240, 245, 255)
CLOUD_DARK     = (160, 170, 200)
RAIN_DROP      = (135, 200, 255)
RAIN_DROP_DK   = (90, 160, 230)
SNOW_FLAKE     = (245, 250, 255)
LIGHTNING      = (255, 245, 130)
LIGHTNING_HOT  = (255, 200, 60)
FOG_LINE       = (200, 205, 220)

# Temperature colour mapping
COLD_BLUE   = (130, 200, 255)
COOL_CYAN   = (160, 230, 240)
NEUTRAL     = (240, 245, 255)
WARM        = (255, 215, 110)
HOT_RED     = (255, 110, 70)

# Clock theme
CLOCK_NIGHT_BG_TOP = (8, 12, 35)
CLOCK_NIGHT_BG_BOT = (28, 16, 60)
CLOCK_DAY_BG_TOP   = (255, 175, 70)
CLOCK_DAY_BG_BOT   = (255, 230, 130)
CLOCK_DUSK_BG_TOP  = (120, 50, 130)
CLOCK_DUSK_BG_BOT  = (255, 130, 90)
CLOCK_DIGIT_NIGHT  = (245, 250, 255)
CLOCK_DIGIT_DAY    = (45, 25, 20)
CLOCK_COLON_NIGHT  = (140, 220, 255)
CLOCK_COLON_DAY    = (220, 70, 40)
CLOCK_DATE_NIGHT   = (180, 195, 240)
CLOCK_DATE_DAY     = (110, 60, 30)

EMPTY_TEXT  = (130, 90, 60)

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


FONT_TINY    = _load_font(FONT_CANDIDATES_SANS_NARROW, 8)
FONT_SMALL   = _load_font(FONT_CANDIDATES_SANS_NARROW, 10)
FONT_BODY    = _load_font(FONT_CANDIDATES_SANS_NARROW, 12)
FONT_BIG     = _load_font(FONT_CANDIDATES_SANS_NARROW, 16)
FONT_MONO    = _load_font(FONT_CANDIDATES_MONO, 11)
FONT_CLOCK   = _load_font(FONT_CANDIDATES_MONO, 19)

# --- Draw helpers -----------------------------------------------------------

def new_image(bg_color=BLACK):
    img = Image.new("RGB", (PANEL_W, PANEL_H), bg_color)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    return img, draw


def gradient_bg(top, mid, bot):
    """Vertical gradient image: top color at y=0, mid at y=H//2, bot at y=H-1."""
    img = Image.new("RGB", (PANEL_W, PANEL_H), top)
    for y in range(PANEL_H):
        if y <= PANEL_H // 2:
            f = y / (PANEL_H / 2)
            r = int(top[0] + (mid[0] - top[0]) * f)
            g = int(top[1] + (mid[1] - top[1]) * f)
            b = int(top[2] + (mid[2] - top[2]) * f)
        else:
            f = (y - PANEL_H // 2) / (PANEL_H / 2)
            r = int(mid[0] + (bot[0] - mid[0]) * f)
            g = int(mid[1] + (bot[1] - mid[1]) * f)
            b = int(mid[2] + (bot[2] - mid[2]) * f)
        for x in range(PANEL_W):
            img.putpixel((x, y), (r, g, b))
    return img


def text_bbox(font, text):
    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1], -bbox[0], -bbox[1]


def draw_centered(draw, x_center, y, text, font, color):
    w, h, ox, oy = text_bbox(font, text)
    draw.text((x_center - w // 2 + ox, y), text, fill=color, font=font)


def draw_text_outline(draw, x, y, text, font, fill, outline=(0,0,0)):
    """Draw text with a 1-pixel outline for legibility on busy backgrounds."""
    for dx, dy in ((-1,0),(1,0),(0,-1),(0,1)):
        draw.text((x+dx, y+dy), text, fill=outline, font=font)
    draw.text((x, y), text, fill=fill, font=font)


def draw_centered_outlined(draw, x_center, y, text, font, fill, outline=(0,0,0)):
    w, h, ox, oy = text_bbox(font, text)
    x = x_center - w // 2 + ox
    draw_text_outline(draw, x, y, text, font, fill, outline)


def draw_text_scrolling(img, region, text, font, color, t=None,
                        gap_px=12, speed=None, outline=None):
    rx, ry, rw, rh = region
    tw, th, ox, oy = text_bbox(font, text)
    if t is None:
        t = time.time()
    speed = speed if speed is not None else SCROLL_SPEED_PX_PER_S

    if tw <= rw:
        text_x = rx + (rw - tw) // 2 + ox
        text_y = ry + (rh - th) // 2 + oy - 1
        d = ImageDraw.Draw(img); d.fontmode = "1"
        if outline:
            draw_text_outline(d, text_x, text_y, text, font, color, outline)
        else:
            d.text((text_x, text_y), text, fill=color, font=font)
        return

    overlay = Image.new("RGBA", (tw + ox + 4, rh), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay); od.fontmode = "1"
    tx, ty = ox, (rh - th) // 2 + oy - 1
    if outline:
        for dx, dy in ((-1,0),(1,0),(0,-1),(0,1)):
            od.text((tx+dx, ty+dy), text, fill=outline, font=font)
    od.text((tx, ty), text, fill=color, font=font)

    cycle_len = tw + gap_px
    offset = int((t * speed) % cycle_len)

    stripe = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    stripe.paste(overlay, (-offset, 0), overlay)
    stripe.paste(overlay, (-offset + cycle_len, 0), overlay)
    img.paste(stripe, (rx, ry), stripe)


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --- Berlin time helper -----------------------------------------------------

def berlin_now(t=None):
    t = t if t is not None else time.time()
    # naive DST: summer Apr-Oct = +2h, else +1h
    utc = datetime.fromtimestamp(t, tz=timezone.utc)
    offset = 2 if 3 <= utc.month <= 10 else 1
    return utc.astimezone(timezone(timedelta(hours=offset)))


def sky_palette_for_hour(hour):
    """Return (top, mid, bot, is_night, is_dusk) based on Berlin hour 0-23."""
    if 6 <= hour < 8:    # dawn
        return SKY_DUSK_TOP, SKY_DUSK_MID, SKY_DAY_BOT, False, True
    if 8 <= hour < 18:   # day
        return SKY_DAY_TOP, SKY_DAY_MID, SKY_DAY_BOT, False, False
    if 18 <= hour < 21:  # dusk
        return SKY_DUSK_TOP, SKY_DUSK_MID, SKY_DUSK_BOT, False, True
    return SKY_NIGHT_TOP, SKY_NIGHT_MID, SKY_NIGHT_BOT, True, False

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
        BVG_BASE + "/locations",
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
    raise RuntimeError("Station not found: " + STATION_QUERY)


def bvg_departures():
    now = time.time()
    with _bvg_lock:
        if now - _bvg_cache["ts"] < BVG_CACHE_TTL and _bvg_cache["deps"]:
            return _bvg_cache["deps"]
    stop_id = _bvg_resolve_stop_id()
    r = requests.get(
        BVG_BASE + "/stops/" + stop_id + "/departures",
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
    d = direction.strip()
    for prefix in ("S+U ", "S ", "U "):
        if d.startswith(prefix):
            return d[len(prefix):]
    return d


def minutes_until(when_dt):
    return max(0, int((when_dt - datetime.now(timezone.utc)).total_seconds() / 60))


# ============================================================================
# App: U6 (BVG-style on dark background, vivid colors per urgency)
# ============================================================================

def _draw_u6_badge(draw, x, y):
    # Solid BVG-style U6 badge
    draw.rectangle([x, y, x + 13, y + 11], fill=U6_PURPLE)
    draw_centered(draw, x + 7, y + 1, "U6", FONT_SMALL, WHITE)


def _render_u6(terminus_match, _t=None):
    # Original BVG-Bahnhofs look: pure black background
    img, draw = new_image(BLACK)
    t = _t if _t is not None else time.time()

    when, direction = bvg_next_for_direction(terminus_match)

    # Badge
    _draw_u6_badge(draw, 0, 1)

    # Direction (scrolling)
    ziel = clean_direction(direction) if direction else "keine Abfahrt"
    color = ORANGE if direction else EMPTY_TEXT
    draw_text_scrolling(img, (16, 0, PANEL_W - 16, 13),
                        ziel, FONT_SMALL, color, t=t)

    # Time line
    if when is None:
        draw_centered(draw, PANEL_W // 2, 17, "--", FONT_BIG, EMPTY_TEXT)
    else:
        mins = minutes_until(when)
        if mins == 0:
            label, color = "JETZT", RED
        elif mins <= 2:
            label, color = str(mins) + " min", RED
        elif mins <= 5:
            label, color = str(mins) + " min", YELLOW
        elif mins <= 9:
            label, color = str(mins) + " min", GREEN_OK
        else:
            label, color = str(mins) + " min", WHITE
        draw_centered(draw, PANEL_W // 2, 16, label, FONT_BIG, color)
    return img


def render_u6_north(_t=None):
    return _render_u6(DIR_NORTH_MATCH, _t=_t)


def render_u6_south(_t=None):
    return _render_u6(DIR_SOUTH_MATCH, _t=_t)


# ============================================================================
# App: Clock (day / dusk / night themes with vivid digits)
# ============================================================================

WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def render_clock(_t=None):
    t = _t if _t is not None else time.time()
    berlin = berlin_now(t)
    hr = berlin.hour

    if 21 <= hr or hr < 6:
        bg = gradient_bg(CLOCK_NIGHT_BG_TOP, CLOCK_NIGHT_BG_TOP, CLOCK_NIGHT_BG_BOT)
        digit_color = CLOCK_DIGIT_NIGHT
        colon_color = CLOCK_COLON_NIGHT
        date_color  = CLOCK_DATE_NIGHT
        # Add a few stars
        d = ImageDraw.Draw(bg)
        for sx, sy in [(8, 5), (22, 3), (50, 6), (58, 11), (4, 14), (30, 2)]:
            d.point((sx, sy), fill=STAR_COLOR)
    elif 6 <= hr < 8 or 19 <= hr < 21:
        bg = gradient_bg(CLOCK_DUSK_BG_TOP, (200, 80, 110), CLOCK_DUSK_BG_BOT)
        digit_color = WHITE
        colon_color = YELLOW
        date_color  = (255, 230, 200)
    else:
        bg = gradient_bg(CLOCK_DAY_BG_TOP, (255, 200, 90), CLOCK_DAY_BG_BOT)
        digit_color = CLOCK_DIGIT_DAY
        colon_color = CLOCK_COLON_DAY
        date_color  = CLOCK_DATE_DAY

    draw = ImageDraw.Draw(bg)
    draw.fontmode = "1"

    # HH and MM drawn separately so the colon can be a different color
    hh = berlin.strftime("%H")
    mm = berlin.strftime("%M")
    colon = ":" if (int(t * 2) % 2 == 0) else " "

    # Measure for layout
    hw, hh_, hox, hoy = text_bbox(FONT_CLOCK, hh)
    mw, mh_, mox, moy = text_bbox(FONT_CLOCK, mm)
    cw, ch_, cox, coy = text_bbox(FONT_CLOCK, ":")
    total = hw + cw + mw + 2  # tiny gap
    start_x = (PANEL_W - total) // 2

    y_digit = -1
    draw_text_outline(draw, start_x + hox, y_digit, hh, FONT_CLOCK, digit_color)
    draw_text_outline(draw, start_x + hw + cox + 1, y_digit, colon,
                      FONT_CLOCK, colon_color)
    draw_text_outline(draw, start_x + hw + cw + mox + 2, y_digit, mm,
                      FONT_CLOCK, digit_color)

    # Date row
    date_str = WEEKDAYS_DE[berlin.weekday()] + " " + berlin.strftime("%d.%m.")
    draw_centered_outlined(draw, PANEL_W // 2, 22, date_str,
                           FONT_SMALL, date_color, outline=(0, 0, 0, 80))
    return bg


# ============================================================================
# App: Weather (vivid sky-gradient + colored pixel-art icons)
# ============================================================================

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=52.5200&longitude=13.4050"
    "&current=temperature_2m,weather_code,is_day,wind_speed_10m"
    "&daily=temperature_2m_max,temperature_2m_min"
    "&timezone=Europe/Berlin"
)
WEATHER_CACHE_TTL = 600
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


def temp_color(t):
    """Map a temperature in °C to a vivid color along blue->white->red."""
    if t <= -5:  return COLD_BLUE
    if t <= 5:   return COOL_CYAN
    if t <= 15:  return NEUTRAL
    if t <= 22:  return WARM
    return HOT_RED


def _putpx(img, x, y, color):
    if 0 <= x < PANEL_W and 0 <= y < PANEL_H:
        img.putpixel((x, y), color)


def draw_sun(img, cx, cy, t=0.0):
    """Draw a 14x14 sun with rotating-ray look (frame-based)."""
    # Body
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            if dx*dx + dy*dy <= 9:
                _putpx(img, cx+dx, cy+dy, SUN_CORE)
            elif dx*dx + dy*dy <= 14:
                _putpx(img, cx+dx, cy+dy, SUN_HALO)
    # 8 rays around, rotating
    phase = (t * 1.4) % (2 * math.pi)
    for i in range(8):
        a = phase + i * math.pi / 4
        r1, r2 = 5, 6 + (i % 2)
        x1, y1 = cx + int(round(r1 * math.cos(a))), cy + int(round(r1 * math.sin(a)))
        x2, y2 = cx + int(round(r2 * math.cos(a))), cy + int(round(r2 * math.sin(a)))
        _putpx(img, x1, y1, SUN_HALO)
        _putpx(img, x2, y2, SUN_CORE)


def draw_moon(img, cx, cy):
    """Crescent moon."""
    for dy in range(-5, 6):
        for dx in range(-5, 6):
            if dx*dx + dy*dy <= 25:
                _putpx(img, cx+dx, cy+dy, MOON_CORE)
    # Carve out a crescent
    for dy in range(-5, 6):
        for dx in range(-5, 6):
            if (dx-2)**2 + dy*dy <= 22:
                _putpx(img, cx+dx, cy+dy, (0, 0, 0))
    # Small star to the side
    _putpx(img, cx+6, cy-4, STAR_COLOR)
    _putpx(img, cx-5, cy+4, STAR_COLOR)


def draw_cloud(img, cx, cy, light=CLOUD_LIGHT, dark=CLOUD_DARK):
    """Soft cloud roughly 16x8 around (cx, cy)."""
    # Two-tone cloud: dark on bottom, light on top
    for dy in range(-3, 5):
        for dx in range(-8, 9):
            # Blob shape: union of three circles
            in_left  = (dx + 5)**2 + (dy + 0)**2 <= 9
            in_mid   = (dx - 0)**2 + (dy - 1)**2 <= 14
            in_right = (dx - 5)**2 + (dy + 0)**2 <= 9
            in_base  = (-7 <= dx <= 7) and (1 <= dy <= 4)
            if in_left or in_mid or in_right or in_base:
                col = dark if dy >= 2 else light
                _putpx(img, cx+dx, cy+dy, col)


def draw_rain(img, cx, cy, t):
    """Cloud with falling raindrops, drops shift each frame."""
    draw_cloud(img, cx, cy)
    phase = int(t * 6) % 4
    drops = [(-6, 6, RAIN_DROP_DK), (-2, 8, RAIN_DROP), (2, 6, RAIN_DROP_DK),
             (6, 8, RAIN_DROP), (-4, 9, RAIN_DROP), (4, 10, RAIN_DROP_DK)]
    for dx, dy, col in drops:
        _putpx(img, cx+dx, cy+dy + phase, col)
        _putpx(img, cx+dx, cy+dy + phase - 1, col)


def draw_snow(img, cx, cy, t):
    draw_cloud(img, cx, cy)
    phase = (int(t * 4) % 4)
    for dx, dy in [(-5, 6), (-1, 8), (3, 6), (5, 9), (-3, 9), (1, 11)]:
        _putpx(img, cx+dx, cy+dy + phase, SNOW_FLAKE)


def draw_thunder(img, cx, cy, t):
    draw_cloud(img, cx, cy, light=CLOUD_DARK, dark=(110, 115, 140))
    # Lightning bolt
    bolt = [(0,5),(-1,6),(-2,7),(-1,7),(0,8),(-1,9),(-2,10),(1,7),(2,7)]
    flash_on = (int(t * 3) % 3) != 0
    color = LIGHTNING_HOT if flash_on else LIGHTNING
    for dx, dy in bolt:
        _putpx(img, cx+dx, cy+dy, color)


def draw_fog(img, cx, cy):
    for i, y in enumerate(range(cy-4, cy+6, 2)):
        for x in range(cx-7, cx+8):
            if (x + i) % 2 == 0:
                _putpx(img, x, y, FOG_LINE)


def render_weather(_t=None):
    t = _t if _t is not None else time.time()
    berlin = berlin_now(t)
    hr = berlin.hour
    top, mid, bot, is_night_palette, is_dusk = sky_palette_for_hour(hr)

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
        print("[warn] weather fetch failed:", e)
        bg = gradient_bg(SKY_STORM_TOP, SKY_STORM_MID, SKY_STORM_BOT)
        d = ImageDraw.Draw(bg); d.fontmode = "1"
        draw_centered_outlined(d, PANEL_W // 2, 12, "?", FONT_BIG, WHITE)
        return bg

    # Force night palette when API says it is not day
    if not is_day:
        top_, mid_, bot_ = SKY_NIGHT_TOP, SKY_NIGHT_MID, SKY_NIGHT_BOT
    else:
        top_, mid_, bot_ = top, mid, bot

    # Override palette for storm/snow
    if code in (95, 96, 99):
        bg = gradient_bg(SKY_STORM_TOP, SKY_STORM_MID, SKY_STORM_BOT)
    elif code in (71, 73, 75, 77, 85, 86):
        bg = gradient_bg((180, 195, 220), (210, 220, 235), (235, 240, 250))
    else:
        bg = gradient_bg(top_, mid_, bot_)
        if not is_day:
            d_stars = ImageDraw.Draw(bg)
            for sx, sy in [(8, 4), (24, 2), (42, 5), (52, 9), (58, 3), (4, 11)]:
                d_stars.point((sx, sy), fill=STAR_COLOR)

    # Draw icon on the LEFT (center 13, 16)
    if code == 0 and is_day:
        draw_sun(bg, 13, 16, t)
    elif code == 0 and not is_day:
        draw_moon(bg, 13, 14)
    elif code in (1, 2):
        if is_day:
            draw_sun(bg, 9, 12, t)
        else:
            draw_moon(bg, 9, 12)
        draw_cloud(bg, 16, 18)
    elif code == 3:
        draw_cloud(bg, 13, 16)
    elif code in (45, 48):
        draw_fog(bg, 13, 16)
    elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        draw_rain(bg, 13, 13, t)
    elif code in (71, 73, 75, 77, 85, 86):
        draw_snow(bg, 13, 13, t)
    elif code in (95, 96, 99):
        draw_thunder(bg, 13, 13, t)
    else:
        draw_cloud(bg, 13, 16)

    # Temperature (right half)
    draw = ImageDraw.Draw(bg); draw.fontmode = "1"
    temp_str = str(temp) + "\xb0"
    t_color = temp_color(temp)
    cx_right = 28 + (PANEL_W - 28) // 2
    draw_centered_outlined(draw, cx_right, 0, temp_str, FONT_BIG, t_color)
    # Min/Max
    minmax = str(min_t) + "/" + str(max_t) + "\xb0"
    draw_centered_outlined(draw, cx_right, 20, minmax, FONT_SMALL, NEUTRAL)
    return bg


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
        print("[warn] app render failed:", e)
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
        raise SystemExit("flask_not_installed")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
