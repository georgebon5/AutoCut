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
  // Server-side extras (admin may apply an AUTOCUT_API_MODEL_PATH); the
  // flag is present only when the trained classifier scored this job.
  model_used?: boolean;
  model_path?: string;
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

export interface Clip {
  clip_id: string;
  original_path: string;
  proxy_path: string;
  audio_path: string | null;
  duration: number;
  fps: number;
  width: number;
  height: number;
  creation_time: string | null;
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  probability: number;
  clip_id: string;
}

export interface Transcript {
  clip_id: string;
  language: string;
  words: WordTimestamp[];
}

export interface RenderChunk {
  /** Index into `edl.segments`, or -1 for the opening hook. */
  segment_index: number;
  /** Seconds from the start of the rendered mp4. */
  output_start: number;
  output_end: number;
}

export interface Edl {
  clips: Clip[];
  segments: SegmentDict[];
  transcripts: Transcript[];
  hook: SegmentDict | null;
  render_chunks: RenderChunk[];
  created_at: string;
  version: string;
}

export interface SegmentUpdate {
  decision: Decision;
}

export interface WordUpdate {
  word: string;
}

export interface WordResponse {
  clip_id: string;
  word_index: number;
  word: string;
  start: number;
  end: number;
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

export interface OverrideRecord {
  id: number;
  job_id: string;
  preset: string;                 // "none" | "tight" | "medium" | "loose"
  segment_index: number;
  clip_id: string;
  segment_start: number;
  segment_end: number;
  previous_decision: Decision;
  new_decision: Decision;
  interest_score: number;
  features: Record<string, number>;
  created_at: string;             // ISO 8601
}

export interface OverridesListResponse {
  overrides: OverrideRecord[];
}

export interface CaptionStyle {
  font?: string;
  size?: number;
  text_color?: string;       // "#RRGGBB"
  outline_color?: string;    // "#RRGGBB"
  highlight_color?: string;  // "#RRGGBB" — karaoke per-word colour
  position?: "top" | "middle" | "bottom";
}
