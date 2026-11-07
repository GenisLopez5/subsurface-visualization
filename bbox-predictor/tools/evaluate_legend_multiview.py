#!/usr/bin/env python3
"""Evaluate the experimental local-view retry with live legend weights."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from bbox_detector.common import write_json
from bbox_detector.evaluation import evaluate_manifest
from bbox_detector.legend_inference import UltralyticsLegendDetector
from bbox_detector.legend_pipeline import LegendPolicy, apply_legend_policy


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--base-predictions",required=True)
    parser.add_argument("--weights",required=True)
    parser.add_argument("--policy",default="docs/legend_multiview_policy.json")
    parser.add_argument("--output",default="artifacts/legend_multiview_probe/evaluation")
    parser.add_argument("--reference-report",default=None)
    parser.add_argument("--device",default="cpu")
    parser.add_argument("--previews",action="store_true")
    args=parser.parse_args(argv)
    policy=LegendPolicy.load(args.policy)
    detector=UltralyticsLegendDetector(args.weights)
    root=Path(args.base_predictions)
    def predict(path,**_kwargs):
        base=json.loads((root/f"{Path(path).stem}.json").read_text())
        diagnostics={}
        result=apply_legend_policy(path,base,policy,detector=detector,device=args.device,diagnostics=diagnostics)
        return {**result,"diagnostics":diagnostics}
    report=evaluate_manifest(args.manifest,None,args.output,predictor=predict,imgsz=policy.imgsz,
                              conf=policy.confidence,device=args.device,save_previews=args.previews)
    values=[d["metrics"]["regions"]["lithology_legend"]["iou"] for d in report["documents"]]
    summary={"legend_iou_mean":sum(values)/len(values),"legend_iou_threshold_counts":{
        str(t):sum(v>=t for v in values) for t in (.8,.9,.95,.98)},"documents":len(values),
        "development_only":True,"runtime_seconds":report["summary"]["runtime_seconds"]}
    if args.reference_report:
        reference=json.loads(Path(args.reference_report).read_text())
        before={d["image_name"]:d for d in reference["documents"]}
        if set(before)!={d["image_name"] for d in report["documents"]}:
            raise ValueError("Reference and experiment must contain the same documents")
        summary["per_document_delta"]={d["image_name"]:
            d["metrics"]["regions"]["lithology_legend"]["iou"]-
            before[d["image_name"]]["metrics"]["regions"]["lithology_legend"]["iou"] for d in report["documents"]}
    write_json(Path(args.output)/"multiview_summary.json",summary)
    print(json.dumps(summary,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
