// Состояние аутентификации. Модуль с рунами: auth реактивен во всех компонентах.
import { api, getToken, setToken, clearToken } from './api.js';

export const auth = $state({
  token: getToken(),
  user: null,
  isServerAdmin: false,
});

export async function login(username, password) {
  const data = await api('/auth/login', { method: 'POST', form: { username, password } });
  setToken(data.access_token);
  auth.token = data.access_token;
  await loadMe();
}

export async function register(username, email, password) {
  await api('/auth/register', { method: 'POST', body: { username, email, password } });
  await login(username, password);
}

export async function loadMe() {
  if (!auth.token) return;
  try {
    auth.user = await api('/auth/me');
    const settings = await api('/settings/me');
    auth.isServerAdmin = Boolean(settings.is_server_admin);
  } catch {
    logout();
  }
}

export function logout() {
  clearToken();
  auth.token = null;
  auth.user = null;
  auth.isServerAdmin = false;
  location.hash = '/login';
}
