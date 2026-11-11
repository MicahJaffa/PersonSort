"""Shared data structures and geometry helpers for PersonSort."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
import math

try:
    import numpy as np
except ImportError:  # Allows inspecting/importing models before numpy is installed
    np = None  # type: ignore


def _dot_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two 1-D L2-normalized vectors."""
    if np is not None:
        return float(np.dot(vec_a, vec_b))
    return float(sum(float(a) * float(b) for a, b in zip(vec_a, vec_b)))


@dataclass
class BoundingBox:
    """Pixel bounding box [x1, y1, x2, y2] with detection confidence and spatial helpers."""

    x1: int
    y1: int
    y2: int
    x2: int = 0
    confidence: float = 1.0

    def __init__(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        confidence: float = 1.0,
    ) -> None:
        self.x1 = int(x1)
        self.y1 = int(y1)
        self.x2 = int(x2)
        self.y2 = int(y2)
        self.confidence = float(confidence)

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def diagonal(self) -> float:
        return math.hypot(float(self.width), float(self.height))

    def distance_to_box_center(self, other: BoundingBox) -> float:
        """Return Euclidean pixel distance between the centers of `self` and `other`."""
        cx1, cy1 = self.center
        cx2, cy2 = other.center
        return math.hypot(cx1 - cx2, cy1 - cy2)

    def proximity_score(self, inner: BoundingBox) -> float:
        """
        Compute a spatial proximity score in [0.0, 1.0] measuring how close and well-contained
        `inner` (e.g. a detected jersey number box) is relative to `self` (a player's torso/body box).
        Higher = closer to the center of the player's bounding box.
        """
        if self.area <= 0 or inner.area <= 0:
            return 0.0

        overlap_ratio = self.intersection_area(inner) / float(inner.area)
        if overlap_ratio < 0.25:
            return 0.0

        half_diag = max(1.0, self.diagonal * 0.5)
        dist = self.distance_to_box_center(inner)
        center_closeness = max(0.0, 1.0 - (dist / half_diag))
        return round(min(1.0, 0.65 * center_closeness + 0.35 * overlap_ratio), 4)

    def intersection_area(self, other: BoundingBox) -> int:
        """Compute pixel intersection area between two bounding boxes."""
        ix1 = max(self.x1, other.x1)
        iy1 = max(self.y1, other.y1)
        ix2 = min(self.x2, other.x2)
        iy2 = min(self.y2, other.y2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0
        return (ix2 - ix1) * (iy2 - iy1)

    def contains_box(self, inner: BoundingBox, min_overlap_ratio: float = 0.7) -> bool:
        """
        Return True if at least `min_overlap_ratio` of `inner` (e.g. a face box)
        lies inside `self` (e.g. a full-body box).
        """
        if inner.area == 0:
            return False
        overlap = self.intersection_area(inner) / float(inner.area)
        return overlap >= min_overlap_ratio

    def torso_from_body_box(self, image_width: int, image_height: int) -> BoundingBox:
        """
        Extract the chest/back jersey region directly inside a full-body or upper-body
        person bounding box `self` (always inside `[self.y1, self.y2]`).
        """
        tx1 = max(0, self.x1 + int(self.width * 0.08))
        tx2 = min(image_width, self.x2 - int(self.width * 0.08))
        ty1 = max(0, self.y1 + int(self.height * 0.14))
        ty2 = min(image_height, self.y1 + int(self.height * 0.68))
        if tx2 <= tx1:
            tx1, tx2 = max(0, self.x1), min(image_width, self.x2)
        if ty2 <= ty1:
            ty1, ty2 = max(0, self.y1), min(image_height, self.y2)
        return BoundingBox(x1=tx1, y1=ty1, x2=tx2, y2=ty2, confidence=self.confidence)

    def torso_from_face_box(self, image_width: int, image_height: int) -> BoundingBox:
        """
        Estimate the jersey/torso region by projecting downward from a face bounding box `self`.
        """
        cx, _ = self.center
        torso_half_w = int(self.width * 1.45)
        tx1 = max(0, int(cx - torso_half_w))
        tx2 = min(image_width, int(cx + torso_half_w))
        ty1 = min(image_height, self.y2 + int(self.height * 0.10))
        ty2 = min(image_height, self.y2 + int(self.height * 3.20))
        return BoundingBox(x1=tx1, y1=ty1, x2=tx2, y2=ty2, confidence=self.confidence)

    def estimate_torso_box(self, image_width: int, image_height: int) -> BoundingBox:
        """
        Estimate the jersey/torso region either from a full-body box or by projecting
        downward from a face bounding box.
        """
        if self.height >= self.width * 1.6:
            return self.torso_from_body_box(image_width, image_height)
        return self.torso_from_face_box(image_width, image_height)

    def to_dict(self) -> dict[str, float | int]:
        return {
            "x1": int(self.x1),
            "y1": int(self.y1),
            "x2": int(self.x2),
            "y2": int(self.y2),
            "confidence": round(float(self.confidence), 4),
        }


@dataclass
class PlayerDetection:
    """Represents a single detected player/face within one specific photo."""

    image_path: Path
    face_bbox: Optional[BoundingBox] = None
    face_embedding: Optional[Any] = None         # Normalized 512-D ArcFace vector
    body_bbox: Optional[BoundingBox] = None      # Full-body person box (Milestone 6)
    number_bbox: Optional[BoundingBox] = None    # Pixel bounding box of the detected jersey number
    jersey_number: Optional[str] = None          # Detected jersey number string, e.g. "23"
    jersey_confidence: float = 0.0
    bbox_proximity_score: float = 0.0            # 0.0-1.0 closeness of number_bbox to player's body/torso box
    assigned_player_id: Optional[str] = None     # Grouped ID, e.g. "Player_001"
    match_similarity: float = 0.0                # Cosine similarity to assigned folder/reference

    @property
    def has_face(self) -> bool:
        return self.face_bbox is not None and self.face_embedding is not None

    @property
    def has_jersey(self) -> bool:
        return bool(self.jersey_number and self.jersey_number.strip())

    @property
    def face_quality_score(self) -> float:
        """Score used to pick the sharpest/largest face crop for a player's reference preview."""
        if self.face_bbox is None:
            return 0.0
        return float(self.face_bbox.area) * float(self.face_bbox.confidence)


@dataclass
class PlayerProfile:
    """Represents a grouped player identity across multiple photos in the shoot."""

    player_id: str
    custom_name: Optional[str] = None
    is_known_reference: bool = False
    embeddings: list[Any] = field(default_factory=list)
    detections: list[PlayerDetection] = field(default_factory=list)
    photo_paths: set[Path] = field(default_factory=set)
    photo_similarities: dict[Path, float] = field(default_factory=dict)
    jersey_votes: dict[str, float] = field(default_factory=dict)
    best_detection: Optional[PlayerDetection] = None

    def add_detection(self, detection: PlayerDetection) -> None:
        """Associate a detection with this player profile and update embeddings, photos, and jersey votes."""
        detection.assigned_player_id = self.player_id
        self.detections.append(detection)
        self.photo_paths.add(detection.image_path)

        if detection.face_embedding is not None:
            self.embeddings.append(detection.face_embedding)

        if detection.has_jersey and detection.jersey_number is not None:
            num = detection.jersey_number.strip()
            weight = max(0.1, float(detection.jersey_confidence))
            self.jersey_votes[num] = self.jersey_votes.get(num, 0.0) + weight

        if detection.has_face:
            if (
                self.best_detection is None
                or detection.face_quality_score > self.best_detection.face_quality_score
            ):
                self.best_detection = detection

        if detection.match_similarity > 0.0:
            prev = self.photo_similarities.get(detection.image_path, 0.0)
            self.photo_similarities[detection.image_path] = max(prev, float(detection.match_similarity))

    def recompute_similarities(self) -> None:
        """
        Compute and store the cosine similarity (0.00 to 1.00) of every photo in this
        folder relative to the folder's centroid/reference face embedding.
        """
        centroid = self.centroid_embedding
        if centroid is None:
            for p in self.photo_paths:
                self.photo_similarities.setdefault(p, 0.0)
            return

        for det in self.detections:
            if det.face_embedding is not None:
                sim = max(0.0, min(1.0, _dot_similarity(det.face_embedding, centroid)))
                det.match_similarity = sim
                prev = self.photo_similarities.get(det.image_path, 0.0)
                self.photo_similarities[det.image_path] = max(prev, round(sim, 4))
            else:
                self.photo_similarities.setdefault(det.image_path, 0.0)

        for p in self.photo_paths:
            self.photo_similarities.setdefault(p, 0.0)

    def remove_photo(self, photo_path: Path) -> bool:
        """Remove `photo_path` from this player folder and recalculate its centroid & similarities."""
        if photo_path not in self.photo_paths:
            return False

        self.photo_paths.discard(photo_path)
        self.photo_similarities.pop(photo_path, None)
        self.detections = [d for d in self.detections if d.image_path != photo_path]
        self.embeddings = [
            d.face_embedding for d in self.detections if d.face_embedding is not None
        ]

        # Rebuild best_detection and jersey_votes from remaining detections
        self.best_detection = None
        self.jersey_votes.clear()
        for d in self.detections:
            if d.has_jersey and d.jersey_number:
                num = d.jersey_number.strip()
                self.jersey_votes[num] = self.jersey_votes.get(num, 0.0) + max(
                    0.1, float(d.jersey_confidence)
                )
            if d.has_face and (
                self.best_detection is None
                or d.face_quality_score > self.best_detection.face_quality_score
            ):
                self.best_detection = d

        self.recompute_similarities()
        return True

    @property
    def centroid_embedding(self) -> Optional[Any]:
        """Compute the normalized mean 512-D face embedding for this player."""
        if not self.embeddings:
            return None
        if np is not None:
            stacked = np.vstack(self.embeddings).astype(np.float32)
            mean_vec = np.mean(stacked, axis=0)
            norm = float(np.linalg.norm(mean_vec))
            if math.isclose(norm, 0.0, abs_tol=1e-9):
                return mean_vec
            return mean_vec / norm

        dim = len(self.embeddings[0])
        n = float(len(self.embeddings))
        mean_list = [
            sum(float(vec[i]) for vec in self.embeddings) / n for i in range(dim)
        ]
        norm = math.sqrt(sum(v * v for v in mean_list))
        if math.isclose(norm, 0.0, abs_tol=1e-9):
            return mean_list
        return [v / norm for v in mean_list]

    @property
    def average_similarity(self) -> float:
        """Return average similarity score across all face-matched photos in this folder."""
        scores = [s for s in self.photo_similarities.values() if s > 0.0]
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 4)

    @property
    def primary_jersey_number(self) -> Optional[str]:
        """Return the highest-voted jersey number associated with this player, if any."""
        if not self.jersey_votes:
            return None
        return max(self.jersey_votes.items(), key=lambda kv: kv[1])[0]

    @property
    def folder_name(self) -> str:
        """
        Return a filesystem-safe folder name for this player.
        Uses `custom_name` if the user renamed the folder or merged a jersey number into it
        (e.g. 'Player_001 #23'); otherwise returns `player_id`.
        """
        if self.custom_name and self.custom_name.strip():
            return self.custom_name.strip()
        return self.player_id

    @property
    def photo_count(self) -> int:
        return len(self.photo_paths)


@dataclass
class JerseyFolder:
    """
    Represents a separate virtual folder for a detected jersey number (e.g. '#23')
    kept at the bottom of the sidebar until the user chooses to add it into a PlayerProfile.
    """

    jersey_number: str
    detections: list[PlayerDetection] = field(default_factory=list)
    photo_paths: set[Path] = field(default_factory=set)
    photo_proximities: dict[Path, float] = field(default_factory=dict)

    @property
    def folder_id(self) -> str:
        return f"jersey_{self.jersey_number}"

    @property
    def folder_name(self) -> str:
        return f"#{self.jersey_number}"

    @property
    def photo_count(self) -> int:
        return len(self.photo_paths)

    @property
    def average_proximity(self) -> float:
        scores = [s for s in self.photo_proximities.values() if s > 0.0]
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 4)

    def add_detection(self, detection: PlayerDetection) -> None:
        """Add a jersey number detection and record its bounding-box proximity score."""
        self.detections.append(detection)
        self.photo_paths.add(detection.image_path)
        prox = float(detection.bbox_proximity_score or detection.jersey_confidence or 0.5)
        prev = self.photo_proximities.get(detection.image_path, 0.0)
        self.photo_proximities[detection.image_path] = max(prev, round(prox, 4))

    def remove_photo(self, photo_path: Path) -> bool:
        """Remove `photo_path` from this jersey number folder."""
        if photo_path not in self.photo_paths:
            return False
        self.photo_paths.discard(photo_path)
        self.photo_proximities.pop(photo_path, None)
        self.detections = [d for d in self.detections if d.image_path != photo_path]
        return True
