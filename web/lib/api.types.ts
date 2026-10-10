// Mirrors autocut_api/schemas.py — keep in sync when the Python side changes.
// Field names, nullability and value types must match exactly so the
// typed fetch wrappers can trust the shapes they receive.

export type JobStatus = "pending" | "running" | "done" | "failed";
export type UploadStatus = "pending" | "uploading" | "complete" | "failed";
export type Decision = "keep" | "cut";

export interface HealthResponse {
  status: string;
  version: string;
}

export interface JobConfig {
  preset: "none" | "tight" | "medium" | "loose" | "all";
  hook: boolean;
  pacing: boolean;
  zoom: boolean;
}

export interface Job {
  id: string;
  status: JobStatus;
  stage: string;
  progress: number;     // 0..1
  error: string | null;
  workspace: string | null;
  config: JobConfig | null;
  created_at: string;   // ISO 8601
  updated_at: string;
}

export interface JobCreateRequest {
  upload_ids: string[];
  preset?: "none" | "tight" | "medium" | "loose" | "all";
  hook?: boolean;
  pacing?: boolean;
  zoom?: boolean;
}

export interface Upload {
  id: string;
  filename: string;
  total_size: number;
  chunk_size: number;
  total_chunks: number;
  status: UploadStatus;
  received: number[];
  final_path: string | null;
}

export interface CreateUploadRequest {
  filename: string;
  total_size: number;
}

export interface ChunkAck {
  id: string;
  chunk_index: number;
  received: number[];
  total_received: number;
  status: UploadStatus;
}

export interface CompleteUploadResponse {
  id: string;
  status: UploadStatus;
  final_path: string;
  total_size: number;
}

export interface SegmentDict {
  clip_id: string;
  start: number;
  end: number;
  features: Record<string, number>;
  interest_score: number;
  decision: Decision;
  decision_source: "auto" | "user";
  reasons: string[];
}

export interface Edl {
  clips: Record<string, unknown>[];
  segments: SegmentDict[];
  transcripts: Record<string, unknown>[];
  hook: SegmentDict | null;
  created_at: string;
  version: string;
}

export interface SegmentUpdate {
  decision: Decision;
}

export interface SegmentResponse {
  index: number;
  clip_id: string;
  start: number;
  end: number;
  duration: number;
  decision: Decision;
  decision_source: "auto" | "user";
  interest_score: number;
  reasons: string[];
  features: Record<string, number>;
}

export interface OutputFile {
  name: string;
  size: number;
  kind: "video" | "edl" | "captions_srt" | "captions_ass";
}

export interface OutputsListResponse {
  outputs: OutputFile[];
}
