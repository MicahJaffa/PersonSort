"""Configuration and CLI argument parsing for PersonSort."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

PROJECT_DIR: Path = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR: Path = PROJECT_DIR / "input_photos"
DEFAULT_OUTPUT_DIR: Path = PROJECT_DIR / "sorted_output"


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
    )
