<script>
  import { auth, loadMe, logout } from './lib/auth.svelte.js';
  import { router, parse, navigate } from './lib/router.svelte.js';
  import Auth from './pages/Auth.svelte';
  import Characters from './pages/Characters.svelte';
  import Editor from './pages/Editor.svelte';
  import Games from './pages/Games.svelte';
  import GameRoom from './pages/GameRoom.svelte';
  import Rfc from './pages/Rfc.svelte';
  import Settings from './pages/Settings.svelte';

  const route = $derived(parse(router.path));

  $effect(() => {
    if (!auth.token && route.page !== 'login') navigate('/login');
  });

  loadMe();
</script>

{#if !auth.token || route.page === 'login'}
  <Auth />
{:else}
  <nav class="spread">
    <div class="row">
      <span class="brand">Стол мастера</span>
      <a href="#/characters" class:active={route.page === 'characters' || route.page === 'editor'}>Персонажи</a>
      <a href="#/games" class:active={route.page === 'games' || route.page === 'game'}>Игры</a>
      <a href="#/rfc" class:active={route.page === 'rfc'}>DnD RFC</a>
      {#if auth.isServerAdmin}
        <a href="#/settings" class:active={route.page === 'settings'}>Settings</a>
      {/if}
    </div>
    <div class="row">
      {#if auth.user}
        <span class="muted">
          {auth.user.username}{#if auth.user.is_admin} · админ{/if}
        </span>
      {/if}
      <button class="ghost small" onclick={logout}>Выйти</button>
    </div>
  </nav>

  <main>
    {#if route.page === 'characters'}
      <Characters />
    {:else if route.page === 'editor'}
      {#key route.id}
        <Editor charId={route.id} />
      {/key}
    {:else if route.page === 'games'}
      <Games />
    {:else if route.page === 'game'}
      {#key route.id}
        <GameRoom gameId={route.id} />
      {/key}
    {:else if route.page === 'rfc'}
      <Rfc />
    {:else if route.page === 'settings'}
      <Settings />
    {/if}
  </main>
{/if}

<style>
  nav {
    padding: 10px 20px;
    border-bottom: 1px solid var(--felt-3);
    background: rgba(20, 25, 34, 0.6);
    position: sticky; top: 0; z-index: 10;
    backdrop-filter: blur(6px);
  }
  .brand {
    font-family: var(--font-display);
    font-weight: 800; font-size: 1.15rem;
    color: var(--gold); margin-right: 12px;
  }
  nav a { color: var(--text); padding: 4px 8px; border-radius: 4px; }
  nav a.active { color: var(--gold); }
  main { max-width: 1200px; margin: 0 auto; padding: 20px; }
</style>
