/*
 * Typed fetch wrapper for the HERMES API.
 *
 * Every backend call goes through `apiFetch`, which:
 *   - prefixes `/api` so callers pass relative paths
 *   - serialises bodies as JSON
 *   - raises `ApiError` with a parsed `detail` string on non-2xx
 *
 * The Vite dev server proxies `/api` to the FastAPI port (see
 * `vite.config.ts`); production deployments do the equivalent via
 * nginx. So this code never sees an absolute URL — saved a class of
 * "works in dev, fails in prod" CORS bugs on day one.
 */

// localStorage key used by every page that needs to read or set the
// access token. Centralised so a future move to a cookie or in-memory
// store only has to change `getStoredToken` / `setStoredToken`.
const TOKEN_STORAGE_KEY = 'hermes.access_token';

export function getStoredToken(): string | null {
	if (typeof window === 'undefined') return null;
	try {
		return window.localStorage.getItem(TOKEN_STORAGE_KEY);
	} catch {
		return null;
	}
}

export function setStoredToken(token: string): void {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
	} catch {
		/* private mode etc. — best-effort */
	}
}

export function clearStoredToken(): void {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.removeItem(TOKEN_STORAGE_KEY);
	} catch {
		/* best-effort */
	}
}

export class ApiError extends Error {
	constructor(
		public readonly status: number,
		public readonly detail: string,
		public readonly path: string
	) {
		super(`${status} ${path}: ${detail}`);
		this.name = 'ApiError';
	}
}

interface ApiOptions {
	method?: string;
	body?: unknown;
	signal?: AbortSignal;
}

export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
	const { method = 'GET', body, signal } = options;

	// Build the RequestInit carefully — exactOptionalPropertyTypes
	// means we can't pass `body: undefined` or `signal: undefined`.
	const init: RequestInit = { method };
	const headers: Record<string, string> = {};
	if (body !== undefined) {
		headers['Content-Type'] = 'application/json';
		init.body = JSON.stringify(body);
	}
	const token = getStoredToken();
	if (token) {
		headers['Authorization'] = `Bearer ${token}`;
	}
	if (Object.keys(headers).length > 0) {
		init.headers = headers;
	}
	if (signal !== undefined) {
		init.signal = signal;
	}

	const res = await fetch(path, init);

	// 401 from any endpoint clears the cached token so the UI's auth
	// guard redirects to /login on the next render.
	if (res.status === 401) {
		clearStoredToken();
	}

	if (!res.ok) {
		let detail = res.statusText;
		try {
			const errBody = await res.json();
			if (typeof errBody.detail === 'string') {
				detail = errBody.detail;
			} else if (Array.isArray(errBody.detail)) {
				// Pydantic validation errors land as an array of objects.
				detail = errBody.detail
					.map((e: { msg?: string; loc?: unknown[] }) => e.msg ?? '')
					.filter(Boolean)
					.join('; ');
			}
		} catch {
			/* fall through to statusText */
		}
		throw new ApiError(res.status, detail, path);
	}

	if (res.status === 204) {
		return undefined as T;
	}
	return (await res.json()) as T;
}

export const api = {
	get: <T>(path: string, signal?: AbortSignal) =>
		apiFetch<T>(path, signal !== undefined ? { signal } : {}),
	post: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: 'POST', body }),
	put: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: 'PUT', body }),
	patch: <T>(path: string, body: unknown) => apiFetch<T>(path, { method: 'PATCH', body }),
	del: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' })
};
