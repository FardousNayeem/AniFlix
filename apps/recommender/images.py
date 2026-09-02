"""Re-encode downloaded artwork to the size the site actually renders.

Cover art arrives at whatever size the licensor supplied. A poster is shown in
a tile about 170px wide, and a banner is shown dimmed behind a gradient with
a headline over it — neither needs the original. Storing it anyway costs disk,
backups, deploy time and, on a slow connection, the viewer's patience.

Pillow is already a dependency of this project, so this adds nothing.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

JPEG_QUALITY = 82


def optimise(data: bytes, max_width: int, *, quality: int = JPEG_QUALITY) -> bytes:
    """Downscale to ``max_width`` and re-encode as progressive JPEG.

    Returns the original bytes untouched if anything goes wrong: a slightly
    large image is a much better outcome than a missing one.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            # Honour the EXIF orientation before resizing, or a rotated
            # original comes out sideways with the rotation flag stripped.
            image = ImageOps.exif_transpose(image)

            if image.width > max_width:
                height = round(image.height * max_width / image.width)
                image = image.resize((max_width, height), Image.LANCZOS)

            if image.mode not in ("RGB", "L"):
                # JPEG has no alpha. Flatten onto the site's own background so
                # a transparent edge does not come out white.
                background = Image.new("RGB", image.size, (13, 20, 36))
                alpha = image.convert("RGBA").split()[-1]
                background.paste(image.convert("RGB"), mask=alpha)
                image = background

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
            return buffer.getvalue()
    except Exception:  # pragma: no cover - Pillow raises a wide variety
        logger.warning("Could not re-encode artwork; keeping the original", exc_info=True)
        return data
