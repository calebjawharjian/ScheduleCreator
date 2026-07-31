import type {
  GenerateResponse, Metadata, RankRequest, RankResponse,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

/** Surfaces FastAPI's error detail instead of a bare status code. */
async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        detail = typeof payload.detail === "string"
          ? payload.detail
          : JSON.stringify(payload.detail);
      }
    } catch {
      // Non-JSON error body; the status line is all we have.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function fetchMetadata(): Promise<Metadata> {
  const response = await fetch(`${BASE}/factors`);
  if (!response.ok) throw new Error("could not reach the scheduler API");
  return response.json();
}

export function generate(term: string, courses: string[]): Promise<GenerateResponse> {
  return post<GenerateResponse>("/generate", { term, courses });
}

/**
 * The interactive call. Takes an AbortSignal because slider drags fire faster
 * than responses return, and an out-of-order reply would show a ranking that
 * does not match the controls.
 */
export function rank(request: RankRequest, signal?: AbortSignal): Promise<RankResponse> {
  return post<RankResponse>("/rank", request, signal);
}
