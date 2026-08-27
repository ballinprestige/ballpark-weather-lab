import type { ArchiveEntry, ArchiveIndex, BallparkPayload, GeometryArtifact, PublicationBundle, ReleasePointer } from './types';
import { validateArchiveIndex, validateGeometry, validatePayload, validateRelease } from './validate';

const REQUEST_TIMEOUT_MS = 8_000;
const REQUEST_ATTEMPTS = 2;

export class PublicationLoadError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'PublicationLoadError';
  }
}

async function fetchText(path: string): Promise<string> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= REQUEST_ATTEMPTS; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(path, { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.text();
    } catch (error) {
      lastError = error;
      if (attempt < REQUEST_ATTEMPTS) await new Promise((resolve) => window.setTimeout(resolve, 180 * attempt));
    } finally {
      window.clearTimeout(timeout);
    }
  }
  const detail = lastError instanceof DOMException && lastError.name === 'AbortError'
    ? `timed out after ${REQUEST_TIMEOUT_MS / 1000} seconds`
    : lastError instanceof Error ? lastError.message : 'unknown network failure';
  throw new PublicationLoadError(`Could not load ${path}: ${detail}.`, { cause: lastError });
}

function parseJson(text: string, label: string): unknown {
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new PublicationLoadError(`${label} is not valid JSON.`, { cause: error });
  }
}

export async function sha256Text(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function loadRelease(): Promise<ReleasePointer> {
  return validateRelease(parseJson(await fetchText('./data/release.json'), 'Release pointer'));
}

async function loadPayloadWithHash(path: string): Promise<{ payload: BallparkPayload; hash: string }> {
  const text = await fetchText(path);
  return { payload: validatePayload(parseJson(text, 'Payload')), hash: await sha256Text(text) };
}

async function loadOptionalArchive(warnings: string[]): Promise<ArchiveIndex> {
  try {
    return validateArchiveIndex(parseJson(await fetchText('./archive/index.json'), 'Archive index'));
  } catch (error) {
    warnings.push(error instanceof Error ? `History unavailable: ${error.message}` : 'History unavailable.');
    return { dates: [] };
  }
}

async function loadOptionalGeometry(warnings: string[]): Promise<GeometryArtifact | null> {
  try {
    return validateGeometry(parseJson(await fetchText('./park_geometry.json'), 'Park geometry'));
  } catch (error) {
    warnings.push(error instanceof Error ? `Park geometry unavailable: ${error.message}` : 'Park geometry unavailable.');
    return null;
  }
}

export async function loadCurrentPublication(): Promise<PublicationBundle> {
  const warnings: string[] = [];
  const [release, payloadResult, archive, geometry] = await Promise.all([
    loadRelease(),
    loadPayloadWithHash('./data/data.json'),
    loadOptionalArchive(warnings),
    loadOptionalGeometry(warnings)
  ]);
  if (payloadResult.hash !== release.payload_sha256) {
    throw new PublicationLoadError(`Publication hash mismatch. Expected ${release.payload_sha256.slice(0, 12)}…, received ${payloadResult.hash.slice(0, 12)}….`);
  }
  if (payloadResult.payload.date !== release.date) {
    throw new PublicationLoadError(`Publication date mismatch. Release points to ${release.date}, payload contains ${payloadResult.payload.date}.`);
  }
  if (payloadResult.payload.generated_at !== release.generated_at) {
    throw new PublicationLoadError('Publication timestamp mismatch between release pointer and payload.');
  }
  return {
    payload: payloadResult.payload,
    release,
    archive,
    geometry,
    warnings,
    payloadHash: payloadResult.hash
  };
}

export async function loadArchivePublication(entry: ArchiveEntry): Promise<{ payload: BallparkPayload; payloadHash: string }> {
  const result = await loadPayloadWithHash(`./archive/${encodeURIComponent(entry.date)}.json`);
  if (result.hash !== entry.payload_sha256) {
    throw new PublicationLoadError(`Archive hash mismatch for ${entry.date}.`);
  }
  if (result.payload.date !== entry.date) {
    throw new PublicationLoadError(`Archive date mismatch for ${entry.date}.`);
  }
  return { payload: result.payload, payloadHash: result.hash };
}
