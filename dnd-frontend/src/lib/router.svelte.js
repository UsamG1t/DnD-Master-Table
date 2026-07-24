// Простой hash-роутер: #/characters, #/characters/12, #/games/3 ...
export const router = $state({ path: location.hash.slice(1) || '/' });

window.addEventListener('hashchange', () => {
  router.path = location.hash.slice(1) || '/';
});

export function navigate(path) {
  location.hash = path;
}

/** Разбирает текущий путь в {page, id}. */
export function parse(path) {
  const seg = path.split('/').filter(Boolean);
  if (seg.length === 0) return { page: 'characters', id: null };
  if (seg[0] === 'login') return { page: 'login', id: null };
  if (seg[0] === 'characters') {
    if (seg[1] === 'new') return { page: 'editor', id: null };
    if (seg[1]) return { page: 'editor', id: Number(seg[1]) };
    return { page: 'characters', id: null };
  }
  if (seg[0] === 'rfc') return { page: 'rfc', id: null };
  if (seg[0] === 'settings') return { page: 'settings', id: null };
  if (seg[0] === 'games') {
    if (seg[1]) return { page: 'game', id: Number(seg[1]) };
    return { page: 'games', id: null };
  }
  return { page: 'characters', id: null };
}
