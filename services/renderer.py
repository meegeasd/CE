"""
Renderer — High-quality ticker image renderer for Capital Expert Bot.

Key quality features:
- 2x supersampling: render at 2160px, downscale to 1080px (sharp text)
- Auto font selection: Vazirmatn (libraqm) or Amiri (fallback)
- Only show cards for symbols we actually have (no zero prices)
- Clean, high-contrast black-gold design
"""
from __future__ import annotations
import os
import math
from datetime import datetime
from typing import Optional, Dict, Any
from PIL import Image, ImageDraw, ImageFont, features as pil_features
import jdatetime

try:
    from arabic_reshaper import ArabicReshaper
    from bidi.algorithm import get_display
    _RESHAPER_AVAILABLE = True
except ImportError:
    _RESHAPER_AVAILABLE = False

# ─── Paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
FONT_DIR = os.path.join(PROJECT_ROOT, "fonts")
LOGO_PATH = os.path.join(PROJECT_ROOT, "assets", "logo.png")

FONT_REG_VAZIR = os.path.join(FONT_DIR, "Vazirmatn-Regular.ttf")
FONT_BOLD_VAZIR = os.path.join(FONT_DIR, "Vazirmatn-Bold.ttf")
FONT_BLACK_VAZIR = os.path.join(FONT_DIR, "Vazirmatn-Black.ttf")

# ─── Font selection ──────────────────────────────────────────────────────
# ALWAYS use Vazirmatn — it has BOTH:
#   - OpenType GSUB table (for libraqm/HarfBuzz shaping)
#   - Arabic Presentation Forms (for fallback rendering without libraqm)
# This means connected letters on ALL systems, with or without libraqm.
HAS_RAQM = pil_features.check("raqm")
_F_REG, _F_BOLD, _F_BLACK = FONT_REG_VAZIR, FONT_BOLD_VAZIR, FONT_BLACK_VAZIR
_FONT_FAMILY = "Vazirmatn"

# Reshaper for no-libraqm fallback (produces Presentation Forms → connected letters)
if not HAS_RAQM and _RESHAPER_AVAILABLE:
    # Use DEFAULT reshaper (not config_for_true_type_font) — it produces
    # Presentation Forms which Vazirmatn has, ensuring connected letters.
    _resh_r = ArabicReshaper()
    _resh_b = ArabicReshaper()
    _resh_k = ArabicReshaper()
    import warnings
    warnings.filterwarnings("ignore", message="libraqm is NOT available")

def _fa(text: str, weight: str = "b") -> str:
    """Prepare Persian text.
    With libraqm: return as-is (HarfBuzz shapes at render time).
    Without libraqm: reshape to Presentation Forms + bidi → connected letters.
    """
    if not text:
        return ""
    text = str(text)
    if HAS_RAQM:
        return text  # HarfBuzz handles shaping + bidi
    if not _RESHAPER_AVAILABLE:
        return text  # Best effort (may be disconnected, but won't crash)
    # Reshape to Presentation Forms (connected letter forms)
    # Vazirmatn has these glyphs, so Pillow renders them correctly.
    return get_display(_resh_b.reshape(text))

def _dir() -> Optional[str]:
    return "rtl" if HAS_RAQM else None

# ─── Colors ──────────────────────────────────────────────────────────────
GOLD = (212, 175, 55)
GOLD_BRIGHT = (255, 215, 0)
GOLD_DARK = (148, 116, 26)
GOLD_SOFT = (90, 70, 25)
BLACK_BG = (10, 10, 12)
BLACK_CARD = (22, 20, 22)
BLACK_FOCAL = (28, 22, 8)
WHITE = (245, 245, 245)
GREEN = (46, 204, 113)
RED = (231, 76, 60)
GRAY = (140, 140, 145)
GRAY_DIM = (90, 90, 95)

# ─── Font cache ──────────────────────────────────────────────────────────
_FONTS = {}
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(path, size)
    return _FONTS[key]

# ─── Persian digits ──────────────────────────────────────────────────────
_FA_DIG = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
def _fa_digits(v) -> str:
    return str(v).translate(_FA_DIG)

def _fmt_price(value, unit: str = "") -> str:
    """Format price. Toman prices show NO unit (it's implied for Iranian users).
    Dollar prices show $ prefix."""
    try:
        v = float(value)
    except:
        v = 0.0
    if abs(v) >= 1000:
        s = f"{v:,.0f}"
    elif abs(v) >= 1:
        s = f"{v:,.2f}"
    else:
        s = f"{v:,.4f}".rstrip("0").rstrip(".")
    out = _fa_digits(s)
    # Only show "$" for dollar prices; "تومان" is implied (don't clutter)
    if unit == "$":
        return "$" + out
    return out  # Toman or empty: just the number

def _fmt_chg(pct):
    if pct is None:
        return ("—", GRAY)
    arrow = "▲" if pct >= 0 else "▼"
    color = GREEN if pct >= 0 else RED
    return (f"{arrow} {_fa_digits(f'{abs(pct):.2f}')}٪", color)

# ─── Drawing helpers ─────────────────────────────────────────────────────
def _text_right(draw, text, x_right, y, font_obj, fill):
    prepared = _fa(text)
    d = _dir()
    bbox = draw.textbbox((0, 0), prepared, font=font_obj, direction=d)
    tw = bbox[2] - bbox[0]
    draw.text((x_right - tw, y), prepared, font=font_obj, fill=fill, direction=d)

def _text_center(draw, text, cx, cy, font_obj, fill):
    prepared = _fa(text)
    d = _dir()
    bbox = draw.textbbox((0, 0), prepared, font=font_obj, direction=d)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - tw/2 - bbox[0], cy - th/2 - bbox[1]), prepared, font=font_obj, fill=fill, direction=d)

def _vignette(draw, W, H):
    for i in range(1, 6):
        c = 14 + i
        draw.rectangle([i, i, W-1-i, H-1-i], outline=(c, c-1, c+1))

# ─── Logo ────────────────────────────────────────────────────────────────
def _load_logo(size: int = 56) -> Image.Image:
    if not os.path.exists(LOGO_PATH):
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    im = Image.open(LOGO_PATH).convert("RGBA")
    w, h = im.size
    return im.resize((int(size * w / h), size), Image.LANCZOS)

# ─── Symbol ordering ─────────────────────────────────────────────────────
# Only show symbols we actually have prices for.
# Order: focal first (USD, GOLD18), then by priority.
SYMBOLS = [
    ("USD", "دلار", "USD", "تومان", "tgju", 1),
    ("GOLD18", "طلای ۱۸ عیار", "GOLD18", "تومان", "tgju", 1),
    ("USDT", "تتر", "USDT", "تومان", "tgju", 2),
    ("EUR_TMN", "یورو", "EUR", "تومان", "tgju", 2),
    ("GBP_TMN", "پوند", "GBP", "تومان", "tgju", 2),
    ("GOLD_MES", "مثقال طلا", "MES", "تومان", "tgju", 2),
    ("SEKE_NEW", "سکه جدید", "SEKE", "تومان", "tgju", 3),
    ("XAU", "انس طلا", "XAU", "$", "yahoo", 2),
    ("XAG", "انس نقره", "XAG", "$", "yahoo", 3),
    ("OIL_WTI", "نفت WTI", "WTI", "$", "yahoo", 2),
    ("OIL_BRENT", "نفت برنت", "BRENT", "$", "yahoo", 2),
    ("EURUSD", "یورو/دلار", "EUR/USD", "$", "yahoo", 3),
    ("GBPUSD", "پوند/دلار", "GBP/USD", "$", "yahoo", 3),
    ("NAS100", "نزدک ۱۰۰", "NAS100", "$", "yahoo", 2),
    ("US30", "داو جونز", "US30", "$", "yahoo", 2),
    ("BTC", "بیت‌کوین", "BTC", "$", "coingecko", 2),
    ("ETH", "اتریوم", "ETH", "$", "coingecko", 2),
    ("SOL", "سولانا", "SOL", "$", "coingecko", 3),
    ("NVDA", "انویدیا", "NVDA", "$", "yahoo", 3),
    ("AAPL", "اپل", "AAPL", "$", "yahoo", 3),
    ("MSFT", "مایکروسافت", "MSFT", "$", "yahoo", 3),
]

def _ordered_codes(prices: Dict[str, Any]) -> list[str]:
    """Return codes in display order, only for symbols we have."""
    have = set(prices.keys())
    # Sort by (priority, code) — priority 1 first
    ordered = [s[0] for s in sorted(SYMBOLS, key=lambda s: (s[5], s[0])) if s[0] in have]
    return ordered

# ─── Header ──────────────────────────────────────────────────────────────
def _draw_header(draw, W, now_jalali, now_local) -> int:
    M = 40
    d = _dir()
    # Title (right-aligned)
    _text_right(draw, "تابلوی قیمتی لحظه‌ای", W - M, 38, _font(_F_BLACK, 42), GOLD_BRIGHT)
    # Subtitle
    _text_right(draw, "کپیتال اکسپرت", W - M, 88, _font(_F_REG, 18), GOLD)
    # Date/time (left)
    val_date = _fa_digits(now_jalali.strftime("%Y/%m/%d"))
    val_time = _fa_digits(now_local.strftime("%H:%M"))
    lbl_d, lbl_t = _fa("تاریخ", "r"), _fa("ساعت", "r")
    f_lbl, f_val = _font(_F_REG, 16), _font(_F_BOLD, 22)
    draw.text((M, 32), lbl_d, font=f_lbl, fill=GRAY, direction=d)
    bbox = draw.textbbox((0, 0), lbl_d, font=f_lbl, direction=d)
    draw.text((M + (bbox[2] - bbox[0]) + 10, 28), val_date, font=f_val, fill=WHITE, direction=d)
    draw.text((M, 68), lbl_t, font=f_lbl, fill=GRAY, direction=d)
    bbox = draw.textbbox((0, 0), lbl_t, font=f_lbl, direction=d)
    draw.text((M + (bbox[2] - bbox[0]) + 10, 64), val_time, font=f_val, fill=WHITE, direction=d)
    # Separator
    sep_y = 110
    draw.line([(M, sep_y), (W - M, sep_y)], fill=GOLD_DARK, width=2)
    dpx = W - M
    draw.polygon([(dpx, sep_y - 4), (dpx + 5, sep_y), (dpx, sep_y + 4), (dpx - 5, sep_y)], fill=GOLD_BRIGHT)
    return sep_y + 18

# ─── Footer ──────────────────────────────────────────────────────────────
def _draw_footer(img, draw, W, H, handle: str):
    d = _dir()
    logo_size = 56
    lx, ly = 40, H - 40 - logo_size
    logo = _load_logo(logo_size)
    # Gold ring
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    r = logo.size[1] // 2 + 4
    cx, cy = lx + logo.size[0] // 2, ly + logo.size[1] // 2
    od.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD, width=2)
    img.paste(overlay, (0, 0), overlay)
    img.paste(logo, (lx, ly), logo)
    # Name + handle
    tx = lx + logo.size[0] + 16
    draw.text((tx, ly + 2), _fa("کپیتال اکسپرت", "b"), font=_font(_F_BOLD, 26), fill=GOLD_BRIGHT, direction=d)
    draw.text((tx, ly + 34), _fa(handle, "r"), font=_font(_F_REG, 18), fill=GRAY, direction=d)

# ─── Card ────────────────────────────────────────────────────────────────
def _draw_card(draw, x, y, w, h, code, p, focal=False):
    """Card layout:
    FOCAL (USD, GOLD18): HUGE price (visible from distance), large symbol
    NORMAL: Medium price, medium symbol — all same size

    Row 1 (top):    symbol (left) + Persian name (right)
    Row 2 (middle): price (left) + change% (right, same line)
    Row 3 (bottom): toman equivalent (if $ price)
    """
    d = _dir()
    name_fa = p.get("name_fa", code)
    name_en = p.get("name_en", code)
    price = p.get("price", 0)
    unit = p.get("unit", "")
    chg = p.get("change_pct")
    toman = p.get("toman_price")

    border = GOLD if focal else GOLD_SOFT
    bg = BLACK_FOCAL if focal else BLACK_CARD
    bw = 2 if focal else 1
    draw.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=bg, outline=border, width=bw)
    draw.rounded_rectangle([x + 1, y + 1, x + w - 1, y + 4], radius=2, fill=GOLD_BRIGHT)

    # Row 1: Symbol (top-left) + Persian name (top-right)
    f_sym = _font(_F_BLACK, 36 if focal else 26)
    draw.text((x + 16, y + 14), name_en, font=f_sym, fill=GOLD_BRIGHT)
    f_name = _font(_F_REG, 22 if focal else 18)
    _text_right(draw, name_fa, x + w - 16, y + 18, f_name, WHITE)

    # Row 2: Price (left) + Change% (right, SAME LINE)
    ps = _fmt_price(price, unit)
    if focal:
        if len(ps) > 14:
            f_price = _font(_F_BLACK, 44)
        elif len(ps) > 10:
            f_price = _font(_F_BLACK, 52)
        else:
            f_price = _font(_F_BLACK, 58)
    else:
        if len(ps) > 16:
            f_price = _font(_F_BLACK, 24)
        elif len(ps) > 11:
            f_price = _font(_F_BLACK, 28)
        else:
            f_price = _font(_F_BLACK, 30)
    draw.text((x + 16, y + 65), ps, font=f_price, fill=WHITE)

    # Change % — right-aligned, same row as price
    chg_str, color = _fmt_chg(chg)
    f_chg = _font(_F_BOLD, 26 if focal else 20)
    _text_right(draw, chg_str, x + w - 16, y + 75, f_chg, color)

    # Row 3: Toman equivalent (for $ prices only)
    if toman and unit == "$":
        ts = _fmt_price(toman)
        f_t = _font(_F_REG, 13 if len(ts) > 20 else 15)
        draw.text((x + 16, y + 120), _fa("معادل: ", "r") + ts, font=f_t, fill=GRAY, direction=d)

# ─── Section banner ──────────────────────────────────────────────────────
def _draw_banner(draw, y, title, icon, W) -> int:
    h = 36
    draw.rounded_rectangle([40, y, W - 40, y + h], radius=8, fill=(40, 32, 8), outline=GOLD_DARK, width=1)
    draw.text((54, y + 8), icon, font=_font(_F_BLACK, 18), fill=GOLD_BRIGHT)
    _text_right(draw, title, W - 54, y + 8, _font(_F_BOLD, 20), GOLD_BRIGHT)
    return y + h + 8

# ─── Main render ─────────────────────────────────────────────────────────
def render_ticker(prices: Dict[str, Any], output_path: str,
                  handle: str = "@CapXpert",
                  now_local: Optional[datetime] = None) -> str:
    """
    Render ticker at 2x resolution (2160px) then downscale to 1080px
    for supersampled sharp text.
    """
    if now_local is None:
        now_local = datetime.now()
    try:
        now_jalali = jdatetime.datetime.fromgregorian(datetime=now_local)
    except:
        now_jalali = jdatetime.datetime.now()

    # ─── Render at 2x ────────────────────────────────────────────────────
    SCALE = 2
    W = 1080 * SCALE  # 2160
    M = 40 * SCALE    # 80
    GAP = 14 * SCALE  # 28
    content_w = W - 2 * M

    # Determine which symbols we have, ordered
    codes = _ordered_codes(prices)
    if not codes:
        # No prices — render error image
        img = Image.new("RGB", (W, 600 * SCALE), BLACK_BG)
        d = ImageDraw.Draw(img)
        _text_center(d, "قیمتی دریافت نشد", W // 2, 300 * SCALE, _font(_F_BLACK, 40 * SCALE), RED)
        img = img.resize((1080, 600), Image.LANCZOS)
        img.save(output_path, "PNG")
        return output_path

    # Separate focal (USD, GOLD18) from the rest
    focal = [c for c in codes if c in ("USD", "GOLD18")]
    others = [c for c in codes if c not in ("USD", "GOLD18")]

    # Categorize others
    meta_by_code = {s[0]: s for s in SYMBOLS}

    # Groups: forex_toman, gold_toman, intl, crypto, stocks
    groups = {
        "ارزها (تومان)": [],
        "طلا و سکه (تومان)": [],
        "نمادهای بین‌المللی": [],
        "ارزهای دیجیتال": [],
        "سهام آمریکا": [],
    }
    for c in others:
        m = meta_by_code.get(c)
        if not m:
            continue
        unit = m[3]
        src = m[4]
        if unit == "تومان" and c in ("USDT", "EUR_TMN", "GBP_TMN"):
            groups["ارزها (تومان)"].append(c)
        elif unit == "تومان" and c in ("GOLD_MES", "SEKE_NEW", "SEKE_OLD", "SEKE_HALF", "SEKE_QUARTER", "SEKE_GRAM"):
            groups["طلا و سکه (تومان)"].append(c)
        elif unit == "$" and c in ("XAU", "XAG", "OIL_WTI", "OIL_BRENT", "EURUSD", "GBPUSD", "NAS100", "US30"):
            groups["نمادهای بین‌المللی"].append(c)
        elif unit == "$" and c in ("BTC", "ETH", "SOL"):
            groups["ارزهای دیجیتال"].append(c)
        elif unit == "$" and c in ("NVDA", "AAPL", "MSFT"):
            groups["سهام آمریکا"].append(c)

    # Calculate height
    card_h = 150 * SCALE
    focal_h = 260 * SCALE
    banner_h = 36 * SCALE + 8 * SCALE
    header_h = 128 * SCALE
    footer_h = 90 * SCALE

    total_h = header_h + 16 * SCALE
    # Focal section (USD + GOLD18)
    total_h += focal_h + 20 * SCALE
    # Groups
    for title, group_codes in groups.items():
        if not group_codes:
            continue
        total_h += banner_h
        rows = (len(group_codes) + 2) // 3
        total_h += rows * card_h + (rows - 1) * GAP + 16 * SCALE
    total_h += footer_h

    H = total_h

    # Create 2x image
    img = Image.new("RGB", (W, H), BLACK_BG)
    draw = ImageDraw.Draw(img)
    _vignette(draw, W, H)

    y = _draw_header_2x(draw, W, M, now_jalali, now_local, SCALE)

    # Focal section: USD + GOLD18 side by side
    if focal:
        half_w = (content_w - GAP) // 2
        if "USD" in focal:
            _draw_card_2x(draw, M, y, half_w, focal_h, "USD", prices["USD"], focal=True, scale=SCALE)
        if "GOLD18" in focal:
            _draw_card_2x(draw, M + half_w + GAP, y, half_w, focal_h, "GOLD18", prices["GOLD18"], focal=True, scale=SCALE)
        y += focal_h + 20 * SCALE

    # Groups
    for title, group_codes in groups.items():
        if not group_codes:
            continue
        y = _draw_banner_2x(draw, y, title, "◆", W, M, SCALE)
        n = len(group_codes)
        rows = (n + 2) // 3
        for row_idx in range(rows):
            row_codes = group_codes[row_idx * 3: row_idx * 3 + 3]
            rn = len(row_codes)
            card_w = (content_w - (rn - 1) * GAP) // rn
            for col_idx, code in enumerate(row_codes):
                x = M + col_idx * (card_w + GAP)
                _draw_card_2x(draw, x, y, card_w, card_h, code, prices[code], focal=False, scale=SCALE)
            y += card_h + GAP
        y = y - GAP + 16 * SCALE

    # Footer
    _draw_footer_2x(img, draw, W, H, M, handle, SCALE)

    # ─── Downscale to 1080px with LANCZOS (supersampling) ────────────────
    final_W = 1080
    final_H = int(H / SCALE)
    img_final = img.resize((final_W, final_H), Image.LANCZOS)
    img_final.save(output_path, "PNG", optimize=True)
    return output_path

# ─── 2x versions of drawing functions ────────────────────────────────────
def _draw_header_2x(draw, W, M, now_jalali, now_local, S):
    d = _dir()
    _text_right(draw, "تابلوی قیمتی لحظه‌ای", W - M, 38 * S, _font(_F_BLACK, 42 * S), GOLD_BRIGHT)
    _text_right(draw, "کپیتال اکسپرت", W - M, 88 * S, _font(_F_REG, 18 * S), GOLD)
    val_date = _fa_digits(now_jalali.strftime("%Y/%m/%d"))
    val_time = _fa_digits(now_local.strftime("%H:%M"))
    lbl_d, lbl_t = _fa("تاریخ", "r"), _fa("ساعت", "r")
    f_lbl, f_val = _font(_F_REG, 16 * S), _font(_F_BOLD, 22 * S)
    draw.text((M, 32 * S), lbl_d, font=f_lbl, fill=GRAY, direction=d)
    bbox = draw.textbbox((0, 0), lbl_d, font=f_lbl, direction=d)
    draw.text((M + (bbox[2] - bbox[0]) + 10 * S, 28 * S), val_date, font=f_val, fill=WHITE, direction=d)
    draw.text((M, 68 * S), lbl_t, font=f_lbl, fill=GRAY, direction=d)
    bbox = draw.textbbox((0, 0), lbl_t, font=f_lbl, direction=d)
    draw.text((M + (bbox[2] - bbox[0]) + 10 * S, 64 * S), val_time, font=f_val, fill=WHITE, direction=d)
    sep_y = 110 * S
    draw.line([(M, sep_y), (W - M, sep_y)], fill=GOLD_DARK, width=2 * S)
    dpx = W - M
    draw.polygon([(dpx, sep_y - 4 * S), (dpx + 5 * S, sep_y), (dpx, sep_y + 4 * S), (dpx - 5 * S, sep_y)], fill=GOLD_BRIGHT)
    return sep_y + 18 * S

def _draw_banner_2x(draw, y, title, icon, W, M, S) -> int:
    h = 36 * S
    draw.rounded_rectangle([M, y, W - M, y + h], radius=8 * S, fill=(40, 32, 8), outline=GOLD_DARK, width=1 * S)
    draw.text((M + 14 * S, y + 8 * S), icon, font=_font(_F_BLACK, 18 * S), fill=GOLD_BRIGHT)
    _text_right(draw, title, W - M - 14 * S, y + 8 * S, _font(_F_BOLD, 20 * S), GOLD_BRIGHT)
    return y + h + 8 * S

def _draw_card_2x(draw, x, y, w, h, code, p, focal=False, scale=1):
    d = _dir()
    S = scale
    name_fa = p.get("name_fa", code)
    name_en = p.get("name_en", code)
    price = p.get("price", 0)
    unit = p.get("unit", "")
    chg = p.get("change_pct")
    toman = p.get("toman_price")

    border = GOLD if focal else GOLD_SOFT
    bg = BLACK_FOCAL if focal else BLACK_CARD
    bw = 2 * S if focal else 1 * S
    draw.rounded_rectangle([x, y, x + w, y + h], radius=14 * S, fill=bg, outline=border, width=bw)
    draw.rounded_rectangle([x + 1 * S, y + 1 * S, x + w - 1 * S, y + 4 * S], radius=2 * S, fill=GOLD_BRIGHT)

    f_sym = _font(_F_BLACK, (36 if focal else 26) * S)
    draw.text((x + 16 * S, y + 14 * S), name_en, font=f_sym, fill=GOLD_BRIGHT)
    f_name = _font(_F_REG, (22 if focal else 18) * S)
    _text_right(draw, name_fa, x + w - 16 * S, y + 18 * S, f_name, WHITE)

    ps = _fmt_price(price, unit)
    if focal:
        if len(ps) > 14:
            f_price = _font(_F_BLACK, 44 * S)
        elif len(ps) > 10:
            f_price = _font(_F_BLACK, 52 * S)
        else:
            f_price = _font(_F_BLACK, 58 * S)
    else:
        if len(ps) > 16:
            f_price = _font(_F_BLACK, 24 * S)
        elif len(ps) > 11:
            f_price = _font(_F_BLACK, 28 * S)
        else:
            f_price = _font(_F_BLACK, 30 * S)
    draw.text((x + 16 * S, y + 65 * S), ps, font=f_price, fill=WHITE)

    chg_str, color = _fmt_chg(chg)
    f_chg = _font(_F_BOLD, (26 if focal else 20) * S)
    _text_right(draw, chg_str, x + w - 16 * S, y + 75 * S, f_chg, color)

    if toman and unit == "$":
        ts = _fmt_price(toman)
        f_t = _font(_F_REG, (13 if len(ts) > 20 else 15) * S)
        draw.text((x + 16 * S, y + 120 * S), _fa("معادل: ", "r") + ts, font=f_t, fill=GRAY, direction=d)

def _draw_footer_2x(img, draw, W, H, M, handle, S):
    d = _dir()
    logo_size = 56 * S
    lx, ly = M, H - M - logo_size
    logo = _load_logo(logo_size)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    r = logo.size[1] // 2 + 4 * S
    cx, cy = lx + logo.size[0] // 2, ly + logo.size[1] // 2
    od.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD, width=2 * S)
    img.paste(overlay, (0, 0), overlay)
    img.paste(logo, (lx, ly), logo)
    tx = lx + logo.size[0] + 16 * S
    draw.text((tx, ly + 2 * S), _fa("کپیتال اکسپرت", "b"), font=_font(_F_BOLD, 26 * S), fill=GOLD_BRIGHT, direction=d)
    draw.text((tx, ly + 34 * S), _fa(handle, "r"), font=_font(_F_REG, 18 * S), fill=GRAY, direction=d)

# ─── Standalone test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.path.insert(0, PROJECT_ROOT)
    from services.price_fetcher import fetch_all_prices
    import asyncio
    print(f"Font: {_FONT_FAMILY}, libraqm: {HAS_RAQM}")
    r = asyncio.run(fetch_all_prices())
    print(f"Prices: {r['total']}")
    out = os.path.join(PROJECT_ROOT, "ticker_test.png")
    out = os.path.abspath(out)
    render_ticker(r["prices"], out)
    print(f"Saved: {out} ({os.path.getsize(out) // 1024} KB)")