<script>
  // Одно сообщение игрового пространства. Типы соответствуют backend:
  // chat, dice, sheet (лист персонажа), log (изменения параметров), system.
  import DataTree from './DataTree.svelte';

  let { message } = $props();

  const time = $derived(
    new Date(message.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  );
</script>

<div class="msg {message.type}">
  <span class="meta mono">{time}</span>

  {#if message.type === 'chat'}
    <span class="author">{message.author_name}:</span>
    <span>{message.payload.text}</span>

  {:else if message.type === 'dice'}
    <span class="author">{message.author_name}</span>
    <span class="mono">
      {#if message.payload.skill}
        проверка «{message.payload.skill.name}» за {message.payload.breakdown?.character}:
        [{message.payload.rolls.join(', ')}]
        {message.payload.modifier > 0 ? ' +' : ' '}{message.payload.modifier}
        = <b class="total">{message.payload.total}</b>
        <span class="muted">
          (БМ +{message.payload.breakdown?.proficiency},
          характеристика {message.payload.breakdown?.ability_mod >= 0 ? '+' : ''}{message.payload.breakdown?.ability_mod},
          навык +{message.payload.breakdown?.skill_bonus})
        </span>
      {:else}
        бросает {message.payload.notation}: [{message.payload.rolls.join(', ')}]
        {#if message.payload.modifier}{message.payload.modifier > 0 ? ' +' : ' '}{message.payload.modifier}{/if}
        = <b class="total">{message.payload.total}</b>
      {/if}
    </span>
    {#if message.payload.comment}<span class="muted">({message.payload.comment})</span>{/if}

  {:else if message.type === 'sheet'}
    <div>
      <span class="author">{message.author_name}</span>
      <span>показывает лист персонажа <b>{message.payload.character_name}</b></span>
      {#if message.payload.hidden_paths?.length}
        <span class="muted">(часть характеристик скрыта мастером)</span>
      {/if}
      <details class="paper sheet">
        <summary>Развернуть лист</summary>
        <DataTree data={message.payload.data} />
      </details>
    </div>

  {:else if message.type === 'log'}
    <div class="log-events mono">
      {#each message.payload.events as ev}
        <div>
          <b>{ev.character_name}</b>: {ev.path} → {JSON.stringify(ev.value)}
          <span class="meta">({ev.changed_by})</span>
        </div>
      {/each}
    </div>

  {:else if message.type === 'system'}
    <span class="system">
      {#if message.payload.event === 'character_joined'}
        Персонаж «{message.payload.character_name}» вступает в игру
      {:else if message.payload.event === 'npc_added'}
        Мастер вводит персонажа «{message.payload.character_name}»
      {:else if message.payload.event === 'character_left'}
        Персонаж «{message.payload.character_name}» покидает игру
      {:else}
        {JSON.stringify(message.payload)}
      {/if}
    </span>
  {/if}
</div>

<style>
  .msg { padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
  .meta { color: var(--text-dim); font-size: 0.75rem; margin-right: 8px; }
  .author { font-weight: 700; color: var(--gold); margin-right: 6px; }
  .total { color: var(--gold); font-size: 1.05em; }
  .system { color: var(--text-dim); font-style: italic; }
  .log-events { display: inline-block; color: #a9c3b1; font-size: 0.82rem; }
  .sheet { margin-top: 6px; padding: 10px 14px; }
  .sheet summary { cursor: pointer; font-weight: 700; }
</style>
