"""Runtime settings, from command-line flags or environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv():
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return
    path = os.environ.get("LITHO_ENV_FILE") or find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)


def _paths(value: str | None) -> list[Path]:
    return [Path(p).expanduser().resolve() for p in (value or "").split(os.pathsep) if p]


DEFAULT_DETECTOR = "lithology.bbox_detector:detector"


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
    # Postgres (Tiger Data) for the /api/v1 file-management API; None disables those routes.
    database_url: str | None = None
    # Origins allowed to call the API from a browser (the separate frontend). "*" allows any.
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"])
    # Largest accepted upload, per file.
    max_upload_mb: int = 200
    # Automatic bounding-box detector, "module:object" (see annotations.BoxDetector). Default: the int8
    # OpenVINO models of bbox-final. LITHO_DETECTOR=none turns it off (only uploaded/dataset JSONs are used).
    detector: str | None = DEFAULT_DETECTOR

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
        LITHO_SPLIT_FILE, LITHO_UI_DIR, LITHO_DESKTOP=1, DATABASE_URL, LITHO_CORS_ORIGINS (comma-separated),
        LITHO_MAX_UPLOAD_MB, LITHO_DETECTOR. A .env file (LITHO_ENV_FILE, or the nearest .env from the
        current folder upwards) is loaded first; real environment variables win over it."""
        _load_dotenv()
        e = os.environ.get
        return cls(
            annotation_dirs=_paths(e("LITHO_ANNOTATION_DIRS")),
            image_dirs=_paths(e("LITHO_IMAGE_DIRS")),
            data_dir=Path(e("LITHO_DATA_DIR", "lithology-data")).expanduser().resolve(),
            split_file=Path(e("LITHO_SPLIT_FILE")).expanduser().resolve() if e("LITHO_SPLIT_FILE") else None,
            ui_dir=Path(e("LITHO_UI_DIR")).expanduser().resolve() if e("LITHO_UI_DIR") else None,
            desktop=e("LITHO_DESKTOP", "") in ("1", "true", "yes"),
            database_url=e("DATABASE_URL") or None,
            cors_origins=[o.strip() for o in e("LITHO_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
                          if o.strip()],
            max_upload_mb=int(e("LITHO_MAX_UPLOAD_MB", "200")),
            detector=None if e("LITHO_DETECTOR", DEFAULT_DETECTOR).lower() in ("", "none", "0", "off") else e("LITHO_DETECTOR", DEFAULT_DETECTOR),
        )
