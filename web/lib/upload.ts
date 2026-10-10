// Chunked uploader driving the resumable upload API in autocut_api.
//
// Chunks are PUT sequentially so progress is monotonic and errors surface
// immediately — parallel chunks inside a single file would require much more
// bookkeeping for very little speed-up on phone/vlog file sizes.

import { completeUpload, createUpload, putChunk } from "./api";

export interface UploadFileOptions {
  /** Fired after each successful chunk with cumulative bytes uploaded. */
  onProgress?: (bytesUploaded: number, totalBytes: number) => void;
  /** Abort any in-flight chunk and stop. */
  signal?: AbortSignal;
}

export class UploadCancelledError extends Error {
  constructor() {
    super("upload cancelled");
    this.name = "UploadCancelledError";
  }
}

/**
 * Upload a single File through the chunked endpoint and finalise it.
 * Returns the upload_id on success.
 */
export async function uploadFileChunked(
  file: File,
  options: UploadFileOptions = {},
): Promise<string> {
  if (options.signal?.aborted) throw new UploadCancelledError();

  const session = await createUpload({
    filename: file.name,
    total_size: file.size,
  });

  const { id, chunk_size: chunkSize, total_chunks: totalChunks } = session;

  let uploaded = 0;
  options.onProgress?.(uploaded, file.size);

  for (let i = 0; i < totalChunks; i += 1) {
    if (options.signal?.aborted) throw new UploadCancelledError();

    const start = i * chunkSize;
    const end = Math.min(start + chunkSize, file.size);
    const slice = file.slice(start, end);
    const buf = await slice.arrayBuffer();

    await putChunk(id, i, buf);

    uploaded = end;
    options.onProgress?.(uploaded, file.size);
  }

  await completeUpload(id);
  return id;
}
