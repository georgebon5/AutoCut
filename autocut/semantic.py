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
    """Return up to ``n`` evenly-spaced frames from a proxy as PIL images."""
    from PIL import Image
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return []
        step = max(1, total // n)
        frames: list[Any] = []
        for fi in range(0, total, step):
            if len(frames) >= n:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ret, bgr = cap.read()
            if not ret:
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(rgb))
        return frames
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
) -> list[str]:
    """Assign each sentence to its best-matching clip id.

    Pure greedy per-sentence argmax — the same clip can be chosen for
    multiple sentences (which is desirable: the "best" shot may fit several
    adjacent sentences). Returns a list of clip_ids of the same length as
    ``sentences``. When ``clip_embeds`` is empty, returns an empty list.
    """
    if not clip_embeds or not sentences:
        return []
    clip_ids = list(clip_embeds.keys())
    clip_mat = np.stack([clip_embeds[cid] for cid in clip_ids], axis=0)
    text_mat = text_embeddings(sentences)
    sim = text_mat @ clip_mat.T   # (N_sentences, N_clips)
    picks = sim.argmax(axis=1)
    return [clip_ids[int(i)] for i in picks]
