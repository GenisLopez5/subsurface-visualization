import json
from unittest.mock import patch

from PIL import Image

from predict import main


def test_stdout_is_only_json_and_file_matches(tmp_path,capsys):
    image=tmp_path/"document.tif"
    Image.new("RGB",(100,400),"white").save(image)
    result={"image_name":image.name,"width":100,"height":400,"bounding_boxes":{
        "depth_column":[5,100,15,350],"interpreted_lithology_column":[40,100,55,350],
        "lithology_legend":[0,5,90,40],
    }}
    class Predictor:
        def __init__(self,**kwargs): print("Third party log")
        def predict(self,path,*,diagnostics):
            diagnostics["ok"]=True
            return result
    output=tmp_path/"result.json"
    with patch("predict.BoundingBoxPredictor",Predictor):
        assert main([str(image),"--output",str(output)])==0
    captured=capsys.readouterr()
    assert json.loads(captured.out)==result
    assert json.loads(output.read_text())==result
    assert "Third party log" in captured.err


def test_failure_has_no_success_json_or_partial_file(tmp_path,capsys):
    image=tmp_path/"document.tif"
    Image.new("RGB",(100,400),"white").save(image)
    with patch("predict.BoundingBoxPredictor",side_effect=RuntimeError("Missing region")):
        assert main([str(image),"--output",str(tmp_path/"result.json")])==2
    assert not (tmp_path/"result.json").exists()
    captured=capsys.readouterr()
    assert not captured.out and "Missing region" in captured.err


def test_cannot_overwrite_input_image(tmp_path):
    image=tmp_path/"document.tif"
    Image.new("RGB",(100,400),"white").save(image)
    before=image.read_bytes()
    assert main([str(image),"--output",str(image)])==2
    assert image.read_bytes()==before
