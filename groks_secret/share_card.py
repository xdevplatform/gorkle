"""Spoiler-free share image: PlayGrokkle logo with score overlay."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from groks_secret import copy
from groks_secret.store import Game

SIZE = 1024
PAD = 48
GREEN = (90, 255, 110)
WHITE = (248, 246, 240)
BASE_IMAGE = Path(__file__).resolve().parent / "assets" / "grokkler.png"
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def cache_path(db_path: Path, game: Game) -> Path:
    return db_path.parent / "share_cards" / f"{game.date}_{game.user_id}.jpg"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    *,
    anchor: str = "lt",
) -> None:
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=3, stroke_fill=(0, 0, 0))
    except TypeError:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _load_base() -> Image.Image:
    if BASE_IMAGE.exists():
        src = Image.open(BASE_IMAGE).convert("RGB")
        return ImageOps.fit(src, (SIZE, SIZE), Image.Resampling.LANCZOS)
    return Image.new("RGB", (SIZE, SIZE), (6, 10, 8))


def _bottom_shade(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    shade = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    top = int(SIZE * 0.68)
    for y in range(top, SIZE):
        alpha = int(200 * ((y - top) / max(SIZE - top, 1)))
        draw.line([(0, y), (SIZE, y)], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(rgba, shade).convert("RGB")


def compose_share_card(game: Game, background: bytes | None = None) -> bytes:
    del background  # logo is the card; leftover arg kept for call sites
    img = _bottom_shade(_load_base())
    draw = ImageDraw.Draw(img)
    title_font = _font(42)
    date_font = _font(24)
    score_font = _font(72)
    left_x, bottom = PAD, SIZE - PAD
    _text(draw, (left_x, bottom - 44), "PlayGrokkle", title_font, GREEN, anchor="ls")
    _text(draw, (left_x, bottom), copy.day_label(game.date), date_font, WHITE, anchor="ls")
    _text(draw, (SIZE - PAD, bottom), copy.share_score_line(game), score_font, GREEN, anchor="rs")

    out = BytesIO()
    img.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()
