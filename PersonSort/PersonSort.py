"""Entry point for PersonSort — Sports photo face grouping and player identification."""

from __future__ import annotations

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import importlib.util
import logging
from pathlib import Path
import subprocess


def _ensure_project_venv() -> None:
    """
    If PersonSort is launched with a system Python that does not have `insightface`
    installed, automatically re-launch using `PersonSort/env/Scripts/python.exe` if present.
    """
    if getattr(sys, "frozen", False):
        return

    if importlib.util.find_spec("insightface") is not None:
        return

    venv_python = Path(__file__).resolve().parent / "env" / "Scripts" / "python.exe"
    if venv_python.is_file():
        try:
            if Path(sys.executable).resolve() != venv_python.resolve():
                sys.exit(subprocess.call([str(venv_python), *sys.argv]))
        except OSError:
            pass


_ensure_project_venv()

from body_jersey_detector import BodyJerseyDetector  # noqa: E402
from config import SortConfig, parse_args  # noqa: E402
from face_detector import FaceDetector  # noqa: E402
from image_loader import load_image_bgr, scan_images  # noqa: E402
from matcher import build_jersey_folders, group_players  # noqa: E402
from models import PlayerDetection, PlayerProfile  # noqa: E402
from organizer import load_reference_profiles, organize_output, print_summary_table  # noqa: E402
from session_state import CurationSession  # noqa: E402
from web_ui import launch_curation_ui  # noqa: E402


def run_pipeline(cfg: SortConfig) -> int:
    # By default (cfg.launch_ui == True), start the Web UI immediately on the Homescreen
    # so the user can upload a .zip of photos directly through the website.
    if cfg.launch_ui:
        session = CurationSession(config=cfg, mode="home")
        launch_curation_ui(session, port=cfg.ui_port, open_browser=True)
        return 0

    # Headless CLI mode (--cli)
    if not cfg.input_dir.exists():
        cfg.input_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Notice] Created empty input directory at:\n  {cfg.input_dir}")
        return 0

    image_paths = scan_images(cfg.input_dir, recursive=True)
    if not image_paths:
        print(f"[Notice] No supported images found in:\n  {cfg.input_dir}")
        return 0

    detector = FaceDetector(cfg)
    body_jersey = BodyJerseyDetector(cfg)
    reference_profiles = {}
    if cfg.reference_dir is not None:
        reference_profiles = load_reference_profiles(cfg.reference_dir, cfg, detector=detector)

    detections: list[PlayerDetection] = []
    no_face_photos = []
    for img_path in image_paths:
        img_bgr = load_image_bgr(img_path)
        if img_bgr is None:
            continue
        face_dets = detector.detect_in_image(img_path, image_bgr=img_bgr)
        enriched = body_jersey.enrich_image_detections(
            image_path=img_path,
            face_detections=face_dets,
            image_bgr=img_bgr,
        )
        if not enriched:
            no_face_photos.append(img_path)
        else:
            detections.extend(enriched)

    profiles, unassigned = group_players(
        detections=detections,
        config=cfg,
        reference_profiles=reference_profiles,
    )
    jersey_folders = build_jersey_folders(detections)
    for jnum, jfold in jersey_folders.items():
        if jfold.photo_count > 0:
            j_prof_id = f"Jersey_#{jnum}"
            j_prof = PlayerProfile(player_id=j_prof_id, custom_name=j_prof_id)
            for p in jfold.photo_paths:
                j_prof.photo_paths.add(p)
            profiles[j_prof_id] = j_prof
    summary = organize_output(
        profiles=profiles,
        unassigned_detections=unassigned,
        no_face_photos=no_face_photos,
        total_photos_scanned=len(image_paths),
        config=cfg,
    )
    print_summary_table(summary, cfg.output_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    cfg = parse_args(argv)
    try:
        return run_pipeline(cfg)
    except Exception as exc:
        print(f"\n[Error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
