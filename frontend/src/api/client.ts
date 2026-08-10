/**
 * Thin fetch wrapper: attaches the bearer token, parses JSON, and throws a
 * typed `ApiError` (carrying the HTTP status) on non-2xx responses so that
 * callers/pages can branch on `error.status` (401/403/404/5xx) per the
 * shared ErrorState conventions.
 */
const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export interface ApiFetchOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE" | "PUT";
  body?: unknown;
  token?: string | null;
  signal?: AbortSignal;
  /** Extra headers, e.g. X-Agent-Key -- not used by the human-facing UI today. */
  headers?: Record<string, string>;
}

/**
 * Build a query string from a params object, skipping undefined/null values.
 * Booleans and numbers are stringified; arrays are not supported (no list
 * endpoints in this API take array query params).
 */
export function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { method = "GET", body, token, signal, headers = {} } = options;

  const requestHeaders: Record<string, string> = {
    Accept: "application/json",
    ...headers,
  };
  if (body !== undefined) {
    requestHeaders["Content-Type"] = "application/json";
  }
  if (token) {
    requestHeaders["Authorization"] = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: requestHeaders,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch {
    throw new ApiError(0, "Network error -- unable to reach the server");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const payload: unknown = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    const detail = extractDetail(payload) ?? response.statusText ?? "Request failed";
    throw new ApiError(response.status, detail);
  }

  return payload as T;
}

function extractDetail(payload: unknown): string | null {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail: unknown = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // FastAPI 422 validation errors: detail is a list of {loc, msg, type}.
    if (Array.isArray(detail)) {
      return (detail as unknown[])
        .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : JSON.stringify(item)))
        .join("; ");
    }
  }
  return null;
}
