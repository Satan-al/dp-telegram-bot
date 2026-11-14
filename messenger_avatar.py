"""Group picture composed from the room's existing profiles."""
import base64
import io
import math
import re

from PIL import Image, ImageDraw, ImageFont

COLOR_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
]


def render_avatar(profiles):
    canvas = Image.new("RGBA", (512, 512), "#16181e")
    draw = ImageDraw.Draw(canvas)
    profiles = profiles[:6]
    count = len(profiles)
    for index, profile in enumerate(profiles):
        angle = 2 * math.pi * index / max(count, 1) - math.pi / 2
        x = 256 + (135 * math.cos(angle) if count > 1 else 0)
        y = 256 + (135 * math.sin(angle) if count > 1 else 0)
        draw.ellipse((x-76, y-76, x+76, y+76), fill="#23262e", outline=profile["color"], width=6)
        emoji = profile.get("emoji", "")
        if re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/=]+", emoji):
            try:
                source = Image.open(io.BytesIO(base64.b64decode(emoji.split(",", 1)[1])))
                if source.width * source.height > 1_048_576:
                    raise ValueError("Avatar is too large")
                image = source.convert("RGBA")
                image.thumbnail((116, 116))
                canvas.alpha_composite(image, (int(x-image.width/2), int(y-image.height/2)))
                continue
            except (OSError, ValueError):
                pass
        painted = False
        if emoji and len(emoji) < 16:
            for path in COLOR_FONTS:
                try:
                    font = ImageFont.truetype(path, 109)
                    box = draw.textbbox((0, 0), emoji, font=font, embedded_color=True)
                    draw.text((x-(box[2]-box[0])/2-box[0], y-(box[3]-box[1])/2-box[1]),
                              emoji, font=font, embedded_color=True)
                    painted = True
                    break
                except (OSError, ValueError):
                    continue
        if not painted:
            font = ImageFont.load_default(size=56)
            letter = profile["name"][:1].upper()
            draw.text((x, y), letter, fill=profile["color"], font=font, anchor="mm")
    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG")
    return output.getvalue()
