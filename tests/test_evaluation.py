from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from bbox_detector.common import CLASS_NAMES
from bbox_detector.evaluation import aggregate_metrics, box_iou, document_metrics, evaluate_manifest, save_comparisons


def three_boxes() -> dict[str, list[int]]:
    return dict(zip(CLASS_NAMES, ([10, 50, 20, 180], [35, 50, 55, 180], [60, 5, 95, 35])))


class DocumentMetricTests(unittest.TestCase):
    def test_exclusive_coordinates_and_disjoint_boxes(self) -> None:
        self.assertEqual(box_iou([0, 0, 10, 10], [10, 0, 20, 10]), 0)
        self.assertAlmostEqual(box_iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)

    def test_perfect_boxes_and_missing_class_penalty(self) -> None:
        truth = three_boxes()
        perfect = document_metrics(truth, truth)
        partial = dict(truth)
        del partial[CLASS_NAMES[1]]
        incomplete = document_metrics(truth, partial)
        self.assertTrue(perfect["all_regions_iou_ge_threshold"])
        self.assertFalse(incomplete["all_regions_iou_ge_threshold"])
        self.assertEqual(incomplete["regions"][CLASS_NAMES[1]]["iou"], 0)
        self.assertIsNone(incomplete["regions"][CLASS_NAMES[1]]["edge_absolute_error_px"])
        rows = [{"metrics": metric, "runtime_seconds": 1, "errors": []} for metric in (perfect, incomplete)]
        summary = aggregate_metrics(rows)
        missing_class = summary["classes"][CLASS_NAMES[1]]
        self.assertEqual(missing_class["iou_mean"], 0.5)
        self.assertEqual(missing_class["iou_p10"], 0.1)
        self.assertEqual(missing_class["missing_rate"], 0.5)
        self.assertEqual(missing_class["edge_error_count"], 1)
        self.assertEqual(summary["all_regions_iou_ge_threshold"]["fraction"], 0.5)

    def test_edge_errors_are_absolute_original_pixels(self) -> None:
        truth = three_boxes()
        predicted = dict(truth)
        predicted[CLASS_NAMES[0]] = [12, 47, 24, 185]
        errors = document_metrics(truth, predicted)["regions"][CLASS_NAMES[0]]["edge_absolute_error_px"]
        self.assertEqual(errors, {"xmin": 2, "ymin": 3, "xmax": 4, "ymax": 5})


class ComparisonTests(unittest.TestCase):
    def test_overview_uses_bounded_reads_and_detail_crops(self) -> None:
        calls = []

        class FakeScan:
            size = (100, 10000)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def read_crop(_scan, box, size=None):
            calls.append((box, size))
            return Image.new("RGB", size, "white")

        with tempfile.TemporaryDirectory() as temporary, patch("bbox_detector.evaluation.open_scan", return_value=FakeScan()), patch("bbox_detector.evaluation.read_crop", side_effect=read_crop):
            truth = dict(zip(CLASS_NAMES, ([10, 400, 20, 9500], [30, 400, 50, 9500], [55, 5, 95, 95])))
            paths = save_comparisons("never-fully-loaded.tif", truth, truth, temporary)
            self.assertEqual(len(paths), 6)
            self.assertTrue((Path(temporary) / "legend.jpg").is_file())
            for path in paths:
                with Image.open(path) as image:
                    self.assertLessEqual(image.width, 1200)
                    self.assertLessEqual(image.height, 1800)
            self.assertGreater(len(calls), 10)
            self.assertTrue(all(box[3] - box[1] <= 120 for box, _ in calls))

    def test_dedicated_legend_contains_tall_boxes_below_header(self) -> None:
        calls = []

        class FakeScan:
            size = (1700, 30000)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def read_crop(_scan, box, size=None):
            calls.append((box, size))
            return Image.new("RGB", size, "white")

        truth = {CLASS_NAMES[2]: [20, 3299, 1680, 8400]}
        predictions = {CLASS_NAMES[2]: [0, 3500, 1600, 9000]}
        with tempfile.TemporaryDirectory() as temporary, patch("bbox_detector.evaluation.open_scan", return_value=FakeScan()), patch("bbox_detector.evaluation.read_crop", side_effect=read_crop):
            paths = save_comparisons("tall-legend.tif", truth, predictions, temporary)
            self.assertEqual(len(paths), 6)
            region, _size = calls[-1]
            for box in (truth[CLASS_NAMES[2]], predictions[CLASS_NAMES[2]]):
                self.assertLessEqual(region[0], box[0])
                self.assertLessEqual(region[1], box[1])
                self.assertGreaterEqual(region[2], box[2])
                self.assertGreaterEqual(region[3], box[3])
            self.assertEqual(region[0], 0)
            self.assertLessEqual(region[2], FakeScan.size[0])
            with Image.open(Path(temporary) / "legend.jpg") as image:
                self.assertLessEqual(image.width, 1200)
                self.assertLessEqual(image.height, 1800)


class EvaluationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.truth = three_boxes()
        self.records = []
        for index in range(2):
            image_name = f"page{index}.tif"
            image_path = self.root / image_name
            Image.new("1", (100, 200), 1).save(image_path)
            annotation_path = self.root / f"annotation{index}.json"
            annotation_path.write_text(json.dumps({"image_name": image_name, "width": 100, "height": 200, "bounding_boxes": self.truth}))
            self.records.append({"image_name": image_name, "image_path": str(image_path), "annotation_path": str(annotation_path), "width": 100, "height": 200, "group": f"well{index}", "split": "val"})
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"dataset_root": str(self.root), "records": self.records}))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_precomputed_missing_document_stays_in_denominator(self) -> None:
        predictions = self.root / "input_predictions"
        predictions.mkdir()
        (predictions / "page0.json").write_text(json.dumps({"image_name": "page0.tif", "width": 100, "height": 200, "bounding_boxes": self.truth}))
        output = self.root / "evaluation"
        report = evaluate_manifest(self.manifest, None, output, predictions_dir=predictions, save_previews=False)
        self.assertEqual(report["prediction_source"], "precomputed")
        self.assertEqual(report["summary"]["documents"], 2)
        self.assertEqual(report["summary"]["all_regions_iou_ge_threshold"]["fraction"], 0.5)
        self.assertEqual(report["summary"]["region_missing_rate"], 0.5)
        self.assertEqual(report["documents"][1]["status"], "error")
        for name in ("report.json", "per_document.csv", "predictions/page0.json", "predictions/page1.json"):
            self.assertTrue((output / name).exists())

    def test_predictor_only_gets_image_and_inference_arguments(self) -> None:
        calls = []

        def fake_predictor(image, **kwargs):
            calls.append((image, kwargs))
            return {"image_name": image.name, "width": 100, "height": 200, "bounding_boxes": self.truth}

        report = evaluate_manifest(self.manifest, "fake.pt", self.root / "evaluation", predictor=fake_predictor, save_previews=True)
        self.assertEqual(report["summary"]["all_regions_iou_ge_threshold"]["fraction"], 1)
        self.assertEqual(len(calls), 2)
        for image, kwargs in calls:
            self.assertEqual(image.suffix, ".tif")
            self.assertEqual(set(kwargs), {"weights", "device", "imgsz", "confidence"})
        self.assertEqual(len(report["documents"][0]["comparison_previews"]), 6)

    def test_inference_error_preserves_partial_boxes_and_scores_missing(self) -> None:
        class FakeInferenceError(RuntimeError):
            partial_boxes = {CLASS_NAMES[0]: self.truth[CLASS_NAMES[0]]}
            diagnostics = {"retries": 1}

        def failing_predictor(*_args, **_kwargs):
            raise FakeInferenceError("Unresolved legend after retry")

        report = evaluate_manifest(self.manifest, None, self.root / "evaluation", predictor=failing_predictor, save_previews=False)
        self.assertEqual(report["summary"]["classes"][CLASS_NAMES[0]]["iou_mean"], 1)
        self.assertEqual(report["summary"]["classes"][CLASS_NAMES[1]]["iou_mean"], 0)
        self.assertEqual(report["documents"][0]["status"], "partial")
        self.assertIn("Unresolved legend", report["documents"][0]["errors"][0])

    def test_wrong_dimensions_and_invalid_coordinates_cannot_score_perfectly(self) -> None:
        predictions = self.root / "input_predictions"
        predictions.mkdir()
        for index in range(2):
            (predictions / f"page{index}.json").write_text(json.dumps({"width": 999, "height": 200, "bounding_boxes": self.truth}))
        report = evaluate_manifest(self.manifest, None, self.root / "evaluation", predictions_dir=predictions, save_previews=False)
        self.assertEqual(report["summary"]["all_regions_iou_ge_threshold"]["fraction"], 0)
        self.assertEqual(report["summary"]["region_missing_rate"], 1)

    def test_invalid_single_box_is_missing_without_discarding_other_regions(self) -> None:
        invalid = dict(self.truth)
        invalid[CLASS_NAMES[0]] = [-1, 50, 20, 180]

        def predictor(image, **_kwargs):
            return {"image_name": image.name, "bounding_boxes": invalid}

        report = evaluate_manifest(self.manifest, None, self.root / "evaluation", predictor=predictor, save_previews=False)
        self.assertEqual(report["summary"]["classes"][CLASS_NAMES[0]]["missing_rate"], 1)
        self.assertEqual(report["summary"]["classes"][CLASS_NAMES[1]]["iou_mean"], 1)
        self.assertEqual(report["summary"]["all_regions_iou_ge_threshold"]["fraction"], 0)

    def test_model_is_loaded_once_for_multiple_documents(self) -> None:
        detector = object()

        def predict(image, **kwargs):
            self.assertIs(kwargs["detector"], detector)
            return {"image_name": image.name, "bounding_boxes": self.truth}

        with patch("bbox_detector.inference.UltralyticsDetector", return_value=detector) as factory, patch("bbox_detector.inference.predict_document", side_effect=predict):
            report = evaluate_manifest(self.manifest, "fake.pt", self.root / "evaluation", save_previews=False)
        factory.assert_called_once_with("fake.pt")
        self.assertEqual(report["summary"]["all_regions_iou_ge_threshold"]["fraction"], 1)


if __name__ == "__main__":
    unittest.main()
