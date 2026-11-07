import ast
from pathlib import Path
import subprocess
import sys

from bbox_runtime.workflow import ROOT


def test_runtime_contains_no_training_or_annotation_loading():
    for file in (ROOT/"bbox_runtime").glob("*.py"):
        tree=ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[alias.name for alias in node.names]
                if isinstance(node,ast.ImportFrom): names.append(node.module or "")
                assert not any(name.startswith(("bbox_detector","legend_data","training","evaluation")) for name in names)
            if isinstance(node,ast.FunctionDef):assert node.name not in {"find_dataset","load_annotation"}


def test_entrypoint_works_from_an_unrelated_directory(tmp_path):
    completed=subprocess.run([sys.executable,str(ROOT/"predict.py"),"--help"],cwd=tmp_path,
                              text=True,capture_output=True,check=True)
    assert "--output" in completed.stdout and "--legend-weights" not in completed.stdout
