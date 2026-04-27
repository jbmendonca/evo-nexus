const API = import.meta.env.DEV ? 'http://localhost:8080' : '';

// Sent on all mutating requests for CSRF mitigation (backend checks this header).
// Browsers cannot forge custom headers cross-origin without a CORS preflight,
// which the backend rejects for non-allowlisted origins.
export const XHR_HEADER = { 'X-Requested-With': 'XMLHttpRequest' };

let csrfToken: string | null = null;
let csrfTokenPromise: Promise<string | null> | null = null;

function updateCsrfTokenFromResponse(res: Response) {
  const token = res.headers.get('X-CSRF-Token') || res.headers.get('x-csrf-token');
  if (token) csrfToken = token;
}

function updateCsrfTokenFromPayload(payload: unknown) {
  if (!payload || typeof payload !== 'object') return;
  const token = (payload as { csrf_token?: unknown }).csrf_token;
  if (typeof token === 'string' && token) csrfToken = token;
}

function parseMaybeJson(text: string) {
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function errorMessageFromResponse(res: Response, text: string) {
  const parsed = parseMaybeJson(text);
  if (parsed && typeof parsed === 'object') {
    const message = (parsed as Record<string, unknown>).error
      || (parsed as Record<string, unknown>).message
      || (parsed as Record<string, unknown>).detail
      || (parsed as Record<string, unknown>).description;
    if (typeof message === 'string' && message.trim()) {
      return message.trim();
    }
  }
  const trimmed = text.trim();
  return trimmed || `${res.status} ${res.statusText}`;
}

async function refreshCsrfToken() {
  if (csrfTokenPromise) return csrfTokenPromise;
  csrfTokenPromise = fetch(`${API}/api/auth/csrf`, { credentials: 'include' })
    .then(async (res) => {
      updateCsrfTokenFromResponse(res);
      const text = await res.text();
      if (!res.ok) {
        throw new Error(errorMessageFromResponse(res, text));
      }
      const payload = parseMaybeJson(text);
      updateCsrfTokenFromPayload(payload);
      return csrfToken;
    })
    .finally(() => {
      csrfTokenPromise = null;
    });
  return csrfTokenPromise;
}

async function readJsonResponse(res: Response) {
  updateCsrfTokenFromResponse(res);
  const text = await res.text();
  if (!res.ok) {
    throw new Error(errorMessageFromResponse(res, text));
  }
  const payload = parseMaybeJson(text);
  updateCsrfTokenFromPayload(payload);
  return payload;
}

async function readTextResponse(res: Response) {
  updateCsrfTokenFromResponse(res);
  const text = await res.text();
  if (!res.ok) {
    throw new Error(errorMessageFromResponse(res, text));
  }
  return text;
}

export const api = {
  get: async (path: string) => {
    const res = await fetch(`${API}/api${path}`, { credentials: 'include' });
    return readJsonResponse(res);
  },
  getRaw: async (path: string) => {
    const res = await fetch(`${API}/api${path}`, { credentials: 'include' });
    return readTextResponse(res);
  },
  post: async (path: string, body?: unknown) => {
    await refreshCsrfToken();
    const res = await fetch(`${API}/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER, ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    return readJsonResponse(res);
  },
  put: async (path: string, body?: unknown) => {
    await refreshCsrfToken();
    const res = await fetch(`${API}/api${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER, ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    return readJsonResponse(res);
  },
  patch: async (path: string, body?: unknown) => {
    await refreshCsrfToken();
    const res = await fetch(`${API}/api${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER, ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    return readJsonResponse(res);
  },
  delete: async (path: string) => {
    await refreshCsrfToken();
    const res = await fetch(`${API}/api${path}`, {
      method: 'DELETE',
      headers: { ...XHR_HEADER, ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
      credentials: 'include',
    });
    return readJsonResponse(res);
  },
};
