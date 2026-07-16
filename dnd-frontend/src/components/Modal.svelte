<script>
  let { title = '', onclose, children } = $props();

  function onkeydown(e) {
    if (e.key === 'Escape') onclose?.();
  }
</script>

<svelte:window {onkeydown} />

<div class="backdrop" onclick={(e) => e.target === e.currentTarget && onclose?.()} role="presentation">
  <div class="dialog paper" role="dialog" aria-modal="true" aria-label={title}>
    <div class="spread head">
      <h3>{title}</h3>
      <button class="ghost small" onclick={() => onclose?.()}>Закрыть</button>
    </div>
    {@render children?.()}
  </div>
</div>

<style>
  .backdrop {
    position: fixed; inset: 0; z-index: 50;
    background: rgba(10, 13, 18, 0.65);
    display: flex; align-items: center; justify-content: center;
    padding: 20px;
  }
  .dialog {
    width: min(560px, 100%);
    max-height: 85vh; overflow-y: auto;
  }
  .head { margin-bottom: 8px; }
</style>
