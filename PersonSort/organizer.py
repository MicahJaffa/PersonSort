"""Output folder organization, face preview crop saving, profile caching, and summary reporting."""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional


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

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore

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


def _allocate_dest_target(dest_dir: Path, filename: str) -> Path:
    """Return a non-colliding file path inside `dest_dir` for `filename`."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / filename
    if target.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 2
        while target.exists():
            target = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    return target


def _safe_export_photo(src_path: Path, dest_dir: Path, move: bool = False) -> Path:
    """
    Copy or move `src_path` into `dest_dir`, avoiding accidental overwrites.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src_path.name

    if target.exists():
        try:
            if (
                not move
                and target.stat().st_size == src_path.stat().st_size
                and int(target.stat().st_mtime) == int(src_path.stat().st_mtime)
            ):
                return target
        except OSError:
            pass
        target = _allocate_dest_target(dest_dir, src_path.name)

    if move:
        shutil.move(str(src_path), str(target))
    else:
        shutil.copy2(src_path, target)
    return target


def _safe_copy_photo(src_path: Path, dest_dir: Path) -> Path:
    """
    Copy `src_path` into `dest_dir`, preserving metadata and avoiding overwrites.
    """
    return _safe_export_photo(src_path, dest_dir, move=False)


def _encode_reference_crop_jpeg(profile: PlayerProfile) -> Optional[bytes]:
    """Encode `_reference_crop.jpg` bytes in memory for `profile`, or None if unavailable."""
    best = profile.best_detection
    if best is None or best.face_bbox is None:
        return None

    img_bgr = load_image_bgr(best.image_path)
    if img_bgr is None:
        return None

    crop_bgr = crop_region(img_bgr, best.face_bbox, padding_ratio=0.25)
    if crop_bgr is None:
        return None

    if Image is not None:
        try:
            buf = io.BytesIO()
            Image.fromarray(crop_bgr[:, :, ::-1]).save(buf, format="JPEG", quality=92)
            return buf.getvalue()
        except Exception:
            pass

    if cv2 is not None:
        try:
            ok, encoded = cv2.imencode(".jpg", crop_bgr)
            if ok:
                return encoded.tobytes()
        except Exception:
            pass

    return None


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
    *,
    output_dir_override: Optional[Path] = None,
    file_mode: str = "copy",
    dropbox_manager: Optional[Any] = None,
    dropbox_entry_by_path: Optional[dict[Path, Any]] = None,
    dropbox_loaded_as_thumbnails: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """
    Copy or move photos into per-player folders locally, save `_reference_crop.jpg` thumbnails,
    cache player profiles (`player_profiles.npz`), and write `summary_report.json`.
    When source photos came from Dropbox as fast thumbnails, downloads full-resolution files.
    """
    output_dir = (output_dir_override or config.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    entry_map = dropbox_entry_by_path or {}
    downloaded_full_cache: dict[Path, Path] = {}

    # Sort profiles in natural numerical / alphabetical order by folder_name
    sorted_profiles = sorted(
        profiles.values(),
        key=lambda p: natural_sort_key(p.folder_name),
    )

    matched_photos_any_player: set[Path] = {
        photo_path for prof in sorted_profiles for photo_path in prof.photo_paths
    }
    singleton_photos: set[Path] = {
        det.image_path
        for det in unassigned_detections
        if det.image_path not in matched_photos_any_player
    }
    truly_unmatched_no_face = [
        p
        for p in no_face_photos
        if p not in matched_photos_any_player and p not in singleton_photos
    ]

    # Count total export operations & occurrences per source path (for safe multi-folder move)
    occurrence_remaining: dict[Path, int] = {}
    total_exports = 0
    for prof in sorted_profiles:
        for p in prof.photo_paths:
            occurrence_remaining[p] = occurrence_remaining.get(p, 0) + 1
            total_exports += 1
    for p in singleton_photos:
        occurrence_remaining[p] = occurrence_remaining.get(p, 0) + 1
        total_exports += 1
    for p in truly_unmatched_no_face:
        occurrence_remaining[p] = occurrence_remaining.get(p, 0) + 1
        total_exports += 1

    completed_exports = 0

    def _export_single(photo_path: Path, target_dir: Path) -> Path:
        nonlocal completed_exports
        is_last_occurrence = occurrence_remaining.get(photo_path, 1) <= 1
        occurrence_remaining[photo_path] = max(0, occurrence_remaining.get(photo_path, 1) - 1)
        should_move_local = file_mode == "move" and is_last_occurrence

        entry = entry_map.get(photo_path)
        if dropbox_loaded_as_thumbnails and dropbox_manager is not None and entry is not None:
            if photo_path in downloaded_full_cache and downloaded_full_cache[photo_path].is_file():
                exported = _safe_export_photo(downloaded_full_cache[photo_path], target_dir, move=False)
            else:
                target_path = _allocate_dest_target(target_dir, entry.name or photo_path.name)
                dropbox_manager.download_full_file(entry, target_path)
                downloaded_full_cache[photo_path] = target_path
                exported = target_path
        else:
            exported = _safe_export_photo(photo_path, target_dir, move=should_move_local)

        completed_exports += 1
        if progress_callback:
            progress_callback(completed_exports, max(1, total_exports), exported.name)
        return exported

    player_summaries: list[dict[str, Any]] = []
    matched_photos_exported: set[Path] = set()

    for prof in sorted_profiles:
        player_dir = output_dir / prof.folder_name
        player_dir.mkdir(parents=True, exist_ok=True)

        if config.save_crops:
            save_reference_crop(prof, player_dir)

        copied_files: list[str] = []
        for photo_path in sorted(prof.photo_paths, key=lambda p: natural_sort_key(p.name)):
            exported_target = _export_single(photo_path, player_dir)
            copied_files.append(exported_target.name)
            matched_photos_exported.add(photo_path)

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

    if singleton_photos:
        singletons_dir = output_dir / SINGLETONS_FOLDER_NAME
        for photo_path in sorted(singleton_photos, key=lambda p: natural_sort_key(p.name)):
            _export_single(photo_path, singletons_dir)

    if truly_unmatched_no_face:
        no_face_dir = output_dir / NO_FACE_FOLDER_NAME
        for photo_path in sorted(truly_unmatched_no_face, key=lambda p: natural_sort_key(p.name)):
            _export_single(photo_path, no_face_dir)

    cache_path = save_profiles_cache(profiles, output_dir)

    summary_data: dict[str, Any] = {
        "destination_type": "local",
        "output_dir": str(output_dir),
        "file_mode": file_mode,
        "total_photos_scanned": total_photos_scanned,
        "total_players_grouped": len(sorted_profiles),
        "photos_with_grouped_players": len(matched_photos_exported),
        "singleton_photos_count": len(singleton_photos),
        "no_face_photos_count": len(truly_unmatched_no_face),
        "profile_cache_file": cache_path.name if cache_path else None,
        "players": player_summaries,
    }

    report_path = output_dir / SUMMARY_REPORT_FILENAME
    report_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
    return summary_data


def organize_output_to_dropbox(
    profiles: dict[str, PlayerProfile],
    unassigned_detections: list[PlayerDetection],
    no_face_photos: list[Path],
    total_photos_scanned: int,
    config: SortConfig,
    dropbox_manager: Any,
    dropbox_output_folder: str,
    *,
    file_mode: str = "copy",
    dropbox_entry_by_path: Optional[dict[Path, Any]] = None,
    shared_folder_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """
    Organize player folders directly inside Dropbox:
    - Photos loaded from Dropbox use instant cloud-to-cloud `copy_or_move_batch(..., autorename=True)`.
    - Photos loaded from local disk are uploaded directly into Dropbox player folders.
    """
    cleaned_base = (dropbox_output_folder or "").strip().replace("\\", "/").strip("/")
    base_prefix = f"/{cleaned_base}" if cleaned_base else ""
    display_base = base_prefix or "/"
    entry_map = dropbox_entry_by_path or {}

    if base_prefix:
        dropbox_manager.ensure_folder(base_prefix, shared_folder_id=shared_folder_id)

    sorted_profiles = sorted(
        profiles.values(),
        key=lambda p: natural_sort_key(p.folder_name),
    )

    matched_photos_any_player: set[Path] = {
        photo_path for prof in sorted_profiles for photo_path in prof.photo_paths
    }
    singleton_photos: set[Path] = {
        det.image_path
        for det in unassigned_detections
        if det.image_path not in matched_photos_any_player
    }
    truly_unmatched_no_face = [
        p
        for p in no_face_photos
        if p not in matched_photos_any_player and p not in singleton_photos
    ]

    relocation_pairs: list[tuple[str, str]] = []
    local_uploads: list[tuple[Path, str]] = []
    reference_uploads: list[tuple[str, bytes]] = []
    player_summaries: list[dict[str, Any]] = []

    for prof in sorted_profiles:
        folder_dbx_path = f"{base_prefix}/{prof.folder_name}"
        dropbox_manager.ensure_folder(folder_dbx_path, shared_folder_id=shared_folder_id)

        if config.save_crops:
            crop_bytes = _encode_reference_crop_jpeg(prof)
            if crop_bytes:
                reference_uploads.append((f"{folder_dbx_path}/{REFERENCE_CROP_FILENAME}", crop_bytes))

        exported_names: list[str] = []
        for photo_path in sorted(prof.photo_paths, key=lambda p: natural_sort_key(p.name)):
            entry = entry_map.get(photo_path)
            if entry is not None:
                src_ref = entry.file_id or entry.path_lower or entry.path_display
                dest_name = entry.name or photo_path.name
                relocation_pairs.append((src_ref, f"{folder_dbx_path}/{dest_name}"))
                exported_names.append(dest_name)
            else:
                local_uploads.append((photo_path, f"{folder_dbx_path}/{photo_path.name}"))
                exported_names.append(photo_path.name)

        player_summaries.append(
            {
                "player_id": prof.player_id,
                "folder_name": prof.folder_name,
                "is_known_reference": prof.is_known_reference,
                "photo_count": prof.photo_count,
                "primary_jersey_number": prof.primary_jersey_number,
                "jersey_votes": prof.jersey_votes,
                "photos": exported_names,
            }
        )

    def _queue_extra_bucket(paths: list[Path], bucket_name: str) -> None:
        if not paths:
            return
        bucket_dbx_path = f"{base_prefix}/{bucket_name}"
        dropbox_manager.ensure_folder(bucket_dbx_path, shared_folder_id=shared_folder_id)
        for photo_path in sorted(paths, key=lambda p: natural_sort_key(p.name)):
            entry = entry_map.get(photo_path)
            if entry is not None:
                src_ref = entry.file_id or entry.path_lower or entry.path_display
                dest_name = entry.name or photo_path.name
                relocation_pairs.append((src_ref, f"{bucket_dbx_path}/{dest_name}"))
            else:
                local_uploads.append((photo_path, f"{bucket_dbx_path}/{photo_path.name}"))

    _queue_extra_bucket(list(singleton_photos), SINGLETONS_FOLDER_NAME)
    _queue_extra_bucket(truly_unmatched_no_face, NO_FACE_FOLDER_NAME)

    total_ops = max(1, len(relocation_pairs) + len(local_uploads))
    completed = 0

    if relocation_pairs:
        def _reloc_cb(done_reloc: int, _reloc_total: int, label: str) -> None:
            if progress_callback:
                progress_callback(done_reloc, total_ops, label)

        completed = dropbox_manager.copy_or_move_batch(
            relocation_pairs,
            mode=file_mode,
            shared_folder_id=shared_folder_id,
            progress_callback=_reloc_cb,
        )

    for local_path, dest_dbx_path in local_uploads:
        dropbox_manager.upload_file(
            local_path,
            dest_dbx_path,
            shared_folder_id=shared_folder_id,
            autorename=True,
        )
        completed += 1
        if progress_callback:
            progress_callback(completed, total_ops, f"Uploading to Dropbox: {local_path.name}")

    for crop_dest, crop_bytes in reference_uploads:
        dropbox_manager.upload_bytes(
            crop_bytes,
            crop_dest,
            shared_folder_id=shared_folder_id,
            autorename=False,
        )

    summary_data: dict[str, Any] = {
        "destination_type": "dropbox",
        "output_dir": display_base,
        "file_mode": file_mode,
        "total_photos_scanned": total_photos_scanned,
        "total_players_grouped": len(sorted_profiles),
        "photos_with_grouped_players": len(matched_photos_any_player),
        "singleton_photos_count": len(singleton_photos),
        "no_face_photos_count": len(truly_unmatched_no_face),
        "profile_cache_file": None,
        "players": player_summaries,
    }

    report_bytes = json.dumps(summary_data, indent=2).encode("utf-8")
    dropbox_manager.upload_bytes(
        report_bytes,
        f"{base_prefix}/{SUMMARY_REPORT_FILENAME}",
        shared_folder_id=shared_folder_id,
        autorename=True,
    )
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
