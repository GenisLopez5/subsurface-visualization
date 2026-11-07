"""Runtime settings, from command-line flags or environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _paths(value: str | None) -> list[Path]:
    return [Path(p).expanduser().resolve() for p in (value or "").split(os.pathsep) if p]


@dataclass
class Settings:
    # Where existing bounding-box JSONs are looked up for an uploaded image (until automatic
    # detection exists). Several folders can be given.
    annotation_dirs: list[Path] = field(default_factory=list)
    # Where images are looked up when only a JSON is uploaded (UI convenience).
    image_dirs: list[Path] = field(default_factory=list)
    # Working folder: uploads, extraction cache, temp files handed to the drawing code.
    data_dir: Path = Path("lithology-data").resolve()
    # Optional dataset split file (dataset/splits/split.json) to tag logs as train/test in the UI.
    split_file: Path | None = None
    # Optional folder with the web UI (index.html) to serve at "/".
    ui_dir: Path | None = None
    # Enable endpoints that act on the server's desktop (folder dialog, open file in a viewer).
    # Only makes sense when the API runs on the user's own machine.
    desktop: bool = False

    @property
    def uploads(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def cache(self) -> Path:
        return self.data_dir / "cache"

    @property
    def tmp(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self):
        for d in (self.uploads, self.cache, self.tmp):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        """LITHO_ANNOTATION_DIRS, LITHO_IMAGE_DIRS (os.pathsep-separated), LITHO_DATA_DIR,
        LITHO_SPLIT_FILE, LITHO_UI_DIR, LITHO_DESKTOP=1"""
        e = os.environ.get
        return cls(
            annotation_dirs=_paths(e("LITHO_ANNOTATION_DIRS")),
            image_dirs=_paths(e("LITHO_IMAGE_DIRS")),
            data_dir=Path(e("LITHO_DATA_DIR", "lithology-data")).expanduser().resolve(),
            split_file=Path(e("LITHO_SPLIT_FILE")).expanduser().resolve() if e("LITHO_SPLIT_FILE") else None,
            ui_dir=Path(e("LITHO_UI_DIR")).expanduser().resolve() if e("LITHO_UI_DIR") else None,
            desktop=e("LITHO_DESKTOP", "") in ("1", "true", "yes"),
        )
