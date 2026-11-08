import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pytest

from bbox_runtime import BoundingBoxPredictor
from bbox_runtime.common import CLASS_NAMES
from bbox_runtime.inference import InferenceError


@pytest.fixture
def bundle(tmp_path):
    models=tmp_path/"models"
    models.mkdir()
    manifest={"version":1,"models":{}}
    for role,classes in (("general",list(CLASS_NAMES)),("legend",["lithology_legend"])):
        file=models/f"{role}.pt"
        file.write_bytes(role.encode())
        manifest["models"][role]={"file":file.name,"classes":classes,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}
    (models/"manifest.json").write_text(json.dumps(manifest))
    config=tmp_path/"pipeline.json"
    config.write_text(json.dumps({"version":1,"workflow_version":"test",
                                 "general":{"imgsz":1536,"confidence":.25,"overlap":.5},
                                 "policy":{"strategy":"fragments_frame","multiview_retry":True}}))
    image=tmp_path/"document.tif"
    Image.new("RGB",(100,400),"white").save(image)
    payload={"image_name":image.name,"width":100,"height":400,"bounding_boxes":{
        "depth_column":[5,100,15,350],"interpreted_lithology_column":[40,100,55,350],
        "lithology_legend":[0,5,90,40],
    }}
    return models,config,image,payload


def predictor(bundle):
    models,config,_,_=bundle
    return BoundingBoxPredictor(models_dir=models,config_path=config,device="cpu",cpu_threads=None)


def test_both_models_are_loaded_once_and_legend_pipeline_is_always_enabled(bundle):
    _,_,image,payload=bundle
    calls=[]
    def refine(path,base,policy,**kwargs):
        calls.append(policy)
        assert kwargs["detector"] is not None
        return base
    with patch("bbox_runtime.workflow.UltralyticsDetector") as general, \
         patch("bbox_runtime.workflow.UltralyticsLegendDetector") as legend, \
         patch("bbox_runtime.workflow.predict_document",return_value=payload), \
         patch("bbox_runtime.workflow.apply_legend_policy",side_effect=refine):
        instance=predictor(bundle)
        assert instance.predict(image)==payload
        assert instance.predict(image)==payload
        assert general.call_count==legend.call_count==1
    assert len(calls)==2 and all(policy.multiview_retry for policy in calls)


def test_missing_general_legend_is_recovered_without_annotations(bundle):
    _,_,image,payload=bundle
    partial={name:box for name,box in payload["bounding_boxes"].items() if name!="lithology_legend"}
    def refine(path,base,*args,**kwargs):
        assert "lithology_legend" not in base["bounding_boxes"]
        assert base["image_name"]==image.name and base["height"]==400
        return payload
    with patch("bbox_runtime.workflow.UltralyticsDetector"), \
         patch("bbox_runtime.workflow.UltralyticsLegendDetector"), \
         patch("bbox_runtime.workflow.predict_document",side_effect=InferenceError("Missing legend",partial_boxes=partial)), \
         patch("bbox_runtime.workflow.apply_legend_policy",side_effect=refine):
        assert predictor(bundle).predict(image)==payload
    assert not list(image.parent.glob("*annotation*"))


def test_missing_column_is_an_error_not_a_partial_success(bundle):
    _,_,image,_=bundle
    with patch("bbox_runtime.workflow.UltralyticsDetector"), \
         patch("bbox_runtime.workflow.UltralyticsLegendDetector"), \
         patch("bbox_runtime.workflow.predict_document",side_effect=InferenceError("Missing columns")), \
         patch("bbox_runtime.workflow.apply_legend_policy") as refine:
        with pytest.raises(InferenceError,match="Missing columns"):
            predictor(bundle).predict(image)
        refine.assert_not_called()


def test_wrong_or_replaced_model_is_detected_before_loading_torch(bundle):
    models,_,_,_=bundle
    (models/"legend.pt").write_bytes(b"different weights")
    with patch("bbox_runtime.workflow.UltralyticsDetector") as factory:
        with pytest.raises(ValueError,match="checksum"):
            predictor(bundle)
        factory.assert_not_called()


def test_column_refinement_cannot_silently_change_coordinates(bundle):
    _,_,image,payload=bundle
    changed=json.loads(json.dumps(payload))
    changed["bounding_boxes"]["depth_column"][0]=6
    with patch("bbox_runtime.workflow.UltralyticsDetector"), \
         patch("bbox_runtime.workflow.UltralyticsLegendDetector"), \
         patch("bbox_runtime.workflow.predict_document",return_value=payload), \
         patch("bbox_runtime.workflow.apply_legend_policy",return_value=changed):
        with pytest.raises(RuntimeError,match="changed a column"):
            predictor(bundle).predict(image)


@pytest.mark.parametrize("bad_box", [[0,0,0,10],[0,0,101,10],[0.1,0,90,10],[False,0,90,10]])
def test_invalid_final_geometry_is_rejected(bundle,bad_box):
    _,_,image,payload=bundle
    changed=json.loads(json.dumps(payload))
    changed["bounding_boxes"]["lithology_legend"]=bad_box
    with patch("bbox_runtime.workflow.UltralyticsDetector"), \
         patch("bbox_runtime.workflow.UltralyticsLegendDetector"), \
         patch("bbox_runtime.workflow.predict_document",return_value=payload), \
         patch("bbox_runtime.workflow.apply_legend_policy",return_value=changed):
        with pytest.raises(InferenceError,match="Invalid final box"):
            predictor(bundle).predict(image)
