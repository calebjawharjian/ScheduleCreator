import type {
  GenerateResponse, Metadata, RankRequest, RankResponse,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

/**
 * fetch() rejects when the browser got no response it may read: the API is
 * down or asleep, the origin isn't allowed, or the server crashed outright (an
 * unhandled 500 carries no CORS header, so it is hidden too). The browser's
 * own message for all of these is a bare "Failed to fetch"; name the API and
 * the usual causes instead. Aborts are rethrown untouched: callers cancel
 * stale /rank requests on purpose and must still recognise them.
 */
async function reach(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new Error(
      `No usable response from the scheduler API at ${BASE}. It may be ` +
      `stopped or still waking up (a free Render instance can take about a ` +
      `minute), it may not allow this site's origin, or it may have crashed ` +
      `on this request (check the API's logs).`);
  }
}

/** Surfaces FastAPI's error detail instead of a bare status code. */
async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await reach(`${BASE}${path}`, {
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
  const response = await reach(`${BASE}/factors`);
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
