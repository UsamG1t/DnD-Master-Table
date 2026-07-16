<script>
  // Игровое пространство: чат (live по WebSocket), броски кубиков,
  // лог изменений, участники, персонажи игры, отправка листа в чат
  // (мастер может скрыть выбранные характеристики своих персонажей).
  import { api, wsUrl, getToken } from '../lib/api.js';
  import { auth } from '../lib/auth.svelte.js';
  import { getSkillsFull, skillLabel } from '../lib/dnd.js';
  import Modal from '../components/Modal.svelte';
  import GameMessage from '../components/GameMessage.svelte';

  let { gameId } = $props();

  let game = $state(null);
  let members = $state([]);
  let gameChars = $state(null); // null = эндпоинт недоступен (нужен backend-patch)
  let myChars = $state([]);
  let messages = $state([]);
  let error = $state('');
  let wsState = $state('подключение…');

  let chatText = $state('');
  let dice = $state({ count: 1, sides: 20, modifier: 0, comment: '' });
  let skillsFull = $state([]); // все навыки игры — список одинаков для всех
  let rollSkill = $state('');
  let rollCharId = $state('');
  let selectedFreeChar = $state('');
  let npcChar = $state('');
  let shareChar = $state(null); // персонаж в модале отправки листа
  let sharePaths = $state({});  // path -> скрыть?

  const myMember = $derived(members.find((m) => m.user_id === auth.user?.id));
  const isMaster = $derived(Boolean(game?.is_master));
  const freeChars = $derived(myChars.filter((c) => !c.in_game));
  const myInGameChars = $derived(myChars.filter((c) => c.in_game && c.game?.id === Number(gameId)));

  let feedEl = $state(null);
  function scrollFeed() {
    setTimeout(() => feedEl?.scrollTo({ top: feedEl.scrollHeight }), 0);
  }

  async function loadAll() {
    try {
      [game, members, messages, myChars] = await Promise.all([
        api(`/games/${gameId}`),
        api(`/games/${gameId}/members`),
        api(`/games/${gameId}/messages`),
        api('/characters'),
      ]);
      scrollFeed();
      try {
        gameChars = await api(`/games/${gameId}/characters`);
      } catch {
        gameChars = null; // без backend-patch списка персонажей игры нет
      }
    } catch (e) {
      error = e.message;
    }
  }
  loadAll();

  // WebSocket с автоматической очисткой при уходе со страницы
  $effect(() => {
    const socket = new WebSocket(wsUrl(`/games/${gameId}/ws?token=${getToken()}`));
    socket.onopen = () => (wsState = 'в эфире');
    socket.onclose = () => (wsState = 'нет связи');
    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      messages = [...messages, msg];
      scrollFeed();
      if (msg.type === 'system') {
        // состав игры изменился — обновляем списки
        api(`/games/${gameId}/members`).then((m) => (members = m));
        api('/characters').then((c) => (myChars = c));
        api(`/games/${gameId}/characters`).then((c) => (gameChars = c)).catch(() => {});
      }
    };
    return () => socket.close();
  });

  async function sendChat() {
    const text = chatText.trim();
    if (!text) return;
    chatText = '';
    await api(`/games/${gameId}/chat`, { method: 'POST', body: { text } });
  }

  async function roll() {
    await api(`/games/${gameId}/roll`, {
      method: 'POST',
      body: {
        count: Number(dice.count), sides: Number(dice.sides),
        modifier: Number(dice.modifier), comment: dice.comment || null,
      },
    });
    dice.comment = '';
  }

  // Список навыков для броска — один и тот же для всех участников
  getSkillsFull().then((s) => (skillsFull = s)).catch(() => {});

  // Держим в селекте актуального персонажа: первый свой при появлении
  $effect(() => {
    if (myInGameChars.length === 0) {
      if (rollCharId !== '') rollCharId = '';
    } else if (!myInGameChars.some((c) => String(c.id) === rollCharId)) {
      rollCharId = String(myInGameChars[0].id);
    }
  });

  async function skillRoll() {
    try {
      await api(`/games/${gameId}/skill-roll`, {
        method: 'POST',
        body: {
          skill: rollSkill,
          character_id: Number(rollCharId),
        },
      });
    } catch (e) {
      alert(e.message);
    }
  }

  async function selectCharacter() {
    try {
      await api(`/games/${gameId}/select-character`, {
        method: 'POST', body: { character_id: Number(selectedFreeChar) },
      });
    } catch (e) {
      alert(e.message);
    }
  }

  async function addNpc() {
    try {
      await api(`/games/${gameId}/characters`, {
        method: 'POST', body: { character_id: Number(npcChar) },
      });
      npcChar = '';
    } catch (e) {
      alert(e.message);
    }
  }

  async function removeChar(charId) {
    if (!confirm('Вывести персонажа из игры?')) return;
    try {
      await api(`/games/${gameId}/characters/${charId}/remove`, { method: 'POST' });
    } catch (e) {
      alert(e.message);
    }
  }

  function openShare(char) {
    shareChar = char;
    const paths = [];
    for (const key of Object.keys(char.data ?? {})) {
      if (key === 'stats') {
        for (const sub of Object.keys(char.data.stats ?? {})) paths.push(`stats.${sub}`);
      } else paths.push(key);
    }
    sharePaths = Object.fromEntries(paths.map((p) => [p, false]));
  }

  function setAllShare(value) {
    for (const path of Object.keys(sharePaths)) sharePaths[path] = value;
  }

  async function shareSheet() {
    const hidden = Object.entries(sharePaths).filter(([, v]) => v).map(([p]) => p);
    try {
      await api(`/games/${gameId}/share-sheet`, {
        method: 'POST',
        body: { character_id: shareChar.id, hidden_paths: hidden },
      });
      shareChar = null;
    } catch (e) {
      alert(e.message);
    }
  }
</script>

{#if error}
  <p class="error">{error}</p>
{:else if !game}
  <p class="muted">Загрузка игры…</p>
{:else}
  <div class="spread head">
    <h1>
      {game.name}
      {#if isMaster}<span class="badge">Мастер</span>{/if}
    </h1>
    <span class="muted">Связь: {wsState}</span>
  </div>

  {#if isMaster && game.credentials}
    <div class="panel creds">
      <b>Доступ для игроков</b> — логин
      <code class="mono">{game.credentials.login}</code> · пароль
      <code class="mono">{game.credentials.password}</code>
      <button
        class="ghost small"
        onclick={() => navigator.clipboard.writeText(`Логин: ${game.credentials.login}\nПароль: ${game.credentials.password}`)}
      >Скопировать</button>
    </div>
  {/if}

  <div class="layout">
    <!-- ============ Чат и лог ============ -->
    <div class="panel chat">
      <div class="feed" bind:this={feedEl}>
        {#each messages as message (message.id)}
          <GameMessage {message} />
        {/each}
        {#if messages.length === 0}
          <p class="muted">Пока тихо. Напишите первое сообщение или бросьте кубики.</p>
        {/if}
      </div>

      <div class="row send">
        <input
          bind:value={chatText}
          placeholder="Сообщение в чат…"
          onkeydown={(e) => e.key === 'Enter' && sendChat()}
          style="flex: 1"
        />
        <button onclick={sendChat} disabled={!chatText.trim()}>Отправить</button>
      </div>

      <div class="row dice mono">
        <span>Бросок:</span>
        <input type="number" min="1" max="100" bind:value={dice.count} style="width: 64px" />
        <span>d</span>
        <input type="number" min="2" max="1000" bind:value={dice.sides} style="width: 72px" />
        <span>+</span>
        <input type="number" bind:value={dice.modifier} style="width: 64px" />
        <input bind:value={dice.comment} placeholder="комментарий" style="flex: 1; min-width: 120px" />
        <button onclick={roll}>Бросить</button>
      </div>

      <div class="row dice mono">
        <span>По навыку:</span>
        <select bind:value={rollSkill} style="flex: 1; min-width: 160px; width: auto">
          <option value="">— навык —</option>
          {#each skillsFull as skill}
            <option value={skill.index}>{skillLabel(skill)}</option>
          {/each}
        </select>
        <select bind:value={rollCharId} style="width: auto" disabled={myInGameChars.length === 0}>
          {#if myInGameChars.length === 0}
            <option value="">— нет персонажа в игре —</option>
          {:else}
            {#each myInGameChars as c}<option value={String(c.id)}>{c.name}</option>{/each}
          {/if}
        </select>
        <button
          onclick={skillRoll}
          disabled={!rollSkill || !rollCharId}
          title={myInGameChars.length === 0 ? 'Сначала выставьте персонажа в игру' : ''}
        >Бросок по навыку</button>
      </div>
    </div>

    <!-- ============ Боковая панель ============ -->
    <aside class="side">
      {#if !isMaster && myMember && myMember.character_id == null}
        <div class="panel">
          <h3>Выберите персонажа</h3>
          <p class="muted">Список ваших свободных персонажей — тех, кто ещё не занят в играх.</p>
          {#if freeChars.length === 0}
            <p class="muted">Свободных персонажей нет — <a href="#/characters/new">создайте нового</a>.</p>
          {:else}
            <select bind:value={selectedFreeChar}>
              <option value="">— выберите —</option>
              {#each freeChars as c}<option value={c.id}>{c.name}</option>{/each}
            </select>
            <button style="margin-top: 8px" disabled={!selectedFreeChar} onclick={selectCharacter}>
              Играть этим персонажем
            </button>
          {/if}
        </div>
      {/if}

      <div class="panel">
        <h3>Участники</h3>
        {#each members as m}
          <div class="spread member">
            <span>{m.username} {#if m.is_master}<span class="badge">Мастер</span>{/if}</span>
            {#if m.character_id}
              <span class="muted">{gameChars?.find((c) => c.id === m.character_id)?.name ?? `персонаж #${m.character_id}`}</span>
            {/if}
          </div>
        {/each}
      </div>

      <div class="panel">
        <h3>Персонажи игры</h3>
        {#if gameChars === null}
          <p class="muted">Нужен backend-patch, чтобы видеть полный список (см. README).</p>
        {:else if gameChars.length === 0}
          <p class="muted">В игре пока нет персонажей.</p>
        {:else}
          {#each gameChars as c}
            <div class="spread member">
              <span>{c.name} <i class="muted">({c.owner_name})</i></span>
              {#if isMaster || c.owner_id === auth.user?.id}
                <button class="ghost small" onclick={() => removeChar(c.id)}>Вывести</button>
              {/if}
            </div>
          {/each}
        {/if}

        {#if myInGameChars.length}
          <div class="rule" style="color: var(--gold)">Лист в чат</div>
          {#each myInGameChars as c}
            <div class="spread member">
              <span>{c.name}</span>
              <button class="small" onclick={() => openShare(c)}>Показать лист</button>
            </div>
          {/each}
        {/if}

        {#if isMaster}
          <div class="rule" style="color: var(--gold)">Добавить персонажа мастера</div>
          {#if freeChars.length === 0}
            <p class="muted">Нет свободных персонажей.</p>
          {:else}
            <div class="row">
              <select bind:value={npcChar} style="flex: 1">
                <option value="">— выберите —</option>
                {#each freeChars as c}<option value={c.id}>{c.name}</option>{/each}
              </select>
              <button class="small" disabled={!npcChar} onclick={addNpc}>Добавить</button>
            </div>
          {/if}
        {/if}
      </div>
    </aside>
  </div>
{/if}

{#if shareChar}
  <Modal title={`Лист «${shareChar.name}» в чат`} onclose={() => (shareChar = null)}>
    {#if isMaster}
      <p class="muted">Отметьте характеристики, которые не показывать игрокам. Для персонажей игроков скрытие невозможно.</p>
      <div class="row" style="margin-bottom: 8px">
        <button class="ghost small" onclick={() => setAllShare(true)}>Выбрать всё</button>
        <button class="ghost small" onclick={() => setAllShare(false)}>Сбросить всё</button>
      </div>
      {#each Object.keys(sharePaths) as path}
        <label class="row" style="margin: 4px 0">
          <input type="checkbox" bind:checked={sharePaths[path]} />
          <span class="mono">{path}</span>
        </label>
      {/each}
    {:else}
      <p class="muted">Лист будет отправлен целиком — скрыть параметры персонажей игроков нельзя.</p>
    {/if}
    <div class="row" style="margin-top: 14px">
      <button onclick={shareSheet}>Отправить в чат</button>
    </div>
  </Modal>
{/if}

<style>
  .head { margin-bottom: 12px; }
  .creds { margin-bottom: 16px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  code { background: #1a1f29; padding: 2px 8px; border-radius: 4px; }
  .layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .chat { display: flex; flex-direction: column; }
  .feed { height: 55vh; overflow-y: auto; padding-right: 6px; }
  .send { margin-top: 10px; }
  .dice { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--felt-3); }
  .side { display: flex; flex-direction: column; gap: 16px; }
  .member { padding: 3px 0; }
</style>
