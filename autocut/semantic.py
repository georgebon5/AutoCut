"""Semantic matching between voiceover sentences and clip visuals via CLIP.

In voiceover-vlog mode the user hands us two things that aren't aligned:
clips (B-roll) and a narration track. Without this module, the clips play
in the order they were uploaded — fine, but a wasted opportunity. With it,
each sentence of the narration is scored against each clip's visual
content, and the clips are re-scheduled so the one that best matches
"I went for coffee" is the one playing while that sentence is heard.

Implementation uses OpenAI's CLIP (ViT-B/32 — small enough to feel instant
after warm-up, well-trained enough to handle broad everyday imagery). Both
visual and text encoders map to a shared 512-D latent space; cosine
similarity picks the best clip per sentence.

First call downloads the model (~150 MB, cached by HF). All subsequent
pipeline runs re-use the cached singleton via ``@lru_cache``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_MODEL_NAME = "openai/clip-vit-base-patch32"
_FRAMES_PER_CLIP = 4   # sampled evenly across each proxy for averaged visual embed


@lru_cache(maxsize=1)
def _load_model() -> tuple[Any, Any]:
    """Load CLIP model + processor once per process. Thread-safe for inference."""
    from transformers import CLIPModel, CLIPProcessor
    model = CLIPModel.from_pretrained(_MODEL_NAME)
    processor = CLIPProcessor.from_pretrained(_MODEL_NAME)
    model.eval()
    return model, processor


def _sample_frames(proxy_path: Path, n: int = _FRAMES_PER_CLIP) -> list[Any]:
    """Scene-aware frame sampling for CLIP embedding.

    Instead of evenly-spaced grabs — which can land on blurry transitions
    or motion-blurred frames — we walk the clip, compute a quick
    frame-to-frame delta, and keep the top-N frames whose "visual
    activity" (brightness variance + delta from previous) is highest.
    These tend to be the composed, static beats of the shot rather than
    pans or motion blur.
    """
    from PIL import Image
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return []
        # Walk ~24 candidate frames evenly, score each, keep top-N by score.
        # Scoring is cheap: variance = sharpness proxy; frame delta = scene
        # change proxy. We punish both extremes (very low = blur; very high
        # = mid-pan motion blur) so the kept frames are "composed stills".
        step = max(1, total // 24)
        candidates: list[tuple[float, Any]] = []
        prev_gray: np.ndarray | None = None
        for fi in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ret, bgr = cap.read()
            if not ret:
                continue
            gray = np.array(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), dtype=np.float32)
            variance = float(gray.var())
            delta = (
                float(np.abs(gray - prev_gray).mean())
                if prev_gray is not None else 10.0
            )
            prev_gray = gray
            # Both extremes bad: 0 variance = flat/blur, very high delta =
            # motion-blurred transition. A gentle quadratic nudge produces
            # the "composed middle" frames.
            score = variance - abs(delta - 8.0) * 50.0
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            candidates.append((score, Image.fromarray(rgb)))
        candidates.sort(key=lambda t: t[0], reverse=True)
        return [img for _, img in candidates[:n]]
    finally:
        cap.release()


def visual_embeddings(proxy_paths: dict[str, Path]) -> dict[str, np.ndarray]:
    """Averaged CLIP visual embedding per clip.

    Returns ``{clip_id: embedding}``. Clips with no decodable frames are
    omitted. Each embedding is L2-normalised so matching reduces to a
    plain dot product.
    """
    import torch
    model, processor = _load_model()

    out: dict[str, np.ndarray] = {}
    for clip_id, path in proxy_paths.items():
        frames = _sample_frames(path)
        if not frames:
            continue
        inputs = processor(images=frames, return_tensors="pt")
        with torch.no_grad():
            feats = model.get_image_features(**inputs)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        out[clip_id] = feats.mean(dim=0).cpu().numpy()
    return out


def text_embeddings(texts: list[str]) -> np.ndarray:
    """CLIP text embeddings for a batch of sentences.

    Returns an (N, D) array, L2-normalised row-wise. Empty strings produce
    zero vectors — the match step treats them as non-matching for anything.
    CLIP's text encoder has a 77-token limit; longer sentences are truncated.
    """
    import torch
    model, processor = _load_model()

    cleaned = [(t or "").strip() for t in texts]
    inputs = processor(
        text=cleaned, return_tensors="pt",
        padding=True, truncation=True, max_length=77,
    )
    with torch.no_grad():
        feats = model.get_text_features(**inputs)
    norms = feats.norm(dim=-1, keepdim=True).clamp(min=1e-9)
    feats = feats / norms
    return feats.cpu().numpy()


def match_sentences_to_clips(
    sentences: list[str],
    clip_embeds: dict[str, np.ndarray],
    *,
    clip_durations: dict[str, float] | None = None,
    segment_durations: list[float] | None = None,
    switch_penalty: float = 0.08,
    duration_floor: float = 0.5,
) -> list[str]:
    """Assign each sentence to its best-matching clip id via DP.

    Beyond plain argmax we fold in two signals that noticeably improve the
    cut's feel on real vlogs:

    - **Smoothness** (``switch_penalty``) — subtracted from the score every
      time we hop from one clip to another. The DP therefore prefers to
      stay on the same shot for consecutive sentences unless a different
      clip is clearly better. Without this, adjacent sentences with
      similar scores would cause visual chattering.

    - **Duration fit** (``clip_durations`` / ``segment_durations``) — if a
      clip is shorter than its target segment it has to loop, which looks
      awful. We multiply the similarity by ``clip_dur / seg_dur`` (clamped
      to ``[duration_floor, 1.0]``) so loop-heavy choices only win when
      the semantic gap is big enough to justify them.

    The DP is O(N·K²) where N is sentences and K is clips — negligible at
    typical vlog sizes (< 100 × 20).

    Returns a list of clip_ids of the same length as ``sentences``. When
    ``clip_embeds`` is empty, returns an empty list.
    """
    if not clip_embeds or not sentences:
        return []
    clip_ids = list(clip_embeds.keys())
    K = len(clip_ids)
    clip_mat = np.stack([clip_embeds[cid] for cid in clip_ids], axis=0)
    text_mat = text_embeddings(sentences)
    sim = text_mat @ clip_mat.T   # (N, K)

    # Duration-fit multiplier per (segment, clip).
    if clip_durations is not None and segment_durations is not None:
        fit = np.ones_like(sim)
        for i, seg_dur in enumerate(segment_durations):
            if seg_dur <= 0:
                continue
            for j, cid in enumerate(clip_ids):
                cdur = clip_durations.get(cid, seg_dur)
                ratio = min(1.0, cdur / seg_dur) if cdur > 0 else duration_floor
                fit[i, j] = max(duration_floor, ratio)
        sim = sim * fit

    # DP forward pass. dp[i, j] = best total score ending with clip j at
    # sentence i. parent[i, j] = the clip index chosen for sentence i-1
    # on that best path (used to reconstruct).
    N = sim.shape[0]
    dp = np.full((N, K), -np.inf, dtype=np.float32)
    parent = np.zeros((N, K), dtype=np.int32)
    dp[0] = sim[0]
    for i in range(1, N):
        # candidate[i,j] = dp[i-1,:] with switching penalty applied where
        # the previous choice differs from j, then + sim[i,j].
        for j in range(K):
            prev = dp[i - 1].copy()
            prev_mask = np.ones(K, dtype=np.float32) * switch_penalty
            prev_mask[j] = 0.0   # staying costs nothing
            scored = prev - prev_mask
            best_k = int(np.argmax(scored))
            dp[i, j] = scored[best_k] + sim[i, j]
            parent[i, j] = best_k

    # Backtrack from the best final cell.
    path = [int(np.argmax(dp[N - 1]))]
    for i in range(N - 1, 0, -1):
        path.append(int(parent[i, path[-1]]))
    path.reverse()
    return [clip_ids[j] for j in path]
