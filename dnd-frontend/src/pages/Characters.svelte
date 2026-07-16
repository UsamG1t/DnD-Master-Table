<script>
  // Панель игровых персонажей: характеристики, пометка «В игре»
  // со ссылкой на игру, настройка видимости в логе (для персонажей мастера).
  import { api } from '../lib/api.js';
  import { navigate } from '../lib/router.svelte.js';
  import { ABILITIES, abilityMod, fmtMod } from '../lib/dnd.js';
  import Modal from '../components/Modal.svelte';

  let characters = $state([]);
  let error = $state('');
  let visChar = $state(null); // персонаж в модале настройки видимости
  let visPaths = $state({});  // path -> скрыт?

  async function load() {
    try {
      characters = await api('/characters');
    } catch (e) {
      error = e.message;
    }
  }
  load();

  async function remove(char) {
    if (!confirm(`Удалить персонажа «${char.name}»?`)) return;
    try {
      await api(`/characters/${char.id}`, { method: 'DELETE' });
      characters = characters.filter((c) => c.id !== char.id);
    } catch (e) {
      alert(e.message);
    }
  }

  // Пути, которые можно скрыть из лога игры
  function hidablePaths(char) {
    const paths = [];
    for (const key of Object.keys(char.data ?? {})) {
      if (key === 'stats') {
        for (const sub of Object.keys(char.data.stats ?? {})) paths.push(`stats.${sub}`);
      } else {
        paths.push(key);
      }
    }
    return paths;
  }

  function openVisibility(char) {
    visChar = char;
    visPaths = Object.fromEntries(
      hidablePaths(char).map((p) => [p, (char.log_hidden_paths ?? []).includes(p)])
    );
  }

  async function saveVisibility() {
    const hidden = Object.entries(visPaths).filter(([, v]) => v).map(([p]) => p);
    try {
      const updated = await api(`/characters/${visChar.id}/log-visibility`, {
        method: 'PUT',
        body: { log_hidden_paths: hidden },
      });
      characters = characters.map((c) => (c.id === updated.id ? updated : c));
      visChar = null;
    } catch (e) {
      alert(e.message);
    }
  }

  function setAllVisibility(value) {
    for (const path of Object.keys(visPaths)) visPaths[path] = value;
  }

  /** Итоговое значение характеристики: старт + очки улучшений. */
  function statTotal(char, key) {
    const base = char.data.stats?.[key];
    if (base === undefined) return null;
    return base + (char.data.asi?.bonuses?.[key] ?? 0)
      + (char.data.background_bonuses?.[key] ?? 0);
  }
</script>

<div class="spread head">
  <h1>Игровые персонажи</h1>
  <button onclick={() => navigate('/characters/new')}>Создать персонажа</button>
</div>

{#if error}<p class="error">{error}</p>{/if}

{#if characters.length === 0 && !error}
  <div class="panel">
    <p>Пока ни одного персонажа. Создайте первого — лист заполняется выбором расы, класса и остального из списков.</p>
  </div>
{/if}

<div class="grid">
  {#each characters as char (char.id)}
    <div class="paper card">
      <div class="spread">
        <h2>{char.name}</h2>
        {#if char.in_game}
          <a class="badge game" href="#/games/{char.game.id}" style="pointer-events:auto">
            В игре: {char.game.name}
          </a>
        {/if}
      </div>
      <p class="muted">
        {[char.data.race, char.data.class, char.data.level ? `уровень ${char.data.level}` : null]
          .filter(Boolean)
          .join(' · ') || 'лист не заполнен'}
      </p>

      {#if char.data.stats}
        <div class="stats mono">
          {#each ABILITIES as [key, label]}
            {@const value = statTotal(char, key)}
            <div class="stat">
              <span class="label">{label.slice(0, 3)}</span>
              <span class="value">{value ?? '—'}</span>
              {#if value !== null}<span class="mod">{fmtMod(abilityMod(value))}</span>{/if}
            </div>
          {/each}
        </div>
      {/if}

      {#if char.data.hp}
        <p class="muted mono">Хиты: {char.data.hp.current ?? '—'} / {char.data.hp.max ?? '—'}</p>
      {/if}

      <div class="row actions">
        <button class="small" onclick={() => navigate(`/characters/${char.id}`)}>Редактировать</button>
        <button class="ghost small" onclick={() => openVisibility(char)}>Видимость в логе</button>
        <button class="danger small" disabled={char.in_game} onclick={() => remove(char)}>Удалить</button>
      </div>
    </div>
  {/each}
</div>

{#if visChar}
  <Modal title="Видимость в логе игры" onclose={() => (visChar = null)}>
    <p class="muted">
      Отмеченные характеристики не будут транслироваться в лог игры.
      Работает только для персонажей мастера: параметры персонажей игроков
      скрыть нельзя — для них выбор игнорируется сервером.
    </p>
    {#each Object.keys(visPaths) as path}
      <label class="row" style="margin: 4px 0">
        <input type="checkbox" bind:checked={visPaths[path]} />
        <span class="mono">{path}</span>
      </label>
    {/each}
    <div class="row" style="margin-top: 14px">
      <button onclick={saveVisibility}>Сохранить</button>
      <button class="ghost small" onclick={() => setAllVisibility(true)}>Выбрать всё</button>
      <button class="ghost small" onclick={() => setAllVisibility(false)}>Сбросить всё</button>
    </div>
  </Modal>
{/if}

<style>
  .head { margin-bottom: 16px; }
  .card h2 { margin-bottom: 2px; }
  .stats { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
  .stat {
    display: flex; flex-direction: column; align-items: center;
    border: 1px solid #cabfa4; border-radius: 4px; padding: 3px 7px;
  }
  .stat .label { font-size: 0.62rem; text-transform: uppercase; color: var(--ink-soft); }
  .stat .value { font-weight: 600; }
  .stat .mod { font-size: 0.68rem; color: var(--gold-dim); }
  .actions { margin-top: 10px; }
</style>
