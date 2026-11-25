"""Configuration and CLI argument parsing for PersonSort."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

IS_FROZEN: bool = bool(getattr(sys, "frozen", False))
PROJECT_DIR: Path = Path(__file__).resolve().parent
APP_RESOURCE_DIR: Path = (
    Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if IS_FROZEN
    else PROJECT_DIR
)
if IS_FROZEN:
    if sys.platform.startswith("win"):
        USER_DATA_DIR: Path = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PersonSort"
        )
    else:
        USER_DATA_DIR = Path.home() / ".personsort"
else:
    USER_DATA_DIR = PROJECT_DIR

MODELS_CACHE_DIR: Path = USER_DATA_DIR / ".models_cache"
DEFAULT_INPUT_DIR: Path = USER_DATA_DIR / "input_photos"
DEFAULT_OUTPUT_DIR: Path = (
    Path.home() / "Pictures" / "PersonSort_Output"
    if IS_FROZEN
    else PROJECT_DIR / "sorted_output"
)


def _load_dotenv_files() -> None:
    """Load key=value pairs from `.env` locations into `os.environ`."""
    candidates: list[Path] = []
    if IS_FROZEN:
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    candidates.extend(
        [
            APP_RESOURCE_DIR / ".env",
            PROJECT_DIR / ".env",
            PROJECT_DIR.parent / ".env",
        ]
    )

    seen: set[Path] = set()
    for env_path in candidates:
        resolved = env_path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            for raw_line in resolved.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)
        except OSError:
            pass


_load_dotenv_files()

APP_VERSION: str = "1.0.0"
GITHUB_RELEASES_REPO: str = os.environ.get(
    "PERSONSORT_GITHUB_REPO", "MicahJaffa/PersonSort"
).strip()

DROPBOX_APP_KEY: str = os.environ.get("PERSONSORT_DROPBOX_APP_KEY", "").strip()
DROPBOX_SHARED_FOLDER_ID: str = os.environ.get("PERSONSORT_DROPBOX_SHARED_FOLDER_ID", "").strip()
DROPBOX_CALLBACK_PATH: str = "/dropbox/callback"
DROPBOX_SETTINGS_PATH: Path = USER_DATA_DIR / ".personsort_dropbox.json"
DROPBOX_KEYRING_SERVICE: str = "PersonSort.Dropbox"
DROPBOX_KEYRING_USERNAME: str = "oauth2_refresh_token"


def _safe_has_glob(directory: Path, pattern: str) -> bool:
    try:
        return directory.is_dir() and any(directory.glob(pattern))
    except OSError:
        return False


def _safe_is_file(file_path: Path) -> bool:
    try:
        return file_path.is_file()
    except OSError:
        return False


def has_downloaded_models(check_jersey: bool = False) -> dict[str, bool]:
    """
    Check whether local AI model weights are already present on disk so first-time
    runs can display a clear 'downloading AI models' notice.
    """
    face_dirs = [
        Path.home() / ".insightface" / "models" / "buffalo_l",
        MODELS_CACHE_DIR / "insightface" / "models" / "buffalo_l",
        APP_RESOURCE_DIR / ".models_cache" / "insightface" / "models" / "buffalo_l",
    ]
    face_ready = any(_safe_has_glob(d, "*.onnx") for d in face_dirs)

    cache_roots = [MODELS_CACHE_DIR, APP_RESOURCE_DIR / ".models_cache"]
    yolo_ready = any(_safe_is_file(r / "yolov8n.pt") for r in cache_roots)
    ocr_ready = any(_safe_has_glob(r / "easyocr", "*.pth") for r in cache_roots)
    jersey_ready = yolo_ready and ocr_ready
    needs_download = (not face_ready) or (bool(check_jersey) and not jersey_ready)
    return {
        "face_ready": face_ready,
        "jersey_ready": jersey_ready,
        "needs_first_time_download": needs_download,
    }


@dataclass
class SortConfig:
    """Tunable parameters for sports photo face grouping and player identification."""

    input_dir: Path = DEFAULT_INPUT_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    reference_dir: Optional[Path] = None

    # Face detection & background spectator filtering
    min_face_size: int = 45          # Minimum face width/height in pixels (ignores tiny crowd faces)
    det_thresh: float = 0.55         # Minimum confidence to keep a detected face

    # Matching & clustering thresholds
    sim_threshold: float = 0.45      # Minimum cosine similarity to match a known reference player
    cluster_eps: float = 0.50        # DBSCAN cosine distance threshold (lower = stricter grouping)
    min_samples: int = 1             # 1 = create folder even for a single appearance; 2+ = group singletons

    # Output & Phase 2 features
    save_crops: bool = True          # Save _reference_crop.jpg inside each player's folder
    enable_jersey_ocr: bool = False  # Enable Milestone 6 full-body + jersey number detection
    use_gpu: bool = True             # Prefer GPU execution provider when available
    self_test: bool = False          # Run built-in end-to-end pipeline self-test
    launch_ui: bool = True           # Start the interactive Web UI immediately by default
    ui_port: int = 8080              # Port for the local Web UI server
    dropbox_app_key: str = DROPBOX_APP_KEY
    dropbox_shared_folder_id: str = DROPBOX_SHARED_FOLDER_ID


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="PersonSort",
        description="Group sports photos by player face, optional reference folders, and jersey numbers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        dest="input_dir",
        help="Input directory containing photos to sort.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        dest="output_dir",
        help="Output directory where per-player folders will be created.",
    )
    parser.add_argument(
        "-r",
        "--reference",
        type=Path,
        default=None,
        dest="reference_dir",
        help="Optional directory of known player reference folders or a previous sorted_output directory.",
    )
    parser.add_argument(
        "--min-face-size",
        type=int,
        default=45,
        help="Minimum face width and height in pixels (filters out tiny background crowd faces).",
    )
    parser.add_argument(
        "--det-thresh",
        type=float,
        default=0.55,
        help="Minimum face detection confidence score (0.0 to 1.0).",
    )
    parser.add_argument(
        "--sim-threshold",
        type=float,
        default=0.45,
        help="Cosine similarity threshold for matching faces to known reference players.",
    )
    parser.add_argument(
        "--cluster-eps",
        type=float,
        default=0.50,
        help="DBSCAN cosine distance threshold for clustering unknown faces (smaller = stricter).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=1,
        help="Minimum photos required to form a cluster (1 = every player gets a folder).",
    )
    parser.add_argument(
        "--no-crops",
        action="store_false",
        dest="save_crops",
        default=True,
        help="Disable saving _reference_crop.jpg preview thumbnails in player folders.",
    )
    parser.add_argument(
        "--enable-jersey",
        action="store_true",
        dest="enable_jersey_ocr",
        default=False,
        help="Enable full-body detection and jersey number OCR (Milestone 6).",
    )
    parser.add_argument(
        "--cpu-only",
        action="store_false",
        dest="use_gpu",
        default=True,
        help="Force CPU execution even if a GPU is available.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        dest="self_test",
        default=False,
        help="Run a built-in end-to-end test of the clustering and folder sorting pipeline.",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        dest="launch_ui",
        default=True,
        help="Launch the interactive Web UI (enabled by default).",
    )
    parser.add_argument(
        "--cli",
        action="store_false",
        dest="launch_ui",
        help="Run in headless command-line mode without starting the Web UI.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        dest="ui_port",
        help="HTTP port for the interactive Web UI server.",
    )
    parser.add_argument(
        "--dropbox-app-key",
        type=str,
        default=DROPBOX_APP_KEY,
        dest="dropbox_app_key",
        help="Dropbox OAuth 2 App Key (PKCE public client).",
    )
    parser.add_argument(
        "--dropbox-shared-folder-id",
        type=str,
        default=DROPBOX_SHARED_FOLDER_ID,
        dest="dropbox_shared_folder_id",
        help="Optional Dropbox shared folder namespace ID to pin requests to.",
    )
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> SortConfig:
    """Parse command-line arguments into a strongly-typed SortConfig instance."""
    parser = build_arg_parser()
    ns = parser.parse_args(argv)
    return SortConfig(
        input_dir=ns.input_dir.resolve(),
        output_dir=ns.output_dir.resolve(),
        reference_dir=ns.reference_dir.resolve() if ns.reference_dir else None,
        min_face_size=max(10, ns.min_face_size),
        det_thresh=min(max(0.1, ns.det_thresh), 0.99),
        sim_threshold=min(max(0.1, ns.sim_threshold), 0.99),
        cluster_eps=min(max(0.05, ns.cluster_eps), 1.5),
        min_samples=max(1, ns.min_samples),
        save_crops=ns.save_crops,
        enable_jersey_ocr=ns.enable_jersey_ocr,
        use_gpu=ns.use_gpu,
        self_test=ns.self_test,
        launch_ui=ns.launch_ui,
        ui_port=ns.ui_port,
        dropbox_app_key=ns.dropbox_app_key.strip(),
        dropbox_shared_folder_id=ns.dropbox_shared_folder_id.strip(),
    )
