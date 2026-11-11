"""Output folder organization, face preview crop saving, profile caching, and summary reporting."""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Optional


def natural_sort_key(text: str) -> list[Any]:
    """
    Split a string into text and integer chunks so names sort in true numerical
    and alphabetical order (e.g. 'Player_2' before 'Player_10', 'A' before 'B').
    """
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from config import SortConfig
from face_detector import FaceDetector, normalize_embedding
from image_loader import crop_region, load_image_bgr, save_image_bgr, scan_images
from models import PlayerDetection, PlayerProfile

logger = logging.getLogger(__name__)

PROFILE_CACHE_FILENAME = "player_profiles.npz"
SUMMARY_REPORT_FILENAME = "summary_report.json"
REFERENCE_CROP_FILENAME = "_reference_crop.jpg"
NO_FACE_FOLDER_NAME = "No_Face_Detected"
SINGLETONS_FOLDER_NAME = "Unclustered_Singletons"


def _safe_copy_photo(src_path: Path, dest_dir: Path) -> Path:
    """
    Copy `src_path` into `dest_dir`, preserving metadata.
    If a different file with the same name already exists in `dest_dir`,
    append a numeric suffix to avoid accidental overwrites.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src_path.name

    if target.exists():
        # If it is already the exact same size and mtime, skip re-copying
        try:
            if (
                target.stat().st_size == src_path.stat().st_size
                and int(target.stat().st_mtime) == int(src_path.stat().st_mtime)
            ):
                return target
        except OSError:
            pass

        stem = src_path.stem
        suffix = src_path.suffix
        counter = 2
        while target.exists():
            target = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.copy2(src_path, target)
    return target


def save_reference_crop(profile: PlayerProfile, player_dir: Path) -> Optional[Path]:
    """
    Save `_reference_crop.jpg` (the sharpest/largest face crop for `profile`)
    inside `player_dir` for easy visual identification of the player.
    """
    best = profile.best_detection
    if best is None or best.face_bbox is None:
        return None

    img_bgr = load_image_bgr(best.image_path)
    if img_bgr is None:
        return None

    crop_bgr = crop_region(img_bgr, best.face_bbox, padding_ratio=0.25)
    if crop_bgr is None:
        return None

    crop_path = player_dir / REFERENCE_CROP_FILENAME
    if save_image_bgr(crop_bgr, crop_path):
        return crop_path
    return None


def save_profiles_cache(
    profiles: dict[str, PlayerProfile],
    output_dir: Path,
) -> Optional[Path]:
    """
    Save each player's centroid face embedding and folder metadata to `player_profiles.npz`
    so `output_dir` can be passed directly as `--reference` in future runs.
    """
    if np is None or not profiles:
        return None

    player_ids: list[str] = []
    folder_names: list[str] = []
    centroids: list[np.ndarray] = []

    for pid, prof in sorted(profiles.items()):
        centroid = prof.centroid_embedding
        if centroid is None:
            continue
        player_ids.append(pid)
        folder_names.append(prof.folder_name)
        centroids.append(normalize_embedding(centroid))

    if not centroids:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = output_dir / PROFILE_CACHE_FILENAME
    np.savez_compressed(
        cache_path,
        player_ids=np.array(player_ids, dtype=str),
        folder_names=np.array(folder_names, dtype=str),
        centroids=np.vstack(centroids).astype(np.float32),
    )
    return cache_path


def load_reference_profiles(
    reference_dir: Path,
    config: SortConfig,
    detector: Optional[FaceDetector] = None,
) -> dict[str, PlayerProfile]:
    """
    Load known player profiles from `reference_dir`:
    1. If `player_profiles.npz` exists in `reference_dir`, load cached embeddings
       (and detect if the user renamed any folders in `reference_dir`).
    2. Also scan any subdirectories in `reference_dir` (excluding No_Face_Detected /
       Unclustered_Singletons) that aren't in the cache and extract face embeddings.
    """
    if not reference_dir.exists() or not reference_dir.is_dir():
        logger.warning("Reference directory not found: %s", reference_dir)
        return {}

    profiles: dict[str, PlayerProfile] = {}
    cache_path = reference_dir / PROFILE_CACHE_FILENAME

    # 1. Load from cached .npz if available
    if np is not None and cache_path.is_file():
        try:
            with np.load(cache_path, allow_pickle=False) as data:
                pids = data["player_ids"].tolist()
                fnames = data["folder_names"].tolist()
                centroids = data["centroids"]

                for pid, fname, vec in zip(pids, fnames, centroids):
                    # If the original folder still exists, use its name;
                    # skip if the user deleted it.
                    folder_path = reference_dir / str(fname)
                    effective_id = str(fname) if folder_path.exists() else str(pid)
                    prof = PlayerProfile(
                        player_id=effective_id,
                        is_known_reference=True,
                        embeddings=[normalize_embedding(vec)],
                    )
                    profiles[effective_id] = prof
            logger.info("Loaded %d cached player profile(s) from %s", len(profiles), cache_path)
        except Exception as exc:
            logger.warning("Failed to load profile cache %s: %s", cache_path, exc)

    # 2. Scan subdirectories for new or renamed reference folders
    skip_dirs = {NO_FACE_FOLDER_NAME.lower(), SINGLETONS_FOLDER_NAME.lower()}
    subdirs = [
        d
        for d in sorted(reference_dir.iterdir(), key=lambda p: p.name.lower())
        if d.is_dir() and not d.name.startswith(".") and d.name.lower() not in skip_dirs
    ]

    for subdir in subdirs:
        if subdir.name in profiles:
            continue

        # Check if `_reference_crop.jpg` or regular photos exist inside this subfolder
        ref_crop = subdir / REFERENCE_CROP_FILENAME
        candidate_images = [ref_crop] if ref_crop.is_file() else scan_images(subdir, recursive=True)
        if not candidate_images:
            continue

        if detector is None:
            detector = FaceDetector(config)

        prof = PlayerProfile(player_id=subdir.name, is_known_reference=True)
        # Sample up to 10 photos per reference folder for speed
        for img_path in candidate_images[:10]:
            dets = detector.detect_in_image(img_path)
            if dets and dets[0].face_embedding is not None:
                prof.embeddings.append(dets[0].face_embedding)

        if prof.embeddings:
            profiles[prof.player_id] = prof
            logger.info("Built reference profile '%s' from %d photo(s)", prof.player_id, len(prof.embeddings))

    return profiles


def organize_output(
    profiles: dict[str, PlayerProfile],
    unassigned_detections: list[PlayerDetection],
    no_face_photos: list[Path],
    total_photos_scanned: int,
    config: SortConfig,
) -> dict[str, Any]:
    """
    Copy photos into per-player folders, save `_reference_crop.jpg` thumbnails,
    cache player profiles (`player_profiles.npz`), and write `summary_report.json`.
    """
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sort profiles in natural numerical / alphabetical order by folder_name
    sorted_profiles = sorted(
        profiles.values(),
        key=lambda p: natural_sort_key(p.folder_name),
    )

    matched_photos_any_player: set[Path] = set()
    player_summaries: list[dict[str, Any]] = []

    for prof in sorted_profiles:
        player_dir = output_dir / prof.folder_name
        player_dir.mkdir(parents=True, exist_ok=True)

        copied_files: list[str] = []
        for photo_path in sorted(prof.photo_paths, key=lambda p: natural_sort_key(p.name)):
            copied_target = _safe_copy_photo(photo_path, player_dir)
            copied_files.append(copied_target.name)
            matched_photos_any_player.add(photo_path)

        if config.save_crops:
            save_reference_crop(prof, player_dir)

        player_summaries.append(
            {
                "player_id": prof.player_id,
                "folder_name": prof.folder_name,
                "is_known_reference": prof.is_known_reference,
                "photo_count": prof.photo_count,
                "primary_jersey_number": prof.primary_jersey_number,
                "jersey_votes": prof.jersey_votes,
                "photos": copied_files,
            }
        )

    # Copy singleton detections (when min_samples > 1) that weren't placed in any player folder
    singleton_photos: set[Path] = {
        det.image_path
        for det in unassigned_detections
        if det.image_path not in matched_photos_any_player
    }
    if singleton_photos:
        singletons_dir = output_dir / SINGLETONS_FOLDER_NAME
        for photo_path in sorted(singleton_photos, key=lambda p: p.name.lower()):
            _safe_copy_photo(photo_path, singletons_dir)

    # Copy photos with 0 foreground faces detected (and not matched via jersey number)
    truly_unmatched_no_face = [
        p
        for p in no_face_photos
        if p not in matched_photos_any_player and p not in singleton_photos
    ]
    if truly_unmatched_no_face:
        no_face_dir = output_dir / NO_FACE_FOLDER_NAME
        for photo_path in sorted(truly_unmatched_no_face, key=lambda p: p.name.lower()):
            _safe_copy_photo(photo_path, no_face_dir)

    # Save profile cache so this output directory can be reused with --reference
    cache_path = save_profiles_cache(profiles, output_dir)

    summary_data: dict[str, Any] = {
        "total_photos_scanned": total_photos_scanned,
        "total_players_grouped": len(sorted_profiles),
        "photos_with_grouped_players": len(matched_photos_any_player),
        "singleton_photos_count": len(singleton_photos),
        "no_face_photos_count": len(truly_unmatched_no_face),
        "profile_cache_file": cache_path.name if cache_path else None,
        "players": player_summaries,
    }

    report_path = output_dir / SUMMARY_REPORT_FILENAME
    report_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
    return summary_data


def print_summary_table(summary: dict[str, Any], output_dir: Path) -> None:
    """Print a clean formatted summary table of grouped players and photo counts."""
    print("\n" + "=" * 62)
    print("                 PERSONSORT SUMMARY REPORT")
    print("=" * 62)
    print(f"  Total Photos Scanned       : {summary['total_photos_scanned']}")
    print(f"  Unique Players Identified  : {summary['total_players_grouped']}")
    print(f"  Photos Matched to Players  : {summary['photos_with_grouped_players']}")
    if summary.get("singleton_photos_count", 0) > 0:
        print(f"  Unclustered Singletons     : {summary['singleton_photos_count']}")
    print(f"  No-Face / Unmatched Photos : {summary['no_face_photos_count']}")
    print(f"  Output Directory           : {output_dir}")
    print("-" * 62)

    players = summary.get("players", [])
    if players:
        print(f"  {'Folder Name':<28} {'Type':<12} {'Jersey #':<10} {'Photos':>6}")
        print("  " + "-" * 58)
        for p in players:
            ptype = "Reference" if p["is_known_reference"] else "Clustered"
            jersey = f"#{p['primary_jersey_number']}" if p.get("primary_jersey_number") else "-"
            print(
                f"  {p['folder_name']:<28} {ptype:<12} {jersey:<10} {p['photo_count']:>6}"
            )
    else:
        print("  (No player faces were grouped.)")

    print("=" * 62 + "\n")
