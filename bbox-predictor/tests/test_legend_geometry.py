import cv2
import numpy as np
from PIL import Image
import pytest

from bbox_detector.legend_geometry import find_legend_frame, legend_band, refine_legend_frame


def test_rectangular_frame_recovers_complete_width_and_heading():
    gray = np.full((260, 500), 255, np.uint8)
    cv2.rectangle(gray, (15, 40), (480, 190), 0, 2)
    cv2.line(gray, (15, 65), (480, 65), 0, 1)
    seed = [130, 42, 350, 188]
    details = {}
    box = find_legend_frame(gray, seed, diagnostics=details)
    assert details["reason"] == "supported_frame"
    assert box[0] < 20 and box[2] > 475
    assert box[1] < 45 and box[3] > 185


def test_rounded_frame_and_distractor_in_another_row():
    image = Image.new("L", (500, 320), "white")
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((15, 100, 480, 220), radius=16, outline="black", width=3)
    draw.rectangle((2, 5, 495, 70), outline="black", width=3)
    box = find_legend_frame(np.asarray(image), [150, 103, 340, 217])
    assert box[0] < 20 and box[2] > 475
    assert 95 < box[1] < 110 and 215 < box[3] < 225


def test_no_frame_preserves_seed_and_rejects_filled_symbol():
    gray = np.full((200, 400), 255, np.uint8)
    gray[75:100, 120:180] = 0
    seed = [90, 40, 250, 150]
    details = {}
    assert find_legend_frame(gray, seed, diagnostics=details) == seed
    assert details["reason"] == "no_supported_frame"


def test_competing_enclosing_frames_preserve_seed():
    gray = np.full((330, 800), 255, np.uint8)
    cv2.rectangle(gray, (20, 50), (500, 250), 0, 2)
    cv2.rectangle(gray, (200, 55), (780, 255), 0, 2)
    seed = [250, 80, 450, 220]
    details = {}
    assert find_legend_frame(gray, seed, diagnostics=details) == seed
    assert details["reason"] == "ambiguous_frames"


@pytest.mark.parametrize("mode", ["1", "P", "RGB"])
def test_native_coordinates_and_tiff_modes(tmp_path, mode):
    from PIL import ImageDraw
    image = Image.new("RGB", (600, 1800), "white")
    ImageDraw.Draw(image).rectangle((20, 200, 575, 350), outline="black", width=3)
    path = tmp_path / "scan.tif"
    image.convert(mode).save(path)
    details = {}
    box = refine_legend_frame(path, [160, 202, 400, 348], column_top=450, diagnostics=details)
    assert box[0] < 25 and box[2] > 570
    assert 190 < box[1] < 210 and 340 < box[3] < 360
    assert details["band"][3] <= 450


def test_band_clips_image_edges_and_rejects_seed_below_columns():
    assert legend_band(100, 200, [0, 0, 100, 30], 60) == (0, 0, 100, 60)
    with pytest.raises(ValueError, match="above"):
        legend_band(100, 200, [0, 100, 100, 150], 90)
