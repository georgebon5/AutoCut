# ---------- builder ----------
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build toolchain only in this stage; librosa/opencv/parselmouth
# ship wheels but may fall back to source on edge platforms.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY autocut/ ./autocut/
COPY autocut_api/ ./autocut_api/
COPY config/ ./config/

# CPU-only torch keeps the image a few GB lighter than the CUDA default.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
    "torch>=2.0" "torchaudio>=2.0" && \
    pip install -e .


# ---------- runtime ----------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTOCUT_API_DATA_DIR=/data \
    HF_HOME=/data/hf-cache \
    TORCH_HOME=/data/torch-cache

# ffmpeg + runtime shared libs that opencv/soundfile/librosa load at import.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root for safety; also so the model-cache volume stays owned by this uid.
RUN useradd --create-home --uid 1000 autocut
WORKDIR /app

# Copy the installed site-packages + sources from the builder.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

RUN mkdir -p /data && chown -R autocut:autocut /app /data
USER autocut

EXPOSE 8000

# Kubernetes/compose honour this; the frontend's depends_on gate waits on it.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "autocut_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
