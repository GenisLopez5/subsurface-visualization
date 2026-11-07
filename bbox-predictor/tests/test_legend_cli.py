import json
from unittest.mock import patch

from PIL import Image

from bbox_detector.common import write_json
from bbox_detector.inference import InferenceError, RawDetection
from predict_bboxes import main


def payload():
    return {"image_name":"page.tif","width":100,"height":300,"bounding_boxes":{
        "depth_column":[5,100,10,270],"interpreted_lithology_column":[40,110,60,280],
    }}


class Detector:
    def __init__(self, weights):
        pass

    def predict(self, image, **kwargs):
        yield RawDetection(0,(image.width*.1,image.height*.1,image.width*.9,image.height*.3),.9)


def test_cli_recovers_missing_legend_and_preserves_cached_columns(tmp_path):
    image=tmp_path/"page.tif"
    Image.new("RGB",(100,300),"white").save(image)
    write_json(tmp_path/"base.json",payload())
    with patch("bbox_detector.legend_inference.UltralyticsLegendDetector",Detector):
        assert main(["--image",str(image),"--base-prediction",str(tmp_path/"base.json"),
                     "--legend-weights",str(tmp_path/"legend.pt"),"--output",str(tmp_path/"out.json")]) == 0
    result=json.loads((tmp_path/"out.json").read_text())
    for name,box in payload()["bounding_boxes"].items():
        assert result["bounding_boxes"][name] == box
    assert "lithology_legend" in result["bounding_boxes"]


def test_cli_continues_from_general_detector_partial_columns(tmp_path):
    image=tmp_path/"page.tif"
    Image.new("RGB",(100,300),"white").save(image)
    with patch("predict_bboxes.predict_document",side_effect=InferenceError("No legend",partial_boxes=payload()["bounding_boxes"])), \
         patch("bbox_detector.legend_inference.UltralyticsLegendDetector",Detector):
        assert main(["--image",str(image),"--weights",str(tmp_path/"base.pt"),
                     "--legend-weights",str(tmp_path/"legend.pt"),"--output",str(tmp_path/"out.json")]) == 0


def test_cli_protects_new_checkpoint_and_configuration_inputs(tmp_path):
    image=tmp_path/"page.tif"
    Image.new("RGB",(100,300),"white").save(image)
    write_json(tmp_path/"base.json",payload())
    weights=tmp_path/"legend.pt"
    weights.write_bytes(b"keep")
    assert main(["--image",str(image),"--base-prediction",str(tmp_path/"base.json"),
                 "--legend-weights",str(weights),"--output",str(weights)]) == 2
    assert weights.read_bytes()==b"keep"
