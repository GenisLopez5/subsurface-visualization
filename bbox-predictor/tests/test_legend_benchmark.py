from pathlib import Path

from PIL import Image
import pytest

from bbox_detector.common import write_json
from bbox_detector.legend_benchmark import benchmark_legend_policies, legend_summary


def test_grid_preserves_better_baseline_and_unchanged_columns(tmp_path):
    image = tmp_path/"page.tif"
    Image.new("RGB", (100,400), "white").save(image)
    boxes = {"depth_column":[5,120,10,350],"interpreted_lithology_column":[40,120,60,350],
             "lithology_legend":[0,20,90,80]}
    payload = {"image_name":image.name,"width":100,"height":400,"bounding_boxes":boxes}
    write_json(tmp_path/"annotation.json",payload)
    write_json(tmp_path/"base/page.json",payload)
    write_json(tmp_path/"specialist/page.json",{**payload,"diagnostics":{"legend_specialist":{"groups":[
        {"best_box":[20,20,60,80],"best_confidence":.8},
    ]}}})
    write_json(tmp_path/"manifest.json",{"records":[{
        "image_name":image.name,"image_path":str(image),"annotation_path":str(tmp_path/"annotation.json"),
        "width":100,"height":400,"group":"held-out","split":"val",
    }]})
    result = benchmark_legend_policies(tmp_path/"manifest.json",tmp_path/"base",tmp_path/"specialist",
                                       tmp_path/"out",save_previews=False)
    assert result["selected_strategy"] == "baseline"
    assert len([name for name in result["policies"] if name.startswith("fragments_c")]) == 6
    assert result["selected"]["iou_mean"] == 1
    assert not result["regressions_vs_previous"]


def test_summary_includes_missing_legends_and_format_breakdown():
    from bbox_detector.evaluation import aggregate_metrics, document_metrics
    boxes={"lithology_legend":[0,0,100,30]}
    docs=[]
    for name,predicted in (("one.tif",boxes),("two.tif",{})):
        docs.append({"image_name":name,"metrics":document_metrics(boxes,predicted),
                     "runtime_seconds":0,"errors":[]})
    report={"documents":docs,"summary":aggregate_metrics(docs)}
    summary=legend_summary(report,{"one.tif":{"layout":"wide","frame":"rounded"}})
    assert summary["iou_mean"]==.5 and summary["missing"]==1
    assert summary["iou_ge_0_9"]==1
    assert summary["formats"]["layout"]["unreviewed"]["missing"]==1
