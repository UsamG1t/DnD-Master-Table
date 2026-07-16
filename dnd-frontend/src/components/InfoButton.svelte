<script>
  // Кнопка info рядом с каждым данным: показывает сжатое описание
  // (получено из backend, без вложенных ссылок), внизу — ссылка на
  // полное описание в базе данных-источнике.
  import Modal from './Modal.svelte';
  import { getInfo, FIELD_LABELS } from '../lib/dnd.js';

  let { category, index, disabled = false } = $props();

  let open = $state(false);
  let info = $state(null);
  let error = $state('');

  async function show() {
    open = true;
    info = null;
    error = '';
    try {
      info = await getInfo(category, index);
    } catch (e) {
      error = e.message;
    }
  }

  function fmt(value) {
    if (Array.isArray(value)) return value.join(', ');
    if (value === true) return 'да';
    if (value === false) return 'нет';
    return String(value);
  }

  const shortFields = $derived(
    info
      ? Object.entries(info).filter(
          ([k]) => !['name', 'description', 'higher_level', 'source_url'].includes(k)
        )
      : []
  );
</script>

<button class="info" title="Описание" aria-label="Описание" {disabled} onclick={show}>i</button>

{#if open}
  <Modal title={info?.name ?? 'Описание'} onclose={() => (open = false)}>
    {#if error}
      <p class="error">Не удалось получить описание: {error}</p>
    {:else if !info}
      <p class="muted">Загрузка…</p>
    {:else}
      <table>
        <tbody>
          {#each shortFields as [key, value]}
            <tr>
              <td class="key">{FIELD_LABELS[key] ?? key}</td>
              <td>{fmt(value)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      {#if info.description}
        <div class="rule">Описание</div>
        <p class="desc">{info.description}</p>
      {/if}
      {#if info.higher_level}
        <div class="rule">На больших кругах</div>
        <p class="desc">{info.higher_level}</p>
      {/if}
      <p class="source">
        <a href={info.source_url} target="_blank" rel="noopener noreferrer">
          Полное описание в базе данных →
        </a>
      </p>
    {/if}
  </Modal>
{/if}

<style>
  table { width: 100%; border-collapse: collapse; }
  td { padding: 3px 8px 3px 0; vertical-align: top; border-bottom: 1px solid rgba(0,0,0,0.08); }
  td.key { color: var(--ink-soft); white-space: nowrap; width: 1%; padding-right: 16px; }
  .desc { white-space: pre-wrap; margin: 4px 0; }
  .source { margin-top: 16px; text-align: right; }
</style>
