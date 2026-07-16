<script>
  // Рекурсивное отображение JSON-структуры персонажа —
  // используется при отправке листа персонажа в чат игры.
  import DataTree from './DataTree.svelte';

  let { data } = $props();

  const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
</script>

{#if Array.isArray(data)}
  <ul>
    {#each data as item}
      <li><DataTree data={item} /></li>
    {/each}
  </ul>
{:else if data !== null && typeof data === 'object'}
  <table>
    <tbody>
      {#each Object.entries(data) as [key, value]}
        <tr>
          <td class="key">{key}</td>
          <td>
            {#if isObject(value) || Array.isArray(value)}
              <DataTree data={value} />
            {:else}
              {String(value)}
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
{:else}
  {String(data)}
{/if}

<style>
  table { border-collapse: collapse; width: 100%; }
  td { padding: 2px 8px 2px 0; vertical-align: top; }
  td.key { color: var(--ink-soft); white-space: nowrap; width: 1%; padding-right: 14px; }
  ul { margin: 0; padding-left: 18px; }
</style>
