"""Foreground player face detection and 512-D ArcFace embedding extraction."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore

from config import APP_RESOURCE_DIR, MODELS_CACHE_DIR, SortConfig
from image_loader import load_image_bgr
from models import BoundingBox, PlayerDetection

logger = logging.getLogger(__name__)


def normalize_embedding(vec: np.ndarray | list[float]) -> np.ndarray | list[float]:
    """L2-normalize a 1-D embedding vector to unit length for cosine similarity."""
    if np is not None:
        arr = np.asarray(vec, dtype=np.float32).flatten()
        norm = float(np.linalg.norm(arr))
        if math.isclose(norm, 0.0, abs_tol=1e-9):
            return arr
        return arr / norm

    vals = [float(x) for x in vec]
    norm = math.sqrt(sum(x * x for x in vals))
    if math.isclose(norm, 0.0, abs_tol=1e-9):
        return vals
    return [x / norm for x in vals]


class FaceDetector:
    """
    Wraps InsightFace (RetinaFace + ArcFace) to detect player faces, filter out
    tiny background crowd faces, and extract L2-normalized 512-D embeddings.
    """

    def __init__(self, config: SortConfig, model_name: str = "buffalo_l") -> None:
        self.config = config
        self.model_name = model_name
        self._app = None

    def _ensure_initialized(self) -> None:
        """Lazily initialize the InsightFace FaceAnalysis model on first use."""
        if self._app is not None:
            return

        try:
            import onnxruntime as ort
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError(
                "Missing face recognition dependencies. Please install 'insightface' "
                "and 'onnxruntime' (see requirements.txt)."
            ) from exc

        available_providers = ort.get_available_providers()
        if self.config.use_gpu:
            preferred = [
                p
                for p in (
                    "CUDAExecutionProvider",
                    "DmlExecutionProvider",
                    "CPUExecutionProvider",
                )
                if p in available_providers
            ]
            providers = preferred or ["CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]

        has_gpu_provider = any(
            p in {"CUDAExecutionProvider", "DmlExecutionProvider"}
            for p in providers
        )
        ctx_id = 0 if has_gpu_provider else -1

        bundled_root = APP_RESOURCE_DIR / ".models_cache" / "insightface"
        writable_root = MODELS_CACHE_DIR / "insightface"
        app_kwargs: dict[str, object] = {}
        try:
            if (bundled_root / "models" / self.model_name).is_dir():
                app_kwargs["root"] = str(bundled_root)
            elif (writable_root / "models" / self.model_name).is_dir():
                app_kwargs["root"] = str(writable_root)
            else:
                # Prefer user's ~/.insightface if already populated and readable;
                # otherwise store downloads under MODELS_CACHE_DIR so restricted folders work.
                default_home_model = Path.home() / ".insightface" / "models" / self.model_name
                try:
                    has_default = default_home_model.is_dir()
                except OSError:
                    has_default = False
                if not has_default:
                    writable_root.mkdir(parents=True, exist_ok=True)
                    app_kwargs["root"] = str(writable_root)
        except OSError:
            writable_root.mkdir(parents=True, exist_ok=True)
            app_kwargs["root"] = str(writable_root)

        logger.info(
            "Initializing InsightFace (%s) with providers: %s",
            self.model_name,
            providers,
        )
        try:
            self._app = FaceAnalysis(
                name=self.model_name, providers=providers, **app_kwargs
            )
            self._app.prepare(
                ctx_id=ctx_id,
                det_thresh=self.config.det_thresh,
                det_size=(640, 640),
            )
        except Exception as exc:
            if not has_gpu_provider:
                raise
            logger.warning(
                "GPU initialization for InsightFace failed (%s); falling back to CPU.",
                exc,
            )
            self._app = FaceAnalysis(
                name=self.model_name,
                providers=["CPUExecutionProvider"],
                **app_kwargs,
            )
            self._app.prepare(
                ctx_id=-1,
                det_thresh=self.config.det_thresh,
                det_size=(640, 640),
            )

    def detect_in_image(
        self,
        image_path: Path,
        image_bgr: Optional[np.ndarray] = None,
    ) -> list[PlayerDetection]:
        """
        Detect foreground faces in a single image and return PlayerDetection objects.
        Tiny spectator faces smaller than `config.min_face_size` are filtered out.
        """
        self._ensure_initialized()

        if image_bgr is None:
            image_bgr = load_image_bgr(image_path)
        if image_bgr is None or image_bgr.size == 0:
            return []

        img_h, img_w = image_bgr.shape[:2]
        raw_faces = self._app.get(image_bgr)

        detections: list[PlayerDetection] = []
        for face in raw_faces:
            det_score = float(getattr(face, "det_score", 1.0))
            if det_score < self.config.det_thresh:
                continue

            box = face.bbox.astype(int)
            x1 = max(0, int(box[0]))
            y1 = max(0, int(box[1]))
            x2 = min(img_w, int(box[2]))
            y2 = min(img_h, int(box[3]))

            bbox = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=det_score)

            # Sports crowd filter: skip tiny faces in the stands/background
            if (
                bbox.width < self.config.min_face_size
                or bbox.height < self.config.min_face_size
            ):
                continue

            raw_emb = getattr(face, "normed_embedding", None)
            if raw_emb is None:
                raw_emb = getattr(face, "embedding", None)
            if raw_emb is None:
                continue

            embedding = normalize_embedding(raw_emb)
            detections.append(
                PlayerDetection(
                    image_path=image_path,
                    face_bbox=bbox,
                    face_embedding=embedding,
                )
            )

        # Sort largest/highest-quality faces first
        detections.sort(key=lambda d: d.face_quality_score, reverse=True)
        return detections

    def detect_batch(
        self,
        image_paths: list[Path],
        show_progress: bool = True,
    ) -> tuple[list[PlayerDetection], list[Path]]:
        """
        Run face detection across a batch of photos.

        Returns:
            all_detections: All foreground player face detections across all images.
            no_face_photos: Image paths where 0 foreground faces were detected.
        """
        all_detections: list[PlayerDetection] = []
        no_face_photos: list[Path] = []

        iterator = (
            tqdm(image_paths, desc="Detecting faces", unit="photo")
            if (show_progress and tqdm is not None)
            else image_paths
        )

        for img_path in iterator:
            img_detections = self.detect_in_image(img_path)
            if img_detections:
                all_detections.extend(img_detections)
            else:
                no_face_photos.append(img_path)

        return all_detections, no_face_photos
