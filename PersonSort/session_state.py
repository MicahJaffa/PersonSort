"""In-memory curation session state for uploading, reviewing, moving, and committing photo folders."""

from __future__ import annotations

import io
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, Callable, Optional
import zipfile

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore

from body_jersey_detector import BodyJerseyDetector
from config import SortConfig, has_downloaded_models
from face_detector import FaceDetector, normalize_embedding
from image_loader import crop_region, is_supported_image, load_image_bgr, save_image_bgr, scan_images
from matcher import build_jersey_folders, compute_pairwise_photo_similarities, group_players
from models import JerseyFolder, PlayerDetection, PlayerProfile, _dot_similarity
from organizer import (
    load_reference_profiles,
    natural_sort_key,
    organize_output,
    organize_output_to_dropbox,
    print_summary_table,
)


def _sanitize_folder_name(raw_name: str) -> str:
    """Strip filesystem-unsafe characters from a user-supplied folder name."""
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", raw_name.strip())
    return cleaned or "Unnamed_Player"


class CurationSession:
    """
    Holds the application state for the Web UI:
    - Starts on the Homescreen (`mode = 'home'`) where the user uploads a `.zip`
      of photos (or individual photos) directly through the website.
    - Sorts all folder names and photo names in natural numerical and alphabetical order.
    - Keeps detected Jersey Number folders (`#7`, `#10`, `#23`) separate at the bottom of
      the sidebar, sorted by bounding-box proximity, until the user chooses to add a
      Jersey Number folder into a Player Folder (which renames it `<Player Name> #[num]`).
    """

    def __init__(
        self,
        config: SortConfig,
        all_photos: Optional[list[Path]] = None,
        all_detections: Optional[list[PlayerDetection]] = None,
        profiles: Optional[dict[str, PlayerProfile]] = None,
        jersey_folders: Optional[dict[str, JerseyFolder]] = None,
        mode: str = "home",
    ) -> None:
        self.config: SortConfig = config
        self.mode: str = mode  # "home" or "review"
        self.all_photos: list[Path] = sorted(
            all_photos or [], key=lambda p: natural_sort_key(p.name)
        )
        self.photo_by_id: dict[str, Path] = {p.name: p for p in self.all_photos}
        self.all_detections: list[PlayerDetection] = list(all_detections or [])
        self.detection_by_face_id: dict[str, PlayerDetection] = {}
        self.face_id_by_detection: dict[int, str] = {}
        self._face_crop_cache: dict[str, bytes] = {}

        self.profiles: dict[str, PlayerProfile] = dict(profiles or {})
        self.jersey_folders: dict[str, JerseyFolder] = (
            dict(jersey_folders)
            if jersey_folders is not None
            else build_jersey_folders(self.all_detections)
        )
        self.photo_dimensions: dict[Path, tuple[int, int]] = {}  # (width, height)
        self.edit_count: int = 0
        self.last_commit_summary: Optional[dict[str, Any]] = None
        self.last_commit_output_dir: Optional[str] = None
        self.last_commit_destination_type: Optional[str] = None
        self.source_type: str = "local"
        self.source_dropbox_folder: str = ""
        self.source_dropbox_shared_folder_id: str = ""
        self.dropbox_entry_by_path: dict[Path, Any] = {}
        self.dropbox_loaded_as_thumbnails: bool = False
        self._detector: Optional[FaceDetector] = None
        self._body_jersey_detector: Optional[BodyJerseyDetector] = None
        self._upload_dir: Optional[Path] = None
        self._temp_dirs: list[Path] = []

        # Real-time progress bar state
        self._progress_lock = threading.Lock()
        self._progress: dict[str, Any] = {
            "active": False,
            "stage": "Idle",
            "current": 0,
            "total": 0,
            "percent": 0,
            "current_file": "",
            "error": None,
        }

        self._index_face_detections()
        for prof in self.profiles.values():
            prof.recompute_similarities()

    def _index_face_detections(self) -> None:
        """Assign a deterministic `face_id` to every detection that has a face bounding box."""
        self.detection_by_face_id.clear()
        self.face_id_by_detection.clear()
        self._face_crop_cache.clear()
        idx = 1
        for det in self.all_detections:
            if det.face_bbox is not None:
                fid = f"face_{idx:04d}"
                idx += 1
                self.detection_by_face_id[fid] = det
                self.face_id_by_detection[id(det)] = fid

    def _get_face_id_for_detection(self, det: Optional[PlayerDetection]) -> Optional[str]:
        """Find the `face_id` for `det`, matching by object identity or bbox coordinates."""
        if det is None or det.face_bbox is None:
            return None
        direct = self.face_id_by_detection.get(id(det))
        if direct:
            return direct
        for fid, orig in self.detection_by_face_id.items():
            if (
                orig.image_path == det.image_path
                and orig.face_bbox is not None
                and orig.face_bbox.x1 == det.face_bbox.x1
                and orig.face_bbox.y1 == det.face_bbox.y1
            ):
                return fid
        return None

    def _get_best_face_id_for_profile(self, prof: PlayerProfile) -> Optional[str]:
        """Return the best reference `face_id` for a player folder."""
        fid = self._get_face_id_for_detection(prof.best_detection)
        if fid:
            return fid

        best_det: Optional[PlayerDetection] = None
        for det in self.all_detections:
            if det.image_path in prof.photo_paths and det.has_face:
                if best_det is None or det.face_quality_score > best_det.face_quality_score:
                    best_det = det
        return self._get_face_id_for_detection(best_det)

    def _update_progress(
        self,
        *,
        active: bool,
        stage: str,
        current: int = 0,
        total: int = 0,
        current_file: str = "",
        error: Optional[str] = None,
    ) -> None:
        pct = int(round((current / total) * 100)) if total > 0 else (100 if not active else 0)
        with self._progress_lock:
            self._progress = {
                "active": active,
                "stage": stage,
                "current": current,
                "total": total,
                "percent": max(0, min(100, pct)),
                "current_file": current_file,
                "error": error,
            }

    def get_progress(self) -> dict[str, Any]:
        with self._progress_lock:
            return dict(self._progress)

    def load_batch(
        self,
        all_photos: list[Path],
        all_detections: list[PlayerDetection],
        profiles: dict[str, PlayerProfile],
        dimensions: Optional[dict[Path, tuple[int, int]]] = None,
        jersey_folders: Optional[dict[str, JerseyFolder]] = None,
    ) -> dict[str, Any]:
        """Populate the session with a processed batch of photos and switch to review mode."""
        self.all_photos = sorted(all_photos, key=lambda p: natural_sort_key(p.name))
        self.photo_by_id = {}
        for p in self.all_photos:
            key = p.name
            counter = 2
            while key in self.photo_by_id and self.photo_by_id[key] != p:
                key = f"{p.stem}_{counter}{p.suffix}"
                counter += 1
            self.photo_by_id[key] = p

        self.all_detections = list(all_detections)
        self._index_face_detections()
        self.profiles = dict(profiles)
        self.jersey_folders = (
            dict(jersey_folders)
            if jersey_folders is not None
            else build_jersey_folders(self.all_detections)
        )
        self.photo_dimensions = dict(dimensions or {})
        self.edit_count = 0
        self.mode = "review"

        for prof in self.profiles.values():
            prof.recompute_similarities()

        return self.to_state_dict()

    def _cleanup_temp_dirs(self) -> None:
        """Delete temporary uploaded/downloaded batch directories to reclaim disk space."""
        for temp_dir in self._temp_dirs:
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except OSError:
                pass
        self._temp_dirs.clear()
        self._upload_dir = None

    def reset_to_home(self) -> dict[str, Any]:
        """Return the session to the Homescreen and clean up temporary downloaded files."""
        self.mode = "home"
        self.all_photos = []
        self.photo_by_id = {}
        self.all_detections = []
        self.detection_by_face_id.clear()
        self.face_id_by_detection.clear()
        self._face_crop_cache.clear()
        self.profiles = {}
        self.jersey_folders = {}
        self.photo_dimensions = {}
        self.edit_count = 0
        self.source_type = "local"
        self.source_dropbox_folder = ""
        self.source_dropbox_shared_folder_id = ""
        self.dropbox_entry_by_path = {}
        self.dropbox_loaded_as_thumbnails = False
        self._cleanup_temp_dirs()
        return self.to_state_dict()

    def start_background_processing(
        self, saved_files: list[Path], enable_jersey_ocr: Optional[bool] = None
    ) -> None:
        """Launch photo extraction, face detection, and jersey recognition in a background thread."""
        if enable_jersey_ocr is not None:
            self.config.enable_jersey_ocr = bool(enable_jersey_ocr)

        self._update_progress(
            active=True,
            stage="Extracting uploaded files...",
            current=0,
            total=1,
            current_file="",
            error=None,
        )
        thread = threading.Thread(
            target=self._run_processing_worker,
            args=(list(saved_files),),
            daemon=True,
        )
        thread.start()

    def _run_processing_worker(self, saved_files: list[Path]) -> None:
        try:
            self.process_uploaded_files(saved_files)
            self._update_progress(
                active=False,
                stage="Complete",
                current=len(self.all_photos),
                total=len(self.all_photos),
            )
        except Exception as exc:
            self._update_progress(
                active=False,
                stage="Error",
                current=0,
                total=0,
                error=str(exc),
            )

    def start_dropbox_processing(
        self,
        dropbox_manager: Any,
        folder_path: str,
        *,
        enable_jersey_ocr: Optional[bool] = None,
        use_thumbnails: bool = True,
        shared_folder_id: Optional[str] = None,
    ) -> None:
        """Download photos from Dropbox (thumbnails or full-res) and scan them in the background."""
        if enable_jersey_ocr is not None:
            self.config.enable_jersey_ocr = bool(enable_jersey_ocr)

        self._update_progress(
            active=True,
            stage="Listing photos in Dropbox folder...",
            current=0,
            total=1,
            current_file="",
            error=None,
        )
        thread = threading.Thread(
            target=self._run_dropbox_worker,
            args=(dropbox_manager, folder_path, bool(use_thumbnails), shared_folder_id),
            daemon=True,
        )
        thread.start()

    def _run_dropbox_worker(
        self,
        dropbox_manager: Any,
        folder_path: str,
        use_thumbnails: bool,
        shared_folder_id: Optional[str],
    ) -> None:
        try:
            self.process_dropbox_folder(
                dropbox_manager=dropbox_manager,
                folder_path=folder_path,
                use_thumbnails=use_thumbnails,
                shared_folder_id=shared_folder_id,
            )
            self._update_progress(
                active=False,
                stage="Complete",
                current=len(self.all_photos),
                total=len(self.all_photos),
            )
        except Exception as exc:
            self._update_progress(
                active=False,
                stage="Error",
                current=0,
                total=0,
                error=str(exc),
            )

    def process_dropbox_folder(
        self,
        dropbox_manager: Any,
        folder_path: str,
        *,
        use_thumbnails: bool = True,
        shared_folder_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Download supported photos from a Dropbox folder into a temp directory,
        store immutable Dropbox file IDs, and run detection & grouping.
        """
        self._cleanup_temp_dirs()
        ns_id = (
            shared_folder_id.strip()
            if shared_folder_id is not None
            else dropbox_manager.shared_folder_id
        )
        entries = dropbox_manager.list_images_in_folder(
            folder_path,
            shared_folder_id=ns_id,
            recursive=False,
        )
        if not entries:
            raise ValueError(
                f"No supported photos (.jpg, .png, .webp, .bmp) were found in Dropbox folder '{folder_path or '/'}'."
            )

        batch_dir = Path(tempfile.mkdtemp(prefix="personsort_dropbox_"))
        self._temp_dirs.append(batch_dir)
        self._upload_dir = batch_dir

        def _dl_progress(done: int, total: int, filename: str) -> None:
            mode_label = "thumbnails" if use_thumbnails else "photos"
            self._update_progress(
                active=True,
                stage=f"Downloading {mode_label} from Dropbox ({done}/{total})...",
                current=done,
                total=total,
                current_file=filename,
            )

        self._update_progress(
            active=True,
            stage=f"Downloading {len(entries)} photo(s) from Dropbox...",
            current=0,
            total=len(entries),
        )

        downloaded_pairs = dropbox_manager.download_images_to_dir(
            entries,
            batch_dir,
            use_thumbnails=use_thumbnails,
            progress_callback=_dl_progress,
        )

        image_paths = [local_p for local_p, _ in downloaded_pairs]
        self.source_type = "dropbox"
        self.source_dropbox_folder = folder_path or "/"
        self.source_dropbox_shared_folder_id = ns_id
        self.dropbox_entry_by_path = {local_p: entry for local_p, entry in downloaded_pairs}
        self.dropbox_loaded_as_thumbnails = bool(use_thumbnails)

        return self._run_detection_and_grouping(image_paths)

    def process_uploaded_files(self, saved_files: list[Path]) -> dict[str, Any]:
        """
        Extract any `.zip` archives among `saved_files`, scan all supported images,
        run InsightFace detection + Body/Jersey OCR + DBSCAN clustering, and switch to review mode.
        """
        self._cleanup_temp_dirs()
        batch_dir = Path(tempfile.mkdtemp(prefix="personsort_upload_"))
        self._temp_dirs.append(batch_dir)
        self._upload_dir = batch_dir
        extract_dir = batch_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        for file_path in saved_files:
            if file_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(file_path, "r") as zf:
                    zf.extractall(extract_dir)
            elif is_supported_image(file_path):
                target = extract_dir / file_path.name
                if target != file_path:
                    shutil.copy2(file_path, target)

        image_paths = scan_images(extract_dir, recursive=True)
        if not image_paths:
            raise ValueError(
                "No supported photos (.jpg, .png, .webp, .bmp) were found in the uploaded file(s)."
            )

        image_paths.sort(key=lambda p: natural_sort_key(p.name))
        self.source_type = "local"
        self.source_dropbox_folder = ""
        self.source_dropbox_shared_folder_id = ""
        self.dropbox_entry_by_path = {}
        self.dropbox_loaded_as_thumbnails = False
        return self._run_detection_and_grouping(image_paths)

    def _run_detection_and_grouping(self, image_paths: list[Path]) -> dict[str, Any]:
        """Run face detection, optional jersey OCR, and DBSCAN grouping over `image_paths`."""
        total_imgs = len(image_paths)
        scan_jerseys = bool(self.config.enable_jersey_ocr)
        needs_models_download = (
            (self._detector is None or (scan_jerseys and self._body_jersey_detector is None))
            and has_downloaded_models(check_jersey=scan_jerseys)["needs_first_time_download"]
        )
        if needs_models_download:
            stage_title = "First-time setup: downloading AI models (this only happens once)..."
        elif scan_jerseys:
            stage_title = "Loading face & jersey recognition models..."
        else:
            stage_title = "Loading face recognition model..."

        self._update_progress(
            active=True,
            stage=stage_title,
            current=0,
            total=total_imgs,
        )

        if self._detector is None:
            self._detector = FaceDetector(self.config)
        if scan_jerseys and self._body_jersey_detector is None:
            self._body_jersey_detector = BodyJerseyDetector(self.config)

        # Warm up model downloads/init now while the clear model-loading status is displayed
        if needs_models_download:
            if hasattr(self._detector, "_ensure_initialized"):
                self._detector._ensure_initialized()
            if scan_jerseys and self._body_jersey_detector is not None:
                self._body_jersey_detector._ensure_yolo()
                self._body_jersey_detector._ensure_ocr()

        reference_profiles: dict[str, PlayerProfile] = {}
        if self.config.reference_dir is not None and self.config.reference_dir.exists():
            reference_profiles = load_reference_profiles(
                self.config.reference_dir, self.config, detector=self._detector
            )

        all_detections: list[PlayerDetection] = []
        dimensions: dict[Path, tuple[int, int]] = {}

        for idx, img_path in enumerate(image_paths, start=1):
            scan_label = (
                f"Scanning photo {idx} of {total_imgs} (faces & jersey numbers)"
                if scan_jerseys
                else f"Scanning photo {idx} of {total_imgs} (faces)"
            )
            self._update_progress(
                active=True,
                stage=scan_label,
                current=idx - 1,
                total=total_imgs,
                current_file=img_path.name,
            )
            img_bgr = load_image_bgr(img_path)
            if img_bgr is not None:
                h, w = img_bgr.shape[:2]
                dimensions[img_path] = (int(w), int(h))
                save_image_bgr(img_bgr, img_path)
                face_dets = self._detector.detect_in_image(img_path, image_bgr=img_bgr)
                if scan_jerseys and self._body_jersey_detector is not None:
                    enriched_dets = self._body_jersey_detector.enrich_image_detections(
                        image_path=img_path,
                        face_detections=face_dets,
                        image_bgr=img_bgr,
                    )
                    all_detections.extend(enriched_dets)
                else:
                    all_detections.extend(face_dets)

        self._update_progress(
            active=True,
            stage="Grouping faces & jersey numbers into folders...",
            current=total_imgs,
            total=total_imgs,
        )

        profiles, _ = group_players(
            detections=all_detections,
            config=self.config,
            reference_profiles=reference_profiles,
        )
        jersey_folders = build_jersey_folders(all_detections)

        return self.load_batch(
            all_photos=image_paths,
            all_detections=all_detections,
            profiles=profiles,
            dimensions=dimensions,
            jersey_folders=jersey_folders,
        )

    def get_face_crop_bytes(self, face_id: str) -> Optional[bytes]:
        """Return JPEG bytes for the cropped face identified by `face_id`."""
        if face_id in self._face_crop_cache:
            return self._face_crop_cache[face_id]

        det = self.detection_by_face_id.get(face_id)
        if det is None or det.face_bbox is None:
            return None

        img_bgr = load_image_bgr(det.image_path)
        if img_bgr is None:
            return None

        crop_bgr = crop_region(img_bgr, det.face_bbox, padding_ratio=0.28)
        if crop_bgr is None:
            return None

        if Image is not None:
            rgb = crop_bgr[:, :, ::-1]
            pil_img = Image.fromarray(rgb)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=90)
            data = buf.getvalue()
            self._face_crop_cache[face_id] = data
            return data

        if cv2 is not None:
            ok, encoded = cv2.imencode(".jpg", crop_bgr)
            if ok:
                data = encoded.tobytes()
                self._face_crop_cache[face_id] = data
                return data

        return None

    def get_top_face_matches(self, face_id: str, limit: int = 10) -> dict[str, Any]:
        """
        Given a clicked `face_id`, return:
        - Metadata & crop URL for the clicked face itself
        - The top `limit` (default 10) closest matching faces across all other detections.
        """
        target_det = self.detection_by_face_id.get(face_id)
        if target_det is None or target_det.face_embedding is None:
            raise ValueError(f"Face '{face_id}' not found or has no embedding.")

        anchor_emb = normalize_embedding(target_det.face_embedding)
        target_photo_id = self._get_photo_id(target_det.image_path)
        target_folders = self.get_folders_for_photo(target_det.image_path)
        target_folder_label = (
            target_folders[0]["folder_name"] if target_folders else "In No Folder"
        )

        matches: list[dict[str, Any]] = []
        for other_fid, other_det in self.detection_by_face_id.items():
            if other_fid == face_id or other_det.face_embedding is None:
                continue

            other_emb = normalize_embedding(other_det.face_embedding)
            sim = max(0.0, min(1.0, _dot_similarity(anchor_emb, other_emb)))
            other_photo_id = self._get_photo_id(other_det.image_path)
            other_folders = self.get_folders_for_photo(other_det.image_path)
            folder_label = (
                other_folders[0]["folder_name"] if other_folders else "In No Folder"
            )
            folder_pid = other_folders[0]["player_id"] if other_folders else None

            matches.append(
                {
                    "face_id": other_fid,
                    "crop_url": f"/api/face-crop?face_id={other_fid}",
                    "photo_id": other_photo_id,
                    "photo_name": other_det.image_path.name,
                    "player_id": folder_pid,
                    "folder_name": folder_label,
                    "similarity": round(sim, 4),
                    "similarity_pct": round(sim * 100.0, 1),
                }
            )

        matches.sort(key=lambda m: m["similarity"], reverse=True)

        return {
            "selected_face": {
                "face_id": face_id,
                "crop_url": f"/api/face-crop?face_id={face_id}",
                "photo_id": target_photo_id,
                "photo_name": target_det.image_path.name,
                "folder_name": target_folder_label,
                "confidence": round(
                    target_det.face_bbox.confidence if target_det.face_bbox else 1.0, 3
                ),
            },
            "matches": matches[: max(1, min(limit, 20))],
        }

    def _get_photo_id(self, photo_path: Path) -> str:
        for pid, p in self.photo_by_id.items():
            if p == photo_path:
                return pid
        return photo_path.name

    def get_folders_for_photo(self, photo_path: Path) -> list[dict[str, Any]]:
        """Return all player folders and jersey folders that currently contain `photo_path`."""
        assigned: list[dict[str, Any]] = []
        for pid, prof in sorted(
            self.profiles.items(), key=lambda kv: natural_sort_key(kv[1].folder_name)
        ):
            if photo_path in prof.photo_paths:
                sim = prof.photo_similarities.get(photo_path, 0.0)
                assigned.append(
                    {
                        "player_id": pid,
                        "folder_name": prof.folder_name,
                        "folder_type": "player",
                        "similarity": round(sim, 4),
                        "similarity_pct": round(sim * 100.0, 1),
                    }
                )
        for jnum, jfold in sorted(
            self.jersey_folders.items(), key=lambda kv: natural_sort_key(kv[0])
        ):
            if photo_path in jfold.photo_paths:
                prox = jfold.photo_proximities.get(photo_path, 0.0)
                assigned.append(
                    {
                        "player_id": jfold.folder_id,
                        "folder_name": jfold.folder_name,
                        "folder_type": "jersey",
                        "similarity": round(prox, 4),
                        "similarity_pct": round(prox * 100.0, 1),
                    }
                )
        return assigned

    def get_suggested_players_for_jersey(self, jfold: JerseyFolder) -> list[dict[str, Any]]:
        """
        Rank all PlayerProfiles by how closely and frequently `jfold.jersey_number`
        was detected on or near that player's bounding box (`bbox_proximity_score`).
        """
        best_prox_by_player: dict[str, float] = {}
        shared_count_by_player: dict[str, int] = {}

        for det in jfold.detections:
            prox = float(det.bbox_proximity_score or det.jersey_confidence or 0.5)
            # 1. Direct link: same PlayerDetection had both face_bbox and this jersey_number
            if det.assigned_player_id and det.assigned_player_id in self.profiles:
                pid = det.assigned_player_id
                best_prox_by_player[pid] = max(best_prox_by_player.get(pid, 0.0), prox)
                shared_count_by_player[pid] = shared_count_by_player.get(pid, 0) + 1
                continue

            # 2. Spatial link in the same photo: check proximity between number_bbox and
            #    other face-matched players' body/torso bounding boxes in the same photo
            if det.number_bbox is not None:
                for other_det in self.all_detections:
                    if (
                        other_det.image_path == det.image_path
                        and other_det.assigned_player_id
                        and other_det.assigned_player_id in self.profiles
                    ):
                        pid = other_det.assigned_player_id
                        ref_box = other_det.body_bbox or other_det.face_bbox
                        if ref_box is not None:
                            spatial_prox = ref_box.proximity_score(det.number_bbox)
                            if spatial_prox > 0.0:
                                best_prox_by_player[pid] = max(
                                    best_prox_by_player.get(pid, 0.0), spatial_prox
                                )
                                shared_count_by_player[pid] = (
                                    shared_count_by_player.get(pid, 0) + 1
                                )

        suggestions: list[dict[str, Any]] = []
        for pid, prof in self.profiles.items():
            prox = best_prox_by_player.get(pid, 0.0)
            shared = shared_count_by_player.get(pid, 0)
            suggestions.append(
                {
                    "player_id": pid,
                    "folder_name": prof.folder_name,
                    "bbox_proximity": round(prox, 4),
                    "bbox_proximity_pct": round(prox * 100.0, 1),
                    "shared_photos": shared,
                }
            )

        # Sort so players with the highest bounding-box proximity to this number come first,
        # then fall back to natural numerical/alphabetical order!
        suggestions.sort(
            key=lambda s: (
                -s["bbox_proximity"],
                -s["shared_photos"],
                natural_sort_key(s["folder_name"]),
            )
        )
        return suggestions

    def get_suggested_jerseys_for_player(self, player_id: str) -> list[dict[str, Any]]:
        """
        Rank all separate JerseyFolders (`#7`, `#10`, `#23`) for a given `player_id`
        by how closely and frequently each jersey number was detected on or near
        that player's bounding box (`bbox_proximity_score`).
        """
        suggestions: list[dict[str, Any]] = []
        for jnum, jfold in self.jersey_folders.items():
            if jfold.photo_count == 0:
                continue
            player_ranks = self.get_suggested_players_for_jersey(jfold)
            match_info = next((r for r in player_ranks if r["player_id"] == player_id), None)
            prox = match_info["bbox_proximity"] if match_info else 0.0
            prox_pct = match_info["bbox_proximity_pct"] if match_info else 0.0
            shared = match_info["shared_photos"] if match_info else 0
            suggestions.append(
                {
                    "jersey_number": jnum,
                    "folder_id": jfold.folder_id,
                    "folder_name": jfold.folder_name,
                    "photo_count": jfold.photo_count,
                    "bbox_proximity": prox,
                    "bbox_proximity_pct": prox_pct,
                    "shared_photos": shared,
                }
            )

        suggestions.sort(
            key=lambda s: (
                -s["bbox_proximity"],
                -s["shared_photos"],
                natural_sort_key(s["jersey_number"]),
            )
        )
        return suggestions

    def merge_jersey_into_player(
        self,
        jersey_number: str,
        target_player_id: str,
    ) -> dict[str, Any]:
        """
        Assign a jersey number `#[num]` to `profiles[target_player_id]`:
        - If a separate `jersey_folders[num]` exists, add all its photos into `target_player_id`
          and remove the separate jersey folder.
        - Rename the player folder to `<Player Name> #[num]`.
        """
        num = _sanitize_folder_name(jersey_number.lstrip("#").replace("jersey_", "").strip())
        if not num:
            raise ValueError("Please provide a valid jersey number.")

        target_prof = self.profiles.get(target_player_id)
        if target_prof is None:
            raise ValueError(f"Unknown player folder: {target_player_id}")

        jfold = self.jersey_folders.get(num)
        if jfold is not None:
            for photo_path in sorted(jfold.photo_paths, key=lambda p: natural_sort_key(p.name)):
                pid = self._get_photo_id(photo_path)
                self.move_or_add_photo(
                    photo_id=pid,
                    target_player_id=target_player_id,
                    source_player_id=None,
                    keep_in_source=True,
                )
            del self.jersey_folders[num]

        # Update player folder name to "<Player Name> #[num]" if not already present
        current_name = target_prof.folder_name
        tag = f"#{num}"
        if tag not in current_name:
            target_prof.custom_name = f"{current_name} {tag}"

        self.edit_count += 1
        return self.to_state_dict()

    def remove_photo_from_jersey_folder(
        self,
        photo_id: str,
        jersey_number: str,
    ) -> dict[str, Any]:
        """
        Remove `photo_id` from a separate jersey folder.
        Auto-deletes the jersey folder if it has 0 photos left.
        """
        num = jersey_number.lstrip("#").replace("jersey_", "").strip()
        photo_path = self.photo_by_id.get(photo_id)
        if photo_path is None:
            raise ValueError(f"Unknown photo: {photo_id}")
        jfold = self.jersey_folders.get(num)
        if jfold is None:
            raise ValueError(f"Unknown jersey folder: #{num}")

        if jfold.remove_photo(photo_path):
            self.edit_count += 1
            if jfold.photo_count == 0:
                del self.jersey_folders[num]
        return self.to_state_dict()

    def delete_jersey_folder(self, jersey_number: str) -> dict[str, Any]:
        """Delete a separate jersey folder completely."""
        num = jersey_number.lstrip("#").replace("jersey_", "").strip()
        if num not in self.jersey_folders:
            raise ValueError(f"Unknown jersey folder: #{num}")
        del self.jersey_folders[num]
        self.edit_count += 1
        return self.to_state_dict()

    def get_face_boxes_for_photo(self, photo_path: Path) -> tuple[int, int, list[dict[str, Any]]]:
        """Return `(image_width, image_height, face_boxes_list)` for drawing clickable face boxes."""
        if photo_path not in self.photo_dimensions:
            img_bgr = load_image_bgr(photo_path)
            if img_bgr is not None:
                h, w = img_bgr.shape[:2]
                self.photo_dimensions[photo_path] = (int(w), int(h))
            else:
                self.photo_dimensions[photo_path] = (800, 520)

        img_w, img_h = self.photo_dimensions[photo_path]
        boxes: list[dict[str, Any]] = []

        for det in self.all_detections:
            if det.image_path != photo_path or det.face_bbox is None:
                continue

            fid = self._get_face_id_for_detection(det) or ""
            folder_label = det.assigned_player_id or "In No Folder"
            sim_val = det.match_similarity
            if det.assigned_player_id and det.assigned_player_id in self.profiles:
                prof = self.profiles[det.assigned_player_id]
                folder_label = prof.folder_name
                sim_val = prof.photo_similarities.get(photo_path, det.match_similarity)

            boxes.append(
                {
                    "face_id": fid,
                    "crop_url": f"/api/face-crop?face_id={fid}",
                    "x1": det.face_bbox.x1,
                    "y1": det.face_bbox.y1,
                    "x2": det.face_bbox.x2,
                    "y2": det.face_bbox.y2,
                    "confidence": round(det.face_bbox.confidence, 3),
                    "player_id": det.assigned_player_id,
                    "label": folder_label,
                    "similarity": round(sim_val, 4),
                    "similarity_pct": round(sim_val * 100.0, 1),
                    "jersey_number": det.jersey_number,
                    "bbox_proximity": round(det.bbox_proximity_score, 4),
                    "bbox_proximity_pct": round(det.bbox_proximity_score * 100.0, 1),
                }
            )
        return img_w, img_h, boxes

    def get_number_and_body_boxes_for_photo(
        self, photo_path: Path
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return `(number_boxes, body_boxes)` for visualizing jersey numbers and player bodies."""
        number_boxes: list[dict[str, Any]] = []
        body_boxes: list[dict[str, Any]] = []

        for det in self.all_detections:
            if det.image_path != photo_path:
                continue
            if det.body_bbox is not None:
                body_boxes.append(
                    {
                        "x1": det.body_bbox.x1,
                        "y1": det.body_bbox.y1,
                        "x2": det.body_bbox.x2,
                        "y2": det.body_bbox.y2,
                        "confidence": round(det.body_bbox.confidence, 3),
                        "jersey_number": det.jersey_number,
                    }
                )
            if det.number_bbox is not None and det.jersey_number:
                prox = float(det.bbox_proximity_score or det.jersey_confidence or 0.5)
                number_boxes.append(
                    {
                        "x1": det.number_bbox.x1,
                        "y1": det.number_bbox.y1,
                        "x2": det.number_bbox.x2,
                        "y2": det.number_bbox.y2,
                        "jersey_number": det.jersey_number,
                        "confidence": round(det.jersey_confidence, 3),
                        "proximity": round(prox, 4),
                        "proximity_pct": round(prox * 100.0, 1),
                    }
                )
        return number_boxes, body_boxes

    @property
    def unassigned_photos(self) -> list[Path]:
        """
        Return all photos from `all_photos` that are currently in NO folder
        (neither a PlayerProfile folder nor a JerseyFolder).
        """
        assigned_any: set[Path] = set()
        for prof in self.profiles.values():
            assigned_any.update(prof.photo_paths)
        for jfold in self.jersey_folders.values():
            assigned_any.update(jfold.photo_paths)
        return [p for p in self.all_photos if p not in assigned_any]

    def remove_photo_from_folder(self, photo_id: str, player_id: str) -> dict[str, Any]:
        """
        Remove `photo_id` from `player_id` (or from a jersey folder if `player_id` starts with `jersey_`).
        If the folder has 0 photos left after removal, automatically delete the folder.
        """
        if player_id.startswith("jersey_"):
            return self.remove_photo_from_jersey_folder(photo_id, player_id)

        photo_path = self.photo_by_id.get(photo_id)
        if photo_path is None:
            raise ValueError(f"Unknown photo: {photo_id}")
        prof = self.profiles.get(player_id)
        if prof is None:
            raise ValueError(f"Unknown folder: {player_id}")

        removed = prof.remove_photo(photo_path)
        if removed:
            self.edit_count += 1
            if prof.photo_count == 0:
                del self.profiles[player_id]
        return self.to_state_dict()

    def move_or_add_photo(
        self,
        photo_id: str,
        target_player_id: str,
        source_player_id: Optional[str] = None,
        keep_in_source: bool = False,
    ) -> dict[str, Any]:
        """
        Move (or copy if `keep_in_source=True`) `photo_id` into `target_player_id`.
        Automatically calculates the face similarity between `photo_id` and `target_player_id`,
        and deletes the source folder if it becomes empty.
        """
        photo_path = self.photo_by_id.get(photo_id)
        if photo_path is None:
            raise ValueError(f"Unknown photo: {photo_id}")
        target_prof = self.profiles.get(target_player_id)
        if target_prof is None:
            raise ValueError(f"Unknown target folder: {target_player_id}")

        if source_player_id and not keep_in_source and source_player_id != target_player_id:
            if source_player_id.startswith("jersey_"):
                jnum = source_player_id.replace("jersey_", "")
                jfold = self.jersey_folders.get(jnum)
                if jfold is not None:
                    jfold.remove_photo(photo_path)
                    if jfold.photo_count == 0:
                        del self.jersey_folders[jnum]
            else:
                src_prof = self.profiles.get(source_player_id)
                if src_prof is not None:
                    src_prof.remove_photo(photo_path)
                    if src_prof.photo_count == 0:
                        del self.profiles[source_player_id]

        photo_dets = [d for d in self.all_detections if d.image_path == photo_path]
        target_centroid = target_prof.centroid_embedding

        best_det: Optional[PlayerDetection] = None
        best_sim: float = 0.0

        if photo_dets:
            if target_centroid is not None:
                norm_centroid = normalize_embedding(target_centroid)
                for d in photo_dets:
                    if d.has_face and d.face_embedding is not None:
                        sim = max(
                            0.0,
                            min(1.0, _dot_similarity(normalize_embedding(d.face_embedding), norm_centroid)),
                        )
                        if best_det is None or sim > best_sim:
                            best_det = d
                            best_sim = sim
            if best_det is None:
                best_det = photo_dets[0]

        if photo_path not in target_prof.photo_paths:
            if best_det is not None:
                orig_fid = self._get_face_id_for_detection(best_det)
                cloned = PlayerDetection(
                    image_path=photo_path,
                    face_bbox=best_det.face_bbox,
                    face_embedding=best_det.face_embedding,
                    body_bbox=best_det.body_bbox,
                    number_bbox=best_det.number_bbox,
                    jersey_number=best_det.jersey_number,
                    jersey_confidence=best_det.jersey_confidence,
                    bbox_proximity_score=best_det.bbox_proximity_score,
                    assigned_player_id=target_player_id,
                    match_similarity=best_sim,
                )
                if orig_fid:
                    self.face_id_by_detection[id(cloned)] = orig_fid
                target_prof.add_detection(cloned)
            else:
                target_prof.photo_paths.add(photo_path)
                target_prof.photo_similarities[photo_path] = 0.0

            target_prof.recompute_similarities()
            self.edit_count += 1

        return self.to_state_dict()

    def merge_folders(self, source_player_id: str, target_player_id: str) -> dict[str, Any]:
        """
        Merge all photos from `source_player_id` into `target_player_id` and remove
        the empty source folder.
        """
        if source_player_id == target_player_id:
            return self.to_state_dict()
        src_prof = self.profiles.get(source_player_id)
        target_prof = self.profiles.get(target_player_id)
        if src_prof is None or target_prof is None:
            raise ValueError("Source or target folder not found.")

        for photo_path in list(src_prof.photo_paths):
            pid = self._get_photo_id(photo_path)
            self.move_or_add_photo(
                photo_id=pid,
                target_player_id=target_player_id,
                source_player_id=source_player_id,
                keep_in_source=False,
            )

        self.profiles.pop(source_player_id, None)
        self.edit_count += 1
        return self.to_state_dict()

    def create_folder(
        self,
        folder_name: str,
        initial_photo_id: Optional[str] = None,
        source_player_id: Optional[str] = None,
        keep_in_source: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new player folder in the session.
        Optionally moves/adds `initial_photo_id` into the newly created folder immediately.
        """
        safe_name = _sanitize_folder_name(folder_name)
        base_id = safe_name
        counter = 2
        while base_id in self.profiles:
            base_id = f"{safe_name}_{counter}"
            counter += 1

        self.profiles[base_id] = PlayerProfile(
            player_id=base_id,
            custom_name=safe_name,
            is_known_reference=False,
        )
        self.edit_count += 1

        if initial_photo_id:
            state = self.move_or_add_photo(
                photo_id=initial_photo_id,
                target_player_id=base_id,
                source_player_id=source_player_id,
                keep_in_source=keep_in_source,
            )
        else:
            state = self.to_state_dict()

        state["created_player_id"] = base_id
        return state

    def delete_folder(self, player_id: str) -> dict[str, Any]:
        """
        Delete `player_id` folder completely (supports both player folders and `jersey_X` folders).
        """
        if player_id.startswith("jersey_"):
            return self.delete_jersey_folder(player_id)
        if player_id not in self.profiles:
            raise ValueError(f"Unknown folder: {player_id}")
        del self.profiles[player_id]
        self.edit_count += 1
        return self.to_state_dict()

    def rename_folder(self, player_id: str, new_name: str) -> dict[str, Any]:
        """Rename an existing player folder."""
        prof = self.profiles.get(player_id)
        if prof is None:
            raise ValueError(f"Unknown folder: {player_id}")
        prof.custom_name = _sanitize_folder_name(new_name)
        self.edit_count += 1
        return self.to_state_dict()

    def compare_photo(self, photo_id: str) -> dict[str, Any]:
        """Return pairwise similarity numbers, face boxes, and jersey number boxes for `photo_id`."""
        photo_path = self.photo_by_id.get(photo_id)
        if photo_path is None:
            raise ValueError(f"Unknown photo: {photo_id}")
        result = compute_pairwise_photo_similarities(
            target_photo=photo_path,
            all_detections=self.all_detections,
            profiles=self.profiles,
        )
        img_w, img_h, face_boxes = self.get_face_boxes_for_photo(photo_path)
        number_boxes, body_boxes = self.get_number_and_body_boxes_for_photo(photo_path)
        result["photo_id"] = photo_id
        result["photo_url"] = f"/api/photo?id={photo_id}"
        result["image_width"] = img_w
        result["image_height"] = img_h
        result["face_boxes"] = face_boxes
        result["number_boxes"] = number_boxes
        result["body_boxes"] = body_boxes
        return result

    def _build_export_profiles(self) -> dict[str, PlayerProfile]:
        """Combine non-empty PlayerProfiles and any remaining unmerged JerseyFolders for export."""
        combined_profiles: dict[str, PlayerProfile] = {
            pid: prof for pid, prof in self.profiles.items() if prof.photo_count > 0
        }
        for jnum, jfold in self.jersey_folders.items():
            if jfold.photo_count > 0:
                j_prof_id = f"Jersey_#{jnum}"
                j_prof = PlayerProfile(player_id=j_prof_id, custom_name=j_prof_id)
                for p in jfold.photo_paths:
                    j_prof.photo_paths.add(p)
                combined_profiles[j_prof_id] = j_prof
        return combined_profiles

    def start_background_export(
        self,
        *,
        destination_type: str = "local",
        local_output_dir: Optional[str] = None,
        dropbox_output_folder: Optional[str] = None,
        file_mode: str = "copy",
        dropbox_manager: Optional[Any] = None,
        shared_folder_id: Optional[str] = None,
    ) -> None:
        """Export sorted folders to local disk or Dropbox on a background thread with progress updates."""
        initial_stage = (
            "Preparing Dropbox export..."
            if destination_type == "dropbox"
            else "Preparing local folder export..."
        )
        self._update_progress(
            active=True,
            stage=initial_stage,
            current=0,
            total=1,
            current_file="",
            error=None,
        )
        thread = threading.Thread(
            target=self._run_export_worker,
            kwargs={
                "destination_type": destination_type,
                "local_output_dir": local_output_dir,
                "dropbox_output_folder": dropbox_output_folder,
                "file_mode": file_mode,
                "dropbox_manager": dropbox_manager,
                "shared_folder_id": shared_folder_id,
            },
            daemon=True,
        )
        thread.start()

    def _run_export_worker(
        self,
        *,
        destination_type: str,
        local_output_dir: Optional[str],
        dropbox_output_folder: Optional[str],
        file_mode: str,
        dropbox_manager: Optional[Any],
        shared_folder_id: Optional[str],
    ) -> None:
        def _export_progress(done: int, total: int, label: str) -> None:
            self._update_progress(
                active=True,
                stage=label,
                current=done,
                total=max(1, total),
                current_file="",
            )

        try:
            self.commit_export(
                destination_type=destination_type,
                local_output_dir=local_output_dir,
                dropbox_output_folder=dropbox_output_folder,
                file_mode=file_mode,
                dropbox_manager=dropbox_manager,
                shared_folder_id=shared_folder_id,
                progress_callback=_export_progress,
            )
            self._update_progress(
                active=False,
                stage="Complete",
                current=1,
                total=1,
            )
        except Exception as exc:
            self._update_progress(
                active=False,
                stage="Error",
                current=0,
                total=0,
                error=str(exc),
            )

    def commit_export(
        self,
        *,
        destination_type: str = "local",
        local_output_dir: Optional[str] = None,
        dropbox_output_folder: Optional[str] = None,
        file_mode: str = "copy",
        dropbox_manager: Optional[Any] = None,
        shared_folder_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict[str, Any]:
        """
        Write curated folders either to a local folder (`destination_type='local'`) or
        directly to Dropbox (`destination_type='dropbox'`), then clean up temp files and
        return to the Homescreen (`mode = 'home'`).
        """
        combined_profiles = self._build_export_profiles()
        unassigned = self.unassigned_photos
        clean_mode = "move" if str(file_mode).lower() == "move" else "copy"
        dest_kind = "dropbox" if str(destination_type).lower() == "dropbox" else "local"

        if dest_kind == "dropbox":
            if dropbox_manager is None:
                raise ValueError("Dropbox manager is required to export to Dropbox.")
            target_dbx = dropbox_output_folder
            if target_dbx is None or not str(target_dbx).strip():
                raise ValueError("Please select a destination Dropbox folder.")

            ns_id = (
                shared_folder_id
                if shared_folder_id is not None
                else (self.source_dropbox_shared_folder_id or None)
            )
            summary = organize_output_to_dropbox(
                profiles=combined_profiles,
                unassigned_detections=[],
                no_face_photos=unassigned,
                total_photos_scanned=len(self.all_photos),
                config=self.config,
                dropbox_manager=dropbox_manager,
                dropbox_output_folder=str(target_dbx),
                file_mode=clean_mode,
                dropbox_entry_by_path=self.dropbox_entry_by_path,
                shared_folder_id=ns_id,
                progress_callback=progress_callback,
            )
            self.last_commit_summary = summary
            self.last_commit_output_dir = str(summary["output_dir"])
            self.last_commit_destination_type = "dropbox"
        else:
            out_dir = (
                Path(local_output_dir).expanduser().resolve()
                if local_output_dir and str(local_output_dir).strip()
                else self.config.output_dir
            )
            summary = organize_output(
                profiles=combined_profiles,
                unassigned_detections=[],
                no_face_photos=unassigned,
                total_photos_scanned=len(self.all_photos),
                config=self.config,
                output_dir_override=out_dir,
                file_mode=clean_mode,
                dropbox_manager=dropbox_manager,
                dropbox_entry_by_path=self.dropbox_entry_by_path,
                dropbox_loaded_as_thumbnails=self.dropbox_loaded_as_thumbnails,
                progress_callback=progress_callback,
            )
            print_summary_table(summary, out_dir)
            self.last_commit_summary = summary
            self.last_commit_output_dir = str(out_dir)
            self.last_commit_destination_type = "local"

        self.reset_to_home()
        return self.to_state_dict()

    def commit_to_disk(self) -> dict[str, Any]:
        """
        Write the curated virtual folders to real folders on disk in `config.output_dir`,
        then reset the session back to the Homescreen (`mode = 'home'`).
        """
        return self.commit_export(destination_type="local")

    def _serialize_photo(
        self,
        photo_path: Path,
        sim_override: Optional[float] = None,
        proximity_override: Optional[float] = None,
    ) -> dict[str, Any]:
        pid = self._get_photo_id(photo_path)
        assigned = self.get_folders_for_photo(photo_path)
        dets_in_photo = [d for d in self.all_detections if d.image_path == photo_path]
        sim = (
            sim_override
            if sim_override is not None
            else max((item["similarity"] for item in assigned), default=0.0)
        )
        best_prox = (
            proximity_override
            if proximity_override is not None
            else max((d.bbox_proximity_score for d in dets_in_photo), default=0.0)
        )
        jerseys = sorted(
            {d.jersey_number for d in dets_in_photo if d.has_jersey and d.jersey_number},
            key=natural_sort_key,
        )
        img_w, img_h, face_boxes = self.get_face_boxes_for_photo(photo_path)
        number_boxes, body_boxes = self.get_number_and_body_boxes_for_photo(photo_path)
        return {
            "id": pid,
            "name": photo_path.name,
            "url": f"/api/photo?id={pid}",
            "similarity": round(sim, 4),
            "similarity_pct": round(sim * 100.0, 1),
            "bbox_proximity": round(best_prox, 4),
            "bbox_proximity_pct": round(best_prox * 100.0, 1),
            "has_face": any(d.has_face for d in dets_in_photo),
            "jerseys": jerseys,
            "assigned_folders": assigned,
            "in_no_folder": len(assigned) == 0,
            "image_width": img_w,
            "image_height": img_h,
            "face_boxes": face_boxes,
            "number_boxes": number_boxes,
            "body_boxes": body_boxes,
        }

    def to_state_dict(self) -> dict[str, Any]:
        """Serialize the full session state in natural numerical and alphabetical order."""
        unassigned = self.unassigned_photos
        unassigned_names = [self._get_photo_id(p) for p in unassigned]

        folders_payload: list[dict[str, Any]] = []
        sorted_profiles = sorted(
            self.profiles.values(),
            key=lambda p: natural_sort_key(p.folder_name),
        )

        for prof in sorted_profiles:
            folder_photos = [
                self._serialize_photo(p, sim_override=prof.photo_similarities.get(p, 0.0))
                for p in sorted(
                    prof.photo_paths,
                    key=lambda path: natural_sort_key(path.name),
                )
            ]

            ref_fid = self._get_best_face_id_for_profile(prof)
            ref_crop_url = f"/api/face-crop?face_id={ref_fid}" if ref_fid else None

            folders_payload.append(
                {
                    "player_id": prof.player_id,
                    "folder_name": prof.folder_name,
                    "is_known_reference": prof.is_known_reference,
                    "primary_jersey_number": prof.primary_jersey_number,
                    "photo_count": prof.photo_count,
                    "average_similarity": prof.average_similarity,
                    "average_similarity_pct": round(prof.average_similarity * 100.0, 1),
                    "reference_face_id": ref_fid,
                    "reference_crop_url": ref_crop_url,
                    "suggested_jerseys": self.get_suggested_jerseys_for_player(prof.player_id),
                    "photos": folder_photos,
                }
            )

        # Serialize separate Jersey Number folders at the bottom, sorted numerically (#1, #2, #10, #23)
        jersey_folders_payload: list[dict[str, Any]] = []
        sorted_jerseys = sorted(
            self.jersey_folders.values(),
            key=lambda jf: natural_sort_key(jf.jersey_number),
        )
        for jfold in sorted_jerseys:
            if jfold.photo_count == 0:
                continue
            # Sort photos inside a Jersey Folder so numbers closer to / on the player's bbox come first!
            j_photos = [
                self._serialize_photo(
                    p,
                    sim_override=jfold.photo_proximities.get(p, 0.0),
                    proximity_override=jfold.photo_proximities.get(p, 0.0),
                )
                for p in sorted(
                    jfold.photo_paths,
                    key=lambda path: (
                        -jfold.photo_proximities.get(path, 0.0),
                        natural_sort_key(path.name),
                    ),
                )
            ]
            jersey_folders_payload.append(
                {
                    "folder_id": jfold.folder_id,
                    "jersey_number": jfold.jersey_number,
                    "folder_name": jfold.folder_name,
                    "photo_count": jfold.photo_count,
                    "average_proximity": jfold.average_proximity,
                    "average_proximity_pct": round(jfold.average_proximity * 100.0, 1),
                    "suggested_players": self.get_suggested_players_for_jersey(jfold),
                    "photos": j_photos,
                }
            )

        all_photos_payload = [
            self._serialize_photo(p)
            for p in sorted(self.all_photos, key=lambda path: natural_sort_key(path.name))
        ]

        dropbox_photo_ids = {
            self._get_photo_id(p): entry.file_id
            for p, entry in self.dropbox_entry_by_path.items()
            if p in self.all_photos
        }

        return {
            "mode": self.mode,
            "source_type": self.source_type,
            "source_dropbox_folder": self.source_dropbox_folder,
            "dropbox_photo_ids": dropbox_photo_ids,
            "output_dir": str(self.config.output_dir),
            "total_photos": len(self.all_photos),
            "total_folders": len(folders_payload),
            "total_jersey_folders": len(jersey_folders_payload),
            "edit_count": self.edit_count,
            "unassigned_count": len(unassigned),
            "unassigned_photos": unassigned_names,
            "has_unassigned_warning": len(unassigned) > 0,
            "all_photos": all_photos_payload,
            "folders": folders_payload,
            "jersey_folders": jersey_folders_payload,
            "last_commit": self.last_commit_summary,
            "last_commit_output_dir": self.last_commit_output_dir,
            "last_commit_destination_type": self.last_commit_destination_type,
        }
