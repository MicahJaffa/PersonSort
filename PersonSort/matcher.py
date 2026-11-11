"""Identity matching (against reference players) and unsupervised DBSCAN face clustering."""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

try:
    from sklearn.cluster import DBSCAN
except ImportError:
    DBSCAN = None  # type: ignore

from config import SortConfig
from face_detector import normalize_embedding
from models import JerseyFolder, PlayerDetection, PlayerProfile

logger = logging.getLogger(__name__)


def _cosine_sim_pair(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two 1-D L2-normalized vectors."""
    if np is not None:
        return float(np.dot(vec_a, vec_b))
    return float(sum(float(a) * float(b) for a, b in zip(vec_a, vec_b)))


def cosine_similarity_matrix(
    matrix_a: Sequence[Sequence[float]],
    matrix_b: Sequence[Sequence[float]],
) -> Any:
    """
    Compute pairwise cosine similarity between row-normalized matrices
    `matrix_a` (N, D) and `matrix_b` (M, D). Returns shape (N, M) in [-1.0, 1.0].
    """
    if np is not None:
        arr_a = np.asarray(matrix_a, dtype=np.float32)
        arr_b = np.asarray(matrix_b, dtype=np.float32)
        norm_a = np.linalg.norm(arr_a, axis=1, keepdims=True)
        norm_b = np.linalg.norm(arr_b, axis=1, keepdims=True)
        safe_a = np.divide(arr_a, np.maximum(norm_a, 1e-9))
        safe_b = np.divide(arr_b, np.maximum(norm_b, 1e-9))
        return np.dot(safe_a, safe_b.T)

    # Pure-Python fallback
    normed_a = [normalize_embedding(row) for row in matrix_a]
    normed_b = [normalize_embedding(row) for row in matrix_b]
    return [[_cosine_sim_pair(ra, rb) for rb in normed_b] for ra in normed_a]


def match_against_references(
    detections: list[PlayerDetection],
    reference_profiles: dict[str, PlayerProfile],
    sim_threshold: float,
) -> tuple[list[PlayerDetection], list[PlayerDetection]]:
    """
    Compare face detections against known reference player profiles.

    Returns:
        matched: Detections assigned to a known reference PlayerProfile.
        unmatched: Detections that did not reach `sim_threshold` for any reference.
    """
    valid_refs: list[tuple[str, Any]] = []
    for pid, profile in reference_profiles.items():
        centroid = profile.centroid_embedding
        if centroid is not None:
            valid_refs.append((pid, normalize_embedding(centroid)))

    if not valid_refs:
        return [], list(detections)

    ref_ids = [item[0] for item in valid_refs]
    ref_vectors = [item[1] for item in valid_refs]

    matched: list[PlayerDetection] = []
    unmatched: list[PlayerDetection] = []

    for det in detections:
        if not det.has_face or det.face_embedding is None:
            unmatched.append(det)
            continue

        emb = normalize_embedding(det.face_embedding)
        sims = [_cosine_sim_pair(emb, rvec) for rvec in ref_vectors]
        best_idx = max(range(len(sims)), key=lambda i: sims[i])
        best_sim = float(sims[best_idx])

        if best_sim >= sim_threshold:
            matched_id = ref_ids[best_idx]
            det.match_similarity = best_sim
            reference_profiles[matched_id].add_detection(det)
            matched.append(det)
        else:
            unmatched.append(det)

    return matched, unmatched


def _fallback_cosine_dbscan(
    embeddings: Sequence[Sequence[float]],
    eps: float,
    min_samples: int,
) -> list[int]:
    """
    DBSCAN implementation using cosine distance (`1 - cosine_similarity`)
    that works both with and without `scikit-learn` / `numpy`.
    """
    n = len(embeddings)
    sim_matrix = cosine_similarity_matrix(embeddings, embeddings)

    labels = [-1] * n
    visited = [False] * n
    cluster_id = 0

    def get_neighbors(idx: int) -> list[int]:
        row = sim_matrix[idx]
        return [
            j
            for j in range(n)
            if max(0.0, min(2.0, 1.0 - float(row[j]))) <= eps
        ]

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        neighbors = get_neighbors(i)
        if len(neighbors) < min_samples:
            continue

        labels[i] = cluster_id
        queue = list(neighbors)
        idx = 0
        while idx < len(queue):
            q = queue[idx]
            idx += 1
            if not visited[q]:
                visited[q] = True
                q_neighbors = get_neighbors(q)
                if len(q_neighbors) >= min_samples:
                    for nb in q_neighbors:
                        if nb not in queue:
                            queue.append(nb)
            if labels[q] == -1:
                labels[q] = cluster_id

        cluster_id += 1

    return labels


def cluster_unmatched_faces(
    detections: list[PlayerDetection],
    cluster_eps: float = 0.50,
    min_samples: int = 1,
    existing_ids: Optional[set[str]] = None,
) -> tuple[dict[str, PlayerProfile], list[PlayerDetection]]:
    """
    Group unknown face detections into new PlayerProfiles (`Player_001`, `Player_002`, ...)
    using DBSCAN with cosine distance.

    Returns:
        new_profiles: Dict mapping new player_id -> PlayerProfile.
        singletons: Detections labeled as noise (-1) when `min_samples > 1` or lacking faces.
    """
    face_detections = [d for d in detections if d.has_face and d.face_embedding is not None]
    non_face_detections = [d for d in detections if not d.has_face or d.face_embedding is None]

    if not face_detections:
        return {}, non_face_detections

    normed_list = [normalize_embedding(d.face_embedding) for d in face_detections]  # type: ignore[arg-type]

    if DBSCAN is not None and np is not None:
        emb_matrix = np.vstack(normed_list).astype(np.float32)
        clustering = DBSCAN(
            eps=cluster_eps,
            min_samples=min_samples,
            metric="cosine",
        ).fit(emb_matrix)
        labels = [int(x) for x in clustering.labels_]
    else:
        labels = _fallback_cosine_dbscan(normed_list, eps=cluster_eps, min_samples=min_samples)

    used_ids = set(existing_ids) if existing_ids else set()
    cluster_map: dict[int, PlayerProfile] = {}
    singletons: list[PlayerDetection] = list(non_face_detections)

    next_num = 1
    for det, lbl in zip(face_detections, labels):
        if lbl == -1:
            singletons.append(det)
            continue

        if lbl not in cluster_map:
            while True:
                candidate_id = f"Player_{next_num:03d}"
                next_num += 1
                if candidate_id not in used_ids:
                    used_ids.add(candidate_id)
                    break
            cluster_map[lbl] = PlayerProfile(
                player_id=candidate_id,
                is_known_reference=False,
            )

        cluster_map[lbl].add_detection(det)

    new_profiles = {prof.player_id: prof for prof in cluster_map.values()}
    return new_profiles, singletons


def group_players(
    detections: list[PlayerDetection],
    config: SortConfig,
    reference_profiles: Optional[dict[str, PlayerProfile]] = None,
) -> tuple[dict[str, PlayerProfile], list[PlayerDetection]]:
    """
    Full identity resolution pipeline:
    1. Match faces against `reference_profiles` (if provided).
    2. Cluster remaining unmatched faces via DBSCAN into `Player_001`, `Player_002`, ...
    3. Link any no-face detections that have a detected jersey number matching a profile's
       primary jersey number (ready for Milestone 6).

    Returns:
        all_profiles: Combined dict of active PlayerProfiles that have >= 1 photo.
        unassigned: Detections that could not be assigned to any player profile.
    """
    active_profiles: dict[str, PlayerProfile] = {}
    if reference_profiles:
        for pid, prof in reference_profiles.items():
            active_profiles[pid] = prof

    # Step 1: Match against known reference profiles (if any)
    if active_profiles:
        _, unmatched = match_against_references(
            detections=detections,
            reference_profiles=active_profiles,
            sim_threshold=config.sim_threshold,
        )
    else:
        unmatched = list(detections)

    # Step 2: Cluster remaining face detections into new Player_XXX profiles
    new_profiles, leftover = cluster_unmatched_faces(
        detections=unmatched,
        cluster_eps=config.cluster_eps,
        min_samples=config.min_samples,
        existing_ids=set(active_profiles.keys()),
    )
    active_profiles.update(new_profiles)

    # Note: Jersey number detections are kept in separate JerseyFolders until the user
    # chooses to add them into a PlayerProfile in the UI.
    populated_profiles = {
        pid: prof for pid, prof in active_profiles.items() if prof.photo_count > 0
    }
    for prof in populated_profiles.values():
        prof.recompute_similarities()

    return populated_profiles, leftover


def build_jersey_folders(
    detections: list[PlayerDetection],
) -> dict[str, JerseyFolder]:
    """
    Group all detections that have a recognized jersey number into separate `JerseyFolder`
    objects (keyed by jersey number string, e.g. '23'), sorted by bounding-box proximity.
    """
    jersey_map: dict[str, JerseyFolder] = {}
    for det in detections:
        if not det.has_jersey or det.jersey_number is None:
            continue
        num = det.jersey_number.strip()
        if not num:
            continue
        if num not in jersey_map:
            jersey_map[num] = JerseyFolder(jersey_number=num)
        jersey_map[num].add_detection(det)

    return jersey_map


def compute_pairwise_photo_similarities(
    target_photo: Path,
    all_detections: list[PlayerDetection],
    profiles: dict[str, PlayerProfile],
) -> dict[str, Any]:
    """
    Compute similarity scores between `target_photo` and:
    1. Every player folder's centroid embedding (`folder_similarities`)
    2. Every other photo in the batch (`photo_similarities`)
    """
    target_dets = [
        d for d in all_detections if d.image_path == target_photo and d.has_face and d.face_embedding is not None
    ]
    if not target_dets:
        return {
            "has_face": False,
            "folder_similarities": [],
            "photo_similarities": [],
        }

    # Use the highest-quality face in target_photo as the primary comparison anchor
    anchor_emb = normalize_embedding(target_dets[0].face_embedding)

    folder_sims: list[dict[str, Any]] = []
    for pid, prof in profiles.items():
        centroid = prof.centroid_embedding
        if centroid is not None:
            sim = max(0.0, min(1.0, _cosine_sim_pair(anchor_emb, normalize_embedding(centroid))))
            folder_sims.append(
                {
                    "player_id": pid,
                    "folder_name": prof.folder_name,
                    "similarity": round(sim, 4),
                    "similarity_pct": round(sim * 100.0, 1),
                }
            )
    folder_sims.sort(key=lambda x: x["similarity"], reverse=True)

    best_by_other_photo: dict[Path, float] = {}
    for det in all_detections:
        if det.face_embedding is None or not det.has_face:
            continue
        other_emb = normalize_embedding(det.face_embedding)
        sim = max(0.0, min(1.0, _cosine_sim_pair(anchor_emb, other_emb)))
        prev = best_by_other_photo.get(det.image_path, -1.0)
        if sim > prev:
            best_by_other_photo[det.image_path] = round(sim, 4)

    photo_sims = [
        {
            "photo_name": p.name,
            "photo_path": str(p),
            "similarity": s,
            "similarity_pct": round(s * 100.0, 1),
        }
        for p, s in sorted(best_by_other_photo.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "has_face": True,
        "folder_similarities": folder_sims,
        "photo_similarities": photo_sims,
    }
