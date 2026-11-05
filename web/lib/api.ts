// Typed fetch wrappers for every FastAPI endpoint.
//
// All calls go through /api, which next.config.mjs rewrites to the FastAPI
// backend during dev. Switching to a different backend in production is a
// matter of changing AUTOCUT_API_URL — the frontend code stays identical.

import type {
  CaptionStyle,
  ChunkAck,
  CompleteUploadResponse,
  CreateUploadRequest,
  Edl,
  HealthResponse,
  Job,
  JobCreateRequest,
  MergeResponse,
  OutputsListResponse,
  OverridesListResponse,
  SegmentResponse,
  SegmentUpdate,
  SplitResponse,
  TrainResult,
  TrainStatus,
  Upload,
  WaveformData,
  WordResponse,
} from "./api.types";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly url: string,
  ) {
    super(`${status} ${detail} (${url})`);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { expectJson?: boolean },
): Promise<T> {
  const url = `${BASE}${path}`;
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError(res.status, String(detail), url);
  }
  if (init?.expectJson === false) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export const getHealth = () => request<HealthResponse>("/health");

// ---------------------------------------------------------------------------
// Uploads (chunked / resumable)
// ---------------------------------------------------------------------------

export const createUpload = (body: CreateUploadRequest) =>
  request<Upload>("/uploads", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const getUpload = (id: string) =>
  request<Upload>(`/uploads/${id}`);

export const putChunk = (id: string, index: number, bytes: ArrayBuffer | Uint8Array) =>
  request<ChunkAck>(`/uploads/${id}/chunks/${index}`, {
    method: "PUT",
    body: bytes as BodyInit,
  });

export const completeUpload = (id: string) =>
  request<CompleteUploadResponse>(`/uploads/${id}/complete`, { method: "POST" });

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export const listJobs = () => request<Job[]>("/jobs");

export const createJob = (body: JobCreateRequest) =>
  request<Job>("/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

export const getJob = (id: string) => request<Job>(`/jobs/${id}`);

export const cloneJob = (
  id: string,
  overrides?: { preset?: string; label?: string },
) =>
  request<Job>(`/jobs/${id}/clone`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(overrides ?? {}),
  });

export const deleteJob = (id: string) =>
  request<void>(`/jobs/${id}`, { method: "DELETE", expectJson: false });

export const patchJobLabel = (id: string, label: string | null) =>
  request<Job>(`/jobs/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ label }),
  });

/** URL for the SSE stream of a job's progress events. */
export const jobEventsUrl = (id: string) => `${BASE}/jobs/${id}/events`;

// ---------------------------------------------------------------------------
// EDL review + segment override
// ---------------------------------------------------------------------------

export const getEdl = (jobId: string, preset?: string) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<Edl>(`/jobs/${jobId}/edl${q}`);
};

export const patchSegment = (
  jobId: string,
  index: number,
  update: SegmentUpdate,
  preset?: string,
) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<SegmentResponse>(`/jobs/${jobId}/segments/${index}${q}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(update),
  });
};

export const batchPatchSegments = (
  jobId: string,
  updates: Array<{ index: number; decision: import("./api.types").Decision }>,
  preset?: string,
) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<import("./api.types").SegmentResponse[]>(
    `/jobs/${jobId}/segments${q}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ updates }),
    },
  );
};

export const splitSegment = (
  jobId: string,
  index: number,
  at: number,
  preset?: string,
) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<SplitResponse>(`/jobs/${jobId}/segments/${index}/split${q}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ at }),
  });
};

export const mergeSegments = (
  jobId: string,
  index: number,
  preset?: string,
) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<MergeResponse>(`/jobs/${jobId}/segments/${index}/merge${q}`, {
    method: "POST",
  });
};

export const patchWord = (
  jobId: string,
  clipId: string,
  wordIndex: number,
  word: string,
  preset?: string,
) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<WordResponse>(
    `/jobs/${jobId}/transcripts/${encodeURIComponent(clipId)}/words/${wordIndex}${q}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ word }),
    },
  );
};

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

export const listOutputs = (jobId: string) =>
  request<OutputsListResponse>(`/jobs/${jobId}/outputs`);

/** URL for a download link / <video src>. The browser hits it directly. */
export const outputDownloadUrl = (jobId: string, filename: string) =>
  `${BASE}/jobs/${jobId}/outputs/${encodeURIComponent(filename)}`;

/** URL for the ZIP bundle containing all user-facing outputs. */
export const bundleDownloadUrl = (jobId: string) =>
  `${BASE}/jobs/${jobId}/outputs/bundle.zip`;

/** Fetch pre-computed waveform peaks for one clip. */
export const getWaveform = (jobId: string, clipId: string) =>
  request<WaveformData>(
    `/jobs/${jobId}/outputs/${encodeURIComponent(`waveform_${clipId}.json`)}`,
  );

/** Fetch a text output file as a plain string (used for chapters preview). */
export const fetchOutputText = async (jobId: string, filename: string): Promise<string> => {
  const url = `${BASE}/jobs/${jobId}/outputs/${encodeURIComponent(filename)}`;
  const res = await fetch(url);
  if (!res.ok) throw new ApiError(res.status, res.statusText, url);
  return res.text();
};

/** Fetch auto-generated chapters as structured JSON. Returns [] if no chapters file exists. */
export const getChapters = (jobId: string, preset?: string) => {
  const filename = preset ? `chapters_${preset}.json` : "chapters.json";
  return request<{ start: number; end: number; title: string }[]>(
    `/jobs/${jobId}/outputs/${encodeURIComponent(filename)}`,
  );
};

// ---------------------------------------------------------------------------
// Re-render (cached — no re-analysis)
// ---------------------------------------------------------------------------

export const postRender = (
  jobId: string,
  preset?: string,
  opts?: {
    burnSubtitles?: boolean;
    captionStyle?: CaptionStyle;
    transitionType?: string;
    colorGrading?: string;
  },
) => {
  const params = new URLSearchParams();
  if (preset) params.set("preset", preset);
  if (opts?.burnSubtitles) params.set("burn_subtitles", "true");
  const q = params.toString() ? `?${params.toString()}` : "";
  const body: Record<string, unknown> = {};
  if (opts?.captionStyle && Object.keys(opts.captionStyle).length > 0) {
    body.caption_style = opts.captionStyle;
  }
  if (opts?.transitionType) body.transition_type = opts.transitionType;
  if (opts?.colorGrading) body.color_grading = opts.colorGrading;
  const hasBody = Object.keys(body).length > 0;
  return request<Job>(`/jobs/${jobId}/render${q}`, {
    method: "POST",
    headers: hasBody ? { "content-type": "application/json" } : undefined,
    body: hasBody ? JSON.stringify(body) : undefined,
  });
};

// ---------------------------------------------------------------------------
// NLE export (FCPXML)
// ---------------------------------------------------------------------------

/** URL for an FCPXML download. Server sets Content-Disposition so the browser
 *  saves it with a sensible filename. */
export const exportFcpxmlUrl = (jobId: string, preset?: string) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return `${BASE}/jobs/${jobId}/export/fcpxml${q}`;
};

// ---------------------------------------------------------------------------
// Override log (training data readback)
// ---------------------------------------------------------------------------

export const listOverrides = (jobId: string) =>
  request<OverridesListResponse>(`/jobs/${jobId}/overrides`);

// ---------------------------------------------------------------------------
// Adaptive learning — training status + manual trigger
// ---------------------------------------------------------------------------

export const getTrainStatus = () => request<TrainStatus>("/train/status");

export const triggerTrain = () =>
  request<TrainResult>("/train", { method: "POST" });
