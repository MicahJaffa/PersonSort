"""Player body bounding box detection, face-to-body linking, and spatial jersey number OCR."""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Optional

try:
    import torch
    if hasattr(torch, "set_num_threads"):
        torch.set_num_threads(min(4, torch.get_num_threads()))
except Exception:
    torch = None  # type: ignore

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

from config import APP_RESOURCE_DIR, MODELS_CACHE_DIR, SortConfig
from image_loader import crop_region, load_image_bgr
from models import BoundingBox, PlayerDetection

logger = logging.getLogger(__name__)

# Valid sports jersey numbers: 1 to 2 digits (e.g. "0", "00", "1".."99")
JERSEY_NUMBER_PATTERN = re.compile(r"^\d{1,2}$")


class BodyJerseyDetector:
    """
    Detects player body bounding boxes (`body_bbox`), links detected faces to player bodies,
    and runs OCR inside each player's torso bounding box to read jersey numbers.

    Every detected jersey number is scored and sorted by `bbox_proximity_score` (how close
    and centered the number is on the player's bounding box) so background scoreboard numbers
    or adjacent players' numbers are filtered out.
    """

    def __init__(self, config: SortConfig, yolo_model_name: str = "yolov8n.pt") -> None:
        self.config = config
        self.yolo_model_name = yolo_model_name
        self._cache_dir = MODELS_CACHE_DIR
        self._bundled_cache_dir = APP_RESOURCE_DIR / ".models_cache"
        self._yolo = None
        self._yolo_checked = False
        self._ocr_reader = None
        self._ocr_checked = False

    def _has_cuda_gpu(self) -> bool:
        """Return True only if GPU execution is enabled and CUDA is actually usable."""
        if not self.config.use_gpu or torch is None:
            return False
        try:
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def _ensure_yolo(self) -> Any:
        """Lazily load YOLOv8 person detector if `ultralytics` is installed."""
        if self._yolo_checked:
            return self._yolo
        self._yolo_checked = True
        try:
            from ultralytics import YOLO  # type: ignore

            bundled_path = self._bundled_cache_dir / self.yolo_model_name
            if bundled_path.is_file():
                model_path = bundled_path
            else:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                model_path = self._cache_dir / self.yolo_model_name
            self._yolo = YOLO(str(model_path))
            logger.info("Loaded YOLO person detector (%s)", model_path)
        except Exception as exc:
            logger.info(
                "YOLOv8 ('ultralytics') not available (%s); falling back to face-projected torso boxes.",
                exc,
            )
            self._yolo = None
        return self._yolo

    def _ensure_ocr(self) -> Any:
        """Lazily load EasyOCR reader if `easyocr` is installed, with clean CPU fallback."""
        if self._ocr_checked:
            return self._ocr_reader
        self._ocr_checked = True
        try:
            import easyocr  # type: ignore

            bundled_ocr = self._bundled_cache_dir / "easyocr"
            if bundled_ocr.is_dir() and any(bundled_ocr.glob("*.pth")):
                ocr_dir = bundled_ocr
            else:
                ocr_dir = self._cache_dir / "easyocr"
                ocr_dir.mkdir(parents=True, exist_ok=True)

            use_gpu = self._has_cuda_gpu()
            try:
                self._ocr_reader = easyocr.Reader(
                    ["en"],
                    gpu=use_gpu,
                    model_storage_directory=str(ocr_dir),
                    user_network_directory=str(ocr_dir),
                    verbose=False,
                )
            except Exception as gpu_exc:
                if not use_gpu:
                    raise
                logger.warning(
                    "EasyOCR GPU init failed (%s); falling back cleanly to CPU.",
                    gpu_exc,
                )
                self._ocr_reader = easyocr.Reader(
                    ["en"],
                    gpu=False,
                    model_storage_directory=str(ocr_dir),
                    user_network_directory=str(ocr_dir),
                    verbose=False,
                )
            logger.info("Initialized EasyOCR for jersey number recognition (gpu=%s).", use_gpu)
        except Exception as exc:
            logger.info(
                "EasyOCR ('easyocr') not available (%s); jersey OCR will be skipped until installed.",
                exc,
            )
            self._ocr_reader = None
        return self._ocr_reader

    def detect_person_boxes(self, image_bgr: np.ndarray) -> list[BoundingBox]:
        """
        Detect foreground person bounding boxes `[x1, y1, x2, y2]` in `image_bgr`.
        Filters out small background spectators in the stands.
        """
        yolo = self._ensure_yolo()
        if yolo is None or image_bgr is None or image_bgr.size == 0:
            return []

        img_h, img_w = image_bgr.shape[:2]
        # Only detect significant foreground people (filters out small background spectators in stands)
        min_body_height = max(140, int(img_h * 0.14))

        person_boxes: list[BoundingBox] = []
        try:
            # COCO class 0 == 'person', downscaled to 640px for fast CPU inference
            results = yolo.predict(
                source=image_bgr,
                classes=[0],
                conf=0.45,
                imgsz=640,
                device=0 if self._has_cuda_gpu() else "cpu",
                verbose=False,
            )
            for res in results:
                boxes = getattr(res, "boxes", None)
                if boxes is None:
                    continue
                xyxy_list = boxes.xyxy.cpu().numpy()
                conf_list = boxes.conf.cpu().numpy()
                for coords, conf in zip(xyxy_list, conf_list):
                    x1 = max(0, int(coords[0]))
                    y1 = max(0, int(coords[1]))
                    x2 = min(img_w, int(coords[2]))
                    y2 = min(img_h, int(coords[3]))
                    b = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=float(conf))
                    if b.height >= min_body_height and b.width >= self.config.min_face_size:
                        person_boxes.append(b)
        except Exception as exc:
            logger.warning("YOLO person detection failed: %s", exc)

        # Sort largest foreground player bodies first
        person_boxes.sort(key=lambda b: b.area * b.confidence, reverse=True)
        return person_boxes

    def link_faces_and_bodies(
        self,
        image_path: Path,
        image_width: int,
        image_height: int,
        face_detections: list[PlayerDetection],
        person_boxes: list[BoundingBox],
    ) -> list[PlayerDetection]:
        """
        Associate detected faces with detected player body boxes:
        1. If a face lies inside the upper portion of a `person_box`, link them.
        2. If a face has no matching `person_box`, estimate a torso/body box below the face.
        3. If a prominent foreground `person_box` has NO face inside it (player turned away),
           create a new no-face `PlayerDetection` for jersey number reading.
        """
        used_person_indices: set[int] = set()
        combined: list[PlayerDetection] = []

        for det in face_detections:
            if det.face_bbox is None:
                combined.append(det)
                continue

            best_p_idx: Optional[int] = None
            best_overlap: float = 0.0
            _, face_cy = det.face_bbox.center

            for idx, pbox in enumerate(person_boxes):
                if idx in used_person_indices:
                    continue
                # Face should overlap with the body box and sit in the upper 55% of the body box
                if not pbox.contains_box(det.face_bbox, min_overlap_ratio=0.50):
                    continue
                if face_cy > pbox.y1 + pbox.height * 0.55:
                    continue

                overlap = pbox.intersection_area(det.face_bbox) / float(max(1, det.face_bbox.area))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_p_idx = idx

            if best_p_idx is not None:
                used_person_indices.add(best_p_idx)
                det.body_bbox = person_boxes[best_p_idx]
            else:
                # Project a body/torso bounding box downward from the face box
                torso = det.face_bbox.torso_from_face_box(image_width, image_height)
                det.body_bbox = BoundingBox(
                    x1=min(det.face_bbox.x1, torso.x1),
                    y1=det.face_bbox.y1,
                    x2=max(det.face_bbox.x2, torso.x2),
                    y2=torso.y2,
                    confidence=det.face_bbox.confidence,
                )

            combined.append(det)

        # Only create no-face PlayerDetections for at most 3 prominent foreground players
        unlinked = [
            person_boxes[idx]
            for idx in range(len(person_boxes))
            if idx not in used_person_indices
        ]
        min_unlinked_h = int(image_height * 0.18)
        min_unlinked_area = int(image_width * image_height * 0.03)
        prominent_unlinked = [
            p for p in unlinked if p.height >= min_unlinked_h and p.area >= min_unlinked_area
        ]
        prominent_unlinked.sort(key=lambda b: b.area, reverse=True)

        for pbox in prominent_unlinked[:3]:
            combined.append(
                PlayerDetection(
                    image_path=image_path,
                    face_bbox=None,
                    face_embedding=None,
                    body_bbox=pbox,
                )
            )

        return combined

    def _get_candidate_torso_boxes(
        self,
        detection: PlayerDetection,
        img_w: int,
        img_h: int,
    ) -> list[BoundingBox]:
        """
        Return the single primary torso bounding box for `detection`.
        Prefers `torso_from_body_box` when a body detection is available;
        falls back to `torso_from_face_box` if only a face detection exists.
        """
        if detection.body_bbox is not None and detection.body_bbox.area > 0:
            b_torso = detection.body_bbox.torso_from_body_box(img_w, img_h)
            if b_torso.area > 0:
                return [b_torso]

        if detection.face_bbox is not None and detection.face_bbox.area > 0:
            f_torso = detection.face_bbox.torso_from_face_box(img_w, img_h)
            if f_torso.area > 0:
                return [f_torso]

        return []

    def _preprocess_torso_variants(
        self,
        torso_bgr: np.ndarray,
    ) -> list[tuple[np.ndarray, float, int]]:
        """
        Produce preprocessed image variants of `torso_bgr` for athletic jersey OCR:
        Returns a list of `(variant_img, scale_factor, pad_px)` tuples:
        1. CLAHE local contrast-enhanced image (makes numbers pop on shadows/mesh).
        2. Inverted-polarity grayscale image (used only as fallback if Pass 1 misses).
        """
        if cv2 is None or np is None or torso_bgr is None or torso_bgr.size == 0:
            return [(torso_bgr, 1.0, 0)]

        h, w = torso_bgr.shape[:2]
        if h < 12 or w < 12:
            return []

        scale = 1.0
        if w < 180:
            scale = min(2.0, max(1.0, 180.0 / float(w)))
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            scaled_bgr = cv2.resize(torso_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        else:
            scaled_bgr = torso_bgr

        pad_px = 8
        padded_bgr = cv2.copyMakeBorder(
            scaled_bgr,
            pad_px,
            pad_px,
            pad_px,
            pad_px,
            borderType=cv2.BORDER_REPLICATE,
        )

        # Variant 1: CLAHE on LAB Luminance channel
        lab = cv2.cvtColor(padded_bgr, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        cl_l = clahe.apply(l_chan)
        clahe_bgr = cv2.cvtColor(cv2.merge((cl_l, a_chan, b_chan)), cv2.COLOR_LAB2BGR)

        # Variant 2: Inverted polarity grayscale (helps light-on-dark jersey numbers)
        inv_gray = cv2.bitwise_not(cl_l)

        return [
            (clahe_bgr, scale, pad_px),
            (inv_gray, scale, pad_px),
        ]

    @staticmethod
    def _is_valid_athletic_digit_box(
        cleaned_text: str,
        ocr_conf: float,
        num_bbox: BoundingBox,
        torso_box: BoundingBox,
    ) -> bool:
        """
        Validate that a candidate OCR detection has realistic athletic jersey digit
        geometry and confidence (filtering out jersey stripes, wrinkles, and tiny logos).
        """
        if not JERSEY_NUMBER_PATTERN.match(cleaned_text):
            return False

        # Wrinkles, vertical stripes, and collar edges often produce false "1", "11", or "7"
        if cleaned_text in {"1", "11", "7"}:
            if ocr_conf < 0.68:
                return False
        else:
            if ocr_conf < 0.45:
                return False

        if num_bbox.height < 12 or num_bbox.width < 5:
            return False

        rel_h = num_bbox.height / float(max(1, torso_box.height))
        if rel_h < 0.12 or rel_h > 0.82:
            return False

        aspect = num_bbox.width / float(max(1, num_bbox.height))
        if aspect < 0.18 or aspect > 2.50:
            return False

        return True

    def detect_jersey_for_player(
        self,
        image_bgr: np.ndarray,
        detection: PlayerDetection,
    ) -> None:
        """
        Scan the player's candidate torso bounding boxes using multi-pass CLAHE + polarity
        preprocessing and athletic digit geometry filtering.
        Populates `detection.jersey_number`, `detection.number_bbox`, `detection.jersey_confidence`,
        and `detection.bbox_proximity_score`.
        """
        reader = self._ensure_ocr()
        if reader is None or image_bgr is None or image_bgr.size == 0:
            return

        img_h, img_w = image_bgr.shape[:2]
        torso_candidates = self._get_candidate_torso_boxes(detection, img_w, img_h)
        if not torso_candidates:
            return

        # Map number_str -> list of (num_bbox, ocr_conf, prox_score)
        grouped_hits: dict[str, list[tuple[BoundingBox, float, float]]] = {}

        for torso_box in torso_candidates:
            torso_crop = crop_region(image_bgr, torso_box, padding_ratio=0.0)
            if torso_crop is None or torso_crop.size == 0:
                continue

            variants = self._preprocess_torso_variants(torso_crop)
            for var_idx, (var_img, scale, pad_px) in enumerate(variants):
                try:
                    ocr_results = reader.readtext(
                        var_img,
                        allowlist="0123456789",
                        detail=1,
                        mag_ratio=1.0,
                        text_threshold=0.50,
                        low_text=0.30,
                        contrast_ths=0.10,
                        adjust_contrast=0.5,
                        batch_size=4,
                    )
                except Exception as exc:
                    logger.debug("EasyOCR readtext failed on %s: %s", detection.image_path.name, exc)
                    continue

                found_valid_in_pass = False
                for bbox_pts, raw_text, conf in ocr_results:
                    cleaned = str(raw_text).strip()
                    ocr_conf = float(conf)

                    xs = [float(pt[0]) for pt in bbox_pts]
                    ys = [float(pt[1]) for pt in bbox_pts]
                    orig_x1 = int(round((min(xs) - pad_px) / scale))
                    orig_y1 = int(round((min(ys) - pad_px) / scale))
                    orig_x2 = int(round((max(xs) - pad_px) / scale))
                    orig_y2 = int(round((max(ys) - pad_px) / scale))

                    nx1 = max(0, min(img_w, torso_box.x1 + orig_x1))
                    ny1 = max(0, min(img_h, torso_box.y1 + orig_y1))
                    nx2 = max(0, min(img_w, torso_box.x1 + orig_x2))
                    ny2 = max(0, min(img_h, torso_box.y1 + orig_y2))

                    num_bbox = BoundingBox(x1=nx1, y1=ny1, x2=nx2, y2=ny2, confidence=ocr_conf)
                    if not self._is_valid_athletic_digit_box(cleaned, ocr_conf, num_bbox, torso_box):
                        continue

                    prox = torso_box.proximity_score(num_bbox)
                    if prox <= 0.0:
                        continue

                    grouped_hits.setdefault(cleaned, []).append((num_bbox, ocr_conf, prox))
                    found_valid_in_pass = True

                # If Pass 1 (CLAHE) found a valid athletic number, skip Pass 2 for speed
                if found_valid_in_pass:
                    break

        if not grouped_hits:
            return

        best_candidate: Optional[tuple[str, BoundingBox, float, float, float]] = None
        # Tuple: (num_str, best_bbox, boosted_conf, best_prox, rank_score)

        for num_str, hits in grouped_hits.items():
            best_bbox, max_conf, best_prox = max(
                hits, key=lambda h: 0.65 * h[2] + 0.35 * h[1]
            )
            # Multi-pass consensus bonus when multiple passes/zones read the same number
            boosted_conf = min(1.0, max_conf + 0.06 * (len(hits) - 1))
            rank_score = 0.65 * best_prox + 0.35 * boosted_conf
            if best_candidate is None or rank_score > best_candidate[4]:
                best_candidate = (num_str, best_bbox, boosted_conf, best_prox, rank_score)

        if best_candidate is not None:
            num_str, num_bbox, ocr_conf, prox, _ = best_candidate
            detection.jersey_number = num_str
            detection.number_bbox = num_bbox
            detection.jersey_confidence = round(ocr_conf, 4)
            detection.bbox_proximity_score = round(prox, 4)

    def enrich_image_detections(
        self,
        image_path: Path,
        face_detections: list[PlayerDetection],
        image_bgr: Optional[np.ndarray] = None,
    ) -> list[PlayerDetection]:
        """
        Full per-image pipeline:
        1. Detect player body bounding boxes (`person_boxes`).
        2. Link `face_detections` to `person_boxes` (and create no-face player detections).
        3. Detect jersey numbers inside each player's torso box and compute `bbox_proximity_score`.
        """
        if image_bgr is None:
            image_bgr = load_image_bgr(image_path)
        if image_bgr is None or image_bgr.size == 0:
            return face_detections

        img_h, img_w = image_bgr.shape[:2]
        person_boxes = self.detect_person_boxes(image_bgr)
        player_detections = self.link_faces_and_bodies(
            image_path=image_path,
            image_width=img_w,
            image_height=img_h,
            face_detections=face_detections,
            person_boxes=person_boxes,
        )

        for det in player_detections:
            self.detect_jersey_for_player(image_bgr, det)

        # Sort detections so those with highest face quality or closest jersey proximity come first
        player_detections.sort(
            key=lambda d: (d.face_quality_score, d.bbox_proximity_score, d.jersey_confidence),
            reverse=True,
        )
        return player_detections
