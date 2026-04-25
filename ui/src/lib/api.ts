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
	if (body !== undefined) {
		init.headers = { 'Content-Type': 'application/json' };
		init.body = JSON.stringify(body);
	}
	if (signal !== undefined) {
		init.signal = signal;
	}

	const res = await fetch(path, init);

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
