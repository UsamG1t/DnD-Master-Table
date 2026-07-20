<script>
  // Settings — страница администратора сервера (SERVER_ADMIN):
  // пользователи с профилями, сводка кешей базы DnD, логи системы.
  import { api } from '../lib/api.js';
  import { auth } from '../lib/auth.svelte.js';

  let users = $state([]);
  let caches = $state(null);
  let requestLogs = $state([]);
  let systemLogs = $state([]);
  let logTab = $state('system'); // 'system' | 'requests'
  let error = $state('');
  let rebuildNote = $state('');

  async function loadUsers() {
    users = await api('/settings/users');
  }
  async function loadCaches() {
    caches = await api('/settings/caches');
  }
  async function loadLogs() {
    [systemLogs, requestLogs] = [
      await api('/settings/logs/system'),
      await api('/settings/logs/requests'),
    ];
  }
  async function loadAll() {
    error = '';
    try {
      await Promise.all([loadUsers(), loadCaches(), loadLogs()]);
    } catch (e) {
      error = e.message;
    }
  }
  loadAll();

  // Пока идёт сборка кеша — обновляем её статус раз в 3 секунды
  $effect(() => {
    if (!caches?.builder?.running) return;
    const timer = setInterval(() => loadCaches().catch(() => {}), 3000);
    return () => clearInterval(timer);
  });

  async function removeUser(user) {
    if (!confirm(`Удалить пользователя «${user.username}» со всеми его персонажами, играми и объектами?`)) return;
    try {
      await api(`/settings/users/${user.id}`, { method: 'DELETE' });
      await Promise.all([loadUsers(), loadLogs()]);
    } catch (e) {
      alert(e.message);
    }
  }

  async function rebuildCaches() {
    rebuildNote = '';
    try {
      const result = await api('/dnd/cache/rebuild', { method: 'POST' });
      rebuildNote = result.started
        ? 'Пересборка запущена в фоне'
        : 'Сборка уже идёт';
      await loadCaches();
    } catch (e) {
      alert(e.message);
    }
  }

  const time = (iso) => new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
</script>

<h1>Settings</h1>
{#if error}<p class="error">{error}</p>{/if}

<!-- ================ Пользователи ================ -->
<div class="panel section">
  <div class="spread">
    <h3>Пользователи</h3>
    <button class="ghost small" onclick={loadUsers}>Обновить</button>
  </div>
  <table class="wide">
    <thead>
      <tr>
        <th>Пользователь</th><th>Email</th><th>Персонажи</th>
        <th>Создал игр</th><th>Участвует в играх</th><th>Создан</th><th></th>
      </tr>
    </thead>
    <tbody>
      {#each users as u (u.id)}
        <tr>
          <td>
            {u.username}
            {#if u.is_server_admin}<span class="badge">SERVER_ADMIN</span>
            {:else if u.is_admin}<span class="badge">админ</span>{/if}
          </td>
          <td class="muted">{u.email}</td>
          <td class="mono num">{u.characters_count}</td>
          <td class="mono num">{u.games_created}</td>
          <td class="mono num">{u.games_playing}</td>
          <td class="muted">{new Date(u.created_at).toLocaleDateString('ru-RU')}</td>
          <td>
            <button
              class="danger small"
              disabled={u.id === auth.user?.id || u.is_server_admin}
              onclick={() => removeUser(u)}
            >Удалить</button>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<!-- ================ Кеши ================ -->
<div class="panel section">
  <div class="spread">
    <h3>Кеши базы DnD</h3>
    <button onclick={rebuildCaches} disabled={caches?.builder?.running}>
      {caches?.builder?.running ? 'Сборка идёт…' : 'Обновить внутренние кеши'}
    </button>
  </div>
  {#if rebuildNote}<p class="muted">{rebuildNote}</p>{/if}
  {#if caches}
    <div class="cache-grid mono">
      <div class="cache-card">
        <b>Статический кеш</b>
        <span>каталог: {caches.builder.cache_dir}</span>
        <span>файлов: {caches.builder.cache_files ?? '—'}</span>
        <span>состояние: {caches.builder.phase}
          {#if caches.builder.running}
            ({caches.builder.categories_done}/{caches.builder.categories_total} категорий,
            {caches.builder.files_written} файлов)
          {/if}
        </span>
        {#if caches.builder.finished_at}<span>завершено: {time(caches.builder.finished_at)}</span>{/if}
        {#if caches.builder.error}<span class="error">ошибка: {caches.builder.error}</span>{/if}
        {#each caches.builder.warnings ?? [] as w}<span class="warn">! {w}</span>{/each}
      </div>
      <div class="cache-card">
        <b>Community-кеш (DnD RFC)</b>
        <span>каталог: {caches.community.dir}</span>
        <span>файлов: {caches.community.files}</span>
      </div>
      <div class="cache-card">
        <b>Кеш в БД</b>
        <span>записей: {caches.db_cache_rows}</span>
        <span>версии API: {caches.api_prefixes.join(' → ')}</span>
      </div>
    </div>
  {:else}
    <p class="muted">Загрузка…</p>
  {/if}
</div>

<!-- ================ Логи ================ -->
<div class="panel section">
  <div class="spread">
    <h3>Логи системы</h3>
    <div class="row">
      <button class:ghost={logTab !== 'system'} class="small" onclick={() => (logTab = 'system')}>
        Внутренние системы
      </button>
      <button class:ghost={logTab !== 'requests'} class="small" onclick={() => (logTab = 'requests')}>
        Запросы
      </button>
      <button class="ghost small" onclick={() => loadLogs().catch((e) => alert(e.message))}>Обновить</button>
    </div>
  </div>

  {#if logTab === 'system'}
    {#if systemLogs.length === 0}
      <p class="muted">Событий пока нет.</p>
    {/if}
    <div class="log mono">
      {#each systemLogs as entry (entry.id)}
        <div class="log-line">
          <span class="muted">{time(entry.created_at)}</span>
          <span>{entry.message}</span>
          {#if entry.actor_name && !entry.is_you}
            <span class="muted">· {entry.actor_name}</span>
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <div class="log mono">
      {#each requestLogs as entry, i (i)}
        <div class="log-line">
          <span class="muted">{entry.ts.slice(11, 19)}</span>
          <span class="method">{entry.method}</span>
          <span class="path">{entry.path}</span>
          <span class:err={entry.status >= 400}>{entry.status}</span>
          <span class="muted">{entry.ms} мс</span>
          {#if entry.username}<span class="muted">· {entry.username}</span>{/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .section { margin-bottom: 20px; }
  table.wide { width: 100%; border-collapse: collapse; }
  table.wide th {
    text-align: left; font-size: 0.72rem; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--text-dim); font-weight: 500; padding: 4px 10px;
  }
  table.wide td { padding: 5px 10px; border-top: 1px solid var(--felt-3); }
  td.num { text-align: center; }

  .cache-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; font-size: 0.82rem; }
  .cache-card { display: flex; flex-direction: column; gap: 3px; border: 1px solid var(--felt-3); border-radius: var(--radius); padding: 10px 12px; }
  .cache-card b { font-family: var(--font-body); }
  .warn { color: #e0b273; }

  .log { max-height: 420px; overflow-y: auto; font-size: 0.8rem; }
  .log-line { display: flex; gap: 10px; padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.04); flex-wrap: wrap; }
  .method { color: var(--gold); min-width: 52px; }
  .path { flex: 1; min-width: 200px; overflow-wrap: anywhere; }
  .err { color: #e08573; font-weight: 700; }
</style>
