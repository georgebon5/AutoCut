// Typed fetch wrappers for every FastAPI endpoint.
//
// All calls go through /api, which next.config.mjs rewrites to the FastAPI
// backend during dev. Switching to a different backend in production is a
// matter of changing AUTOCUT_API_URL — the frontend code stays identical.

import type {
  ChunkAck,
  CompleteUploadResponse,
  CreateUploadRequest,
  Edl,
  HealthResponse,
  Job,
  JobCreateRequest,
  OutputsListResponse,
  SegmentResponse,
  SegmentUpdate,
  Upload,
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

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

export const listOutputs = (jobId: string) =>
  request<OutputsListResponse>(`/jobs/${jobId}/outputs`);

/** URL for a download link / <video src>. The browser hits it directly. */
export const outputDownloadUrl = (jobId: string, filename: string) =>
  `${BASE}/jobs/${jobId}/outputs/${encodeURIComponent(filename)}`;

// ---------------------------------------------------------------------------
// Re-render (cached — no re-analysis)
// ---------------------------------------------------------------------------

export const postRender = (jobId: string, preset?: string) => {
  const q = preset ? `?preset=${encodeURIComponent(preset)}` : "";
  return request<Job>(`/jobs/${jobId}/render${q}`, { method: "POST" });
};
