// Обёртка над fetch: подставляет JWT, разбирает ошибки backend.
export const API_BASE = import.meta.env.VITE_API_BASE || '';

export function getToken() {
  return localStorage.getItem('token');
}
export function setToken(token) {
  localStorage.setItem('token', token);
}
export function clearToken() {
  localStorage.removeItem('token');
}

export class ApiError extends Error {
  constructor(message, status, detail = null) {
    super(message);
    this.status = status;
    this.detail = detail; // исходное тело detail (строка или объект)
  }
}

export async function api(path, { method = 'GET', body, form } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers['Authorization'] = 'Bearer ' + token;

  let payload;
  if (form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    payload = new URLSearchParams(form);
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  const res = await fetch(API_BASE + path, { method, headers, body: payload });

  if (res.status === 401 && path !== '/auth/login') {
    clearToken();
    location.hash = '/login';
  }
  if (!res.ok) {
    let detail = res.statusText;
    let rawDetail = null;
    try {
      const data = await res.json();
      if (data.detail !== undefined) {
        rawDetail = data.detail;
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      }
    } catch { /* тело не JSON */ }
    throw new ApiError(detail, res.status, rawDetail);
  }
  if (res.status === 204) return null;
  const contentType = res.headers.get('content-type') ?? '';
  if (!contentType.includes('json')) {
    // Типичная причина: путь не проксируется на backend и Vite отдал index.html
    throw new ApiError(
      `Backend вернул не JSON (${contentType || 'без content-type'}) для ${path} — ` +
      'проверьте прокси в vite.config.js и что роутер подключён в main.py',
      res.status
    );
  }
  return res.json();
}

/** ws(s)://-адрес для WebSocket игровой комнаты.
 *  API_BASE может быть абсолютным (dev-прокси, http://…) или относительным
 *  префиксом (прод, "/api"). В обоих случаях возвращаем абсолютный ws-URL
 *  с корректной схемой (wss на HTTPS-странице). */
export function wsUrl(path) {
  let base = API_BASE || location.origin;
  if (!/^https?:\/\//.test(base)) {
    // относительный префикс вроде "/api" — приклеиваем к текущему origin
    base = location.origin + base;
  }
  return base.replace(/^http/, 'ws') + path;
}
