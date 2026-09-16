"""Spoiler-free share image: neon mark, poster type, score."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from gorkle import copy
from gorkle.store import Game

SIZE = 1024
GREEN = (118, 255, 140)
WHITE = (244, 242, 236)
MUTED = (168, 176, 168)
ASSETS = Path(__file__).resolve().parent / "assets"
BASE_IMAGE = ASSETS / "grokkler.png"
FONTS = ASSETS / "fonts"
_BUNDLED = {
    "display_bold": FONTS / "SpaceGrotesk-Bold.ttf",
    "display_medium": FONTS / "SpaceGrotesk-Medium.ttf",
    "mono_bold": FONTS / "SpaceMono-Bold.ttf",
    "mono": FONTS / "SpaceMono-Regular.ttf",
}
_FALLBACK = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def cache_path(db_path: Path, game: Game) -> Path:
    return db_path.parent / "share_cards" / f"{game.date}_{game.user_id}.jpg"


def _font(size: int, *, key: str = "display_bold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _BUNDLED.get(key)
    if path is not None and path.exists():
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            pass
    for candidate in _FALLBACK:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _measure(font: ImageFont.ImageFont, text: str) -> tuple[float, float]:
    bbox = font.getbbox(text)
    return float(bbox[2] - bbox[0]), float(bbox[3] - bbox[1])


def _tracked_width(font: ImageFont.ImageFont, text: str, tracking: float) -> float:
    if not text:
        return 0.0
    return sum(_measure(font, ch)[0] for ch in text) + tracking * (len(text) - 1)


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    text: str,
    center: tuple[float, float],
    font: ImageFont.ImageFont,
    fill: tuple[int, ...],
    tracking: float,
) -> None:
    x = center[0] - _tracked_width(font, text, tracking) / 2
    y = center[1]
    for ch in text:
        w, _ = _measure(font, ch)
        draw.text((x + w / 2, y), ch, font=font, fill=fill, anchor="mm")
        x += w + tracking


def _load_base() -> Image.Image:
    if BASE_IMAGE.exists():
        src = Image.open(BASE_IMAGE).convert("RGB")
        return ImageOps.fit(src, (SIZE, SIZE), Image.Resampling.LANCZOS)
    return Image.new("RGB", (SIZE, SIZE), (6, 10, 8))


def _poster_grade(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    shade = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    top = int(SIZE * 0.50)
    for y in range(top, SIZE):
        t = (y - top) / max(SIZE - top, 1)
        alpha = int(10 + 220 * (t**1.45))
        draw.line([(0, y), (SIZE, y)], fill=(0, 0, 0, min(alpha, 228)))
    inset = 36
    draw.rectangle(
        [inset, inset, SIZE - inset, SIZE - inset],
        outline=GREEN + (48,),
        width=1,
    )
    return Image.alpha_composite(rgba, shade)


def _glow_text(
    img: Image.Image,
    text: str,
    xy: tuple[float, float],
    font: ImageFont.ImageFont,
    *,
    fill: tuple[int, int, int] = GREEN,
    radius: int = 22,
) -> Image.Image:
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.text(xy, text, font=font, fill=fill + (160,), anchor="mm")
    glow = glow.filter(ImageFilter.GaussianBlur(radius))
    return Image.alpha_composite(img, glow)


def compose_share_card(game: Game, background: bytes | None = None) -> bytes:
    del background
    img = _poster_grade(_load_base())
    score = copy.share_score_line(game)
    kicker = "SOLVED" if game.status == "won" else "UNSOLVED"
    meta = f"{kicker}  ·  {copy.day_label(game.date).upper()}"
    wordmark_font = _font(28, key="display_medium")
    score_font = _font(140, key="mono_bold")
    meta_font = _font(18, key="mono")
    handle_font = _font(16, key="display_medium")

    img = _glow_text(img, score, (SIZE / 2, 842), score_font, fill=GREEN, radius=32)
    draw = ImageDraw.Draw(img)
    _draw_tracked(draw, "PLAYGORKLE", (SIZE / 2, 724), wordmark_font, WHITE, 6)
    draw.text((SIZE / 2, 842), score, font=score_font, fill=GREEN, anchor="mm")
    draw.text((SIZE / 2, 930), meta, font=meta_font, fill=MUTED, anchor="mm")
    draw.text((SIZE / 2, 976), copy.HANDLE, font=handle_font, fill=WHITE, anchor="mm")

    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=93, optimize=True)
    return out.getvalue()
