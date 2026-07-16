<script>
  import { api } from '../lib/api.js';
  import { navigate } from '../lib/router.svelte.js';

  let games = $state([]);
  let error = $state('');
  let newName = $state('');
  let joinLogin = $state('');
  let joinPassword = $state('');
  let joinError = $state('');

  async function load() {
    try {
      games = await api('/games');
    } catch (e) {
      error = e.message;
    }
  }
  load();

  async function create() {
    try {
      const game = await api('/games', { method: 'POST', body: { name: newName } });
      newName = '';
      games = [...games, game];
      navigate(`/games/${game.id}`);
    } catch (e) {
      error = e.message;
    }
  }

  async function join() {
    joinError = '';
    try {
      const game = await api('/games/join', {
        method: 'POST',
        body: { login: joinLogin, password: joinPassword },
      });
      navigate(`/games/${game.id}`);
    } catch (e) {
      joinError = e.message;
    }
  }
</script>

<h1>Игры</h1>
{#if error}<p class="error">{error}</p>{/if}

<div class="cols">
  <div class="panel">
    <h3>Создать игру</h3>
    <p class="muted">Вы получите пометку мастера, логин-пароль игры сгенерируются автоматически — они будут в параметрах игры.</p>
    <label class="field">
      <span>Название</span>
      <input bind:value={newName} placeholder="Проклятье Штральда" />
    </label>
    <button disabled={!newName.trim()} onclick={create}>Создать</button>
  </div>

  <div class="panel">
    <h3>Войти в игру</h3>
    <p class="muted">Логин и пароль игры выдаёт её мастер.</p>
    <label class="field"><span>Логин игры</span><input bind:value={joinLogin} class="mono" /></label>
    <label class="field"><span>Пароль игры</span><input bind:value={joinPassword} class="mono" /></label>
    {#if joinError}<p class="error">{joinError}</p>{/if}
    <button disabled={!joinLogin || !joinPassword} onclick={join}>Войти</button>
  </div>
</div>

<h2 style="margin-top: 24px">Мои игры</h2>
{#if games.length === 0}
  <p class="muted">Вы пока не состоите ни в одной игре.</p>
{/if}
<div class="grid">
  {#each games as game (game.id)}
    <a class="panel game-card" href="#/games/{game.id}">
      <div class="spread">
        <h3>{game.name}</h3>
        {#if game.is_master}<span class="badge">Мастер</span>{/if}
      </div>
      <span class="muted">Создана {new Date(game.created_at).toLocaleDateString('ru-RU')}</span>
    </a>
  {/each}
</div>

<style>
  .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .game-card { display: block; color: var(--text); }
  .game-card:hover { border-color: var(--gold); text-decoration: none; }
</style>
