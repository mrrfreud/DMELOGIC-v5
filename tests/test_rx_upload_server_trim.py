from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

from dmelogic.services.rx_upload_server import _scan_enhance


def test_scan_enhance_trims_noisy_phone_background():
    rng = np.random.default_rng(42)
    height = 900
    width = 700
    background = rng.integers(0, 55, size=(height, width), dtype=np.uint8)

    # Add a bright page centered within a noisy dark surround.
    page_left, page_top, page_right, page_bottom = 120, 70, 600, 840
    background[page_top:page_bottom, page_left:page_right] = 242

    image = Image.fromarray(background, mode="L")
    draw = ImageDraw.Draw(image)
    draw.text((170, 180), "Physician Rx", fill=20)
    draw.rectangle((180, 320, 540, 338), outline=35, width=3)

    raw = io.BytesIO()
    image.save(raw, format="PNG")

    result = _scan_enhance(raw.getvalue())

    assert result is not None
    _, trimmed_width, trimmed_height, _ = result
    assert 470 <= trimmed_width <= 520
    assert 760 <= trimmed_height <= 810