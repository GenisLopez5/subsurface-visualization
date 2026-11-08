import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from bbox_detector.common import write_json
from bbox_detector.legend_experiments import train_band_experiments
from bbox_detector.legend_pipeline import LegendPolicy


def test_two_resolutions_are_sequential_rectangular_and_cannot_promote_a_tie(tmp_path):
    calls = []
    for split in ("train", "val"):
        folder = tmp_path/"dataset/images"/split
        folder.mkdir(parents=True)
        Image.new("RGB", (64,32), "white").save(folder/"tile.png")
    yaml = tmp_path/"dataset/dataset.yaml"
    write_json(yaml, {"path": str(yaml.parent), "train": "images/train", "val": "images/val", "names": ["lithology_legend"]})
    image = tmp_path/"page.tif"
    Image.new("RGB", (100,400), "white").save(image)
    payload = {"image_name": image.name, "width": 100, "height": 400, "bounding_boxes": {
        "depth_column": [5,120,10,350], "interpreted_lithology_column": [40,120,60,350],
        "lithology_legend": [10,30,90,70],
    }}
    write_json(tmp_path/"annotation.json",payload)
    write_json(tmp_path/"base/page.json",payload)
    write_json(tmp_path/"coarse/page.json",{**payload,"diagnostics":{"legend_specialist":{"groups":[]}}})
    write_json(tmp_path/"manifest.json",{"records":[{
        "image_name":image.name,"image_path":str(image),"annotation_path":str(tmp_path/"annotation.json"),
        "width":100,"height":400,"group":"held-out","split":"val",
    }]})
    write_json(tmp_path/"policy.json",{"version":1,"policy":LegendPolicy(strategy="baseline").to_dict()})

    class Model:
        def __init__(self, weights):
            assert weights == "yolo11s.pt"

        def train(self, **kwargs):
            calls.append(kwargs)
            target=Path(kwargs["project"])/kwargs["name"]
            (target/"weights").mkdir(parents=True)
            (target/"weights/best.pt").write_bytes(b"mock")
            self.trainer=SimpleNamespace(save_dir=target)
            return SimpleNamespace(results_dict={})

    class Detector:
        def __init__(self, weights):
            assert Path(weights).is_file()

        def predict(self, image, **kwargs):
            return iter(())

    result=train_band_experiments(yaml,tmp_path/"manifest.json",tmp_path/"base",tmp_path/"coarse",
                                  tmp_path/"policy.json",tmp_path/"runs",device="cpu",
                                  model_factory=Model,detector_factory=Detector)
    assert [call["imgsz"] for call in calls] == [1024,1536]
    for call in calls:
        assert call["rect"] is True and call["epochs"]==60 and call["patience"]==10
        assert call["batch"]==4 and call["seed"]==42 and call["lr0"]==.001
        assert call["mosaic"]==0 and call["fliplr"]==0
    assert result["selected"]=="reference" and result["band_weights"] is None
