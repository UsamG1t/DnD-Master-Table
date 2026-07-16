<script>
  import { login, register } from '../lib/auth.svelte.js';
  import { navigate } from '../lib/router.svelte.js';

  let mode = $state('login'); // 'login' | 'register'
  let username = $state('');
  let email = $state('');
  let password = $state('');
  let error = $state('');
  let busy = $state(false);

  async function submit() {
    error = '';
    busy = true;
    try {
      if (mode === 'login') await login(username, password);
      else await register(username, email, password);
      navigate('/characters');
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <div class="paper card">
    <h1>Стол мастера</h1>
    <p class="muted">Персонажи, листы и игровые комнаты DnD</p>

    <div class="row tabs">
      <button class:ghost={mode !== 'login'} onclick={() => (mode = 'login')}>Вход</button>
      <button class:ghost={mode !== 'register'} onclick={() => (mode = 'register')}>Регистрация</button>
    </div>

    <label class="field">
      <span>Имя пользователя</span>
      <input bind:value={username} autocomplete="username" />
    </label>
    {#if mode === 'register'}
      <label class="field">
        <span>Email</span>
        <input type="email" bind:value={email} autocomplete="email" />
      </label>
    {/if}
    <label class="field">
      <span>Пароль {#if mode === 'register'}<i>(не короче 8 символов)</i>{/if}</span>
      <input
        type="password"
        bind:value={password}
        autocomplete={mode === 'login' ? 'current-password' : 'new-password'}
        onkeydown={(e) => e.key === 'Enter' && submit()}
      />
    </label>

    {#if error}<p class="error">{error}</p>{/if}

    <button disabled={busy || !username || !password} onclick={submit}>
      {mode === 'login' ? 'Войти' : 'Создать аккаунт'}
    </button>
  </div>
</div>

<style>
  .wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
  .card { width: min(420px, 100%); }
  .tabs { margin-bottom: 16px; }
</style>
