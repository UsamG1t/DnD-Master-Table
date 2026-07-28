<script>
  // DnD RFC: объекты сообщества. Форма строится динамически по схеме
  // категории (GET /rfc/schema/{category}): типы полей, enum, ссылки на
  // другие карточки (ref/ref_list). Объекты со ссылками на непринятые
  // объекты уходят в статус draft («Не готов к модерации»); автор жмёт
  // «Проверить и отправить» — сервер резолвит ссылки и переводит в pending.
  import { api } from '../lib/api.js';
  import { getList } from '../lib/dnd.js';
  import Modal from '../components/Modal.svelte';
  import DataTree from '../components/DataTree.svelte';

  let categories = $state({});   // {index: описание}
  let objects = $state([]);
  let canReview = $state(false);
  let error = $state('');
  let notice = $state('');

  // Форма
  let editingId = $state(null);
  let category = $state('');
  let schema = $state(null);      // {fields:[...], strict}
  let objName = $state('');
  let fieldValues = $state({});   // key -> значение
  let extraJson = $state('');
  let formError = $state('');
  let fieldErrors = $state([]);   // ошибки полей с сервера
  let loadingSchema = $state(false);

  // Опции для ref-полей: category -> [{index, name, community}]
  let refOptions = $state({});

  // Просмотр карточки
  let viewing = $state(null);
  let viewError = $state('');
  let schemaCache = $state({});

  const STATUS_CLASS = {
    draft: 'draft', pending: 'pending', accepted: 'accepted', rejected: 'rejected',
  };

  async function load() {
    try {
      const [cats, list] = await Promise.all([api('/rfc/categories'), api('/rfc/objects')]);
      categories = cats;
      objects = list.objects;
      canReview = list.can_review;
    } catch (e) {
      error = e.message;
    }
  }
  load();

  function resetForm() {
    editingId = null;
    category = '';
    schema = null;
    objName = '';
    fieldValues = {};
    extraJson = '';
    formError = '';
    fieldErrors = [];
  }

  async function loadSchema(cat) {
    if (!cat) { schema = null; return; }
    loadingSchema = true;
    try {
      schema = await api(`/rfc/schema/${cat}`);
      const refCats = [...new Set(
        schema.fields.filter((f) => f.ref_category).map((f) => f.ref_category)
      )];
      await Promise.all(refCats.map(async (rc) => {
        if (!refOptions[rc]) {
          try { refOptions[rc] = await getList(rc); }
          catch { refOptions[rc] = []; }
        }
      }));
    } catch (e) {
      formError = e.message;
      schema = null;
    } finally {
      loadingSchema = false;
    }
  }

  async function selectCategory(value) {
    category = value;
    fieldValues = {};
    fieldErrors = [];
    await loadSchema(value);
  }

  function coerce(field, raw) {
    if (raw === undefined || raw === null || raw === '') return undefined;
    switch (field.type) {
      case 'int': return Number(raw);
      case 'bool': return Boolean(raw);
      case 'csv':
      case 'component_set':
        return Array.isArray(raw) ? raw
          : String(raw).split(',').map((s) => s.trim()).filter(Boolean);
      case 'ref_list':
        return Array.isArray(raw) ? raw.filter(Boolean) : [raw].filter(Boolean);
      default: return raw;
    }
  }

  function buildData() {
    const data = {};
    for (const f of schema?.fields ?? []) {
      const v = coerce(f, fieldValues[f.key]);
      if (v !== undefined && !(Array.isArray(v) && v.length === 0)) data[f.key] = v;
    }
    if (extraJson.trim()) {
      const extra = JSON.parse(extraJson);
      if (typeof extra !== 'object' || Array.isArray(extra)) {
        throw new Error('Дополнительные поля должны быть JSON-объектом');
      }
      Object.assign(data, extra);
    }
    return data;
  }

  async function submit() {
    formError = '';
    fieldErrors = [];
    notice = '';
    let data;
    try {
      data = buildData();
    } catch (e) {
      formError = `Дополнительные поля: ${e.message}`;
      return;
    }
    try {
      const body = { category, name: objName, data };
      const result = editingId
        ? await api(`/rfc/objects/${editingId}`, { method: 'PUT', body })
        : await api('/rfc/objects', { method: 'POST', body });
      notice = result.status === 'draft'
        ? 'Сохранено как черновик: есть ссылки на непринятые объекты. Нажмите «Проверить и отправить».'
        : (editingId ? 'Объект обновлён и отправлен на обработку' : 'Объект отправлен на обработку');
      resetForm();
      await load();
    } catch (e) {
      if (e.status === 422 && e.detail && e.detail.field_errors) {
        fieldErrors = e.detail.field_errors;
        formError = 'Исправьте ошибки полей';
      } else {
        formError = e.message;
      }
    }
  }

  function startEdit(obj) {
    viewing = null;
    editingId = obj.id;
    category = obj.category;
    objName = obj.name;
    fieldErrors = [];
    formError = '';
    loadSchema(obj.category).then(() => {
      const known = new Set((schema?.fields ?? []).map((f) => f.key));
      const values = {};
      const extra = {};
      for (const [k, v] of Object.entries(obj.data ?? {})) {
        if (!known.has(k)) { extra[k] = v; continue; }
        const field = schema.fields.find((f) => f.key === k);
        if (field?.type === 'ref') values[k] = typeof v === 'object' ? v.index : v;
        else if (field?.type === 'ref_list') {
          values[k] = (Array.isArray(v) ? v : [v]).map((x) => (typeof x === 'object' ? x.index : x));
        } else if ((field?.type === 'csv' || field?.type === 'component_set') && Array.isArray(v)) {
          values[k] = v.join(', ');
        } else values[k] = v;
      }
      fieldValues = values;
      extraJson = Object.keys(extra).length ? JSON.stringify(extra, null, 2) : '';
    });
    window.scrollTo({ top: 0 });
  }

  async function trySubmit(obj) {
    try {
      const r = await api(`/rfc/objects/${obj.id}/submit`, { method: 'POST' });
      notice = r.message;
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  async function remove(obj) {
    if (!confirm(`Удалить объект «${obj.name}»?`)) return;
    try {
      await api(`/rfc/objects/${obj.id}`, { method: 'DELETE' });
      if (viewing?.id === obj.id) viewing = null;
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  async function accept(obj) {
    viewError = '';
    try {
      await api(`/rfc/objects/${obj.id}/accept`, { method: 'POST' });
      if (viewing?.id === obj.id) viewing = null;
      await load();
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.broken_refs) {
        const list = e.detail.broken_refs.map((r) => `${r.field_label}: ${r.index} (${r.state})`).join('; ');
        viewError = `Ссылки объекта недействительны, приём отменён — ${list}. Объект возвращён в черновики.`;
        await load();
      } else {
        alert(e.message);
      }
    }
  }

  async function reject(obj) {
    const comment = prompt('Комментарий автору (что доработать):', '') ?? '';
    try {
      await api(`/rfc/objects/${obj.id}/reject`, { method: 'POST', body: { comment } });
      if (viewing?.id === obj.id) viewing = null;
      await load();
    } catch (e) {
      alert(e.message);
    }
  }

  function refLabel(v) {
    if (v && typeof v === 'object') return v.name ?? v.index;
    return String(v);
  }

  function schemaFor(cat) {
    return schemaCache[cat]?.fields ?? [];
  }
  async function ensureSchema(cat) {
    if (!schemaCache[cat]) {
      try { schemaCache[cat] = await api(`/rfc/schema/${cat}`); }
      catch { schemaCache[cat] = { fields: [] }; }
    }
  }
  async function openView(obj) {
    viewError = '';
    await ensureSchema(obj.category);
    viewing = obj;
  }

  function viewRows(obj) {
    const sc = schemaFor(obj.category);
    const labels = {};
    const order = [];
    for (const f of sc) { labels[f.key] = f.label; order.push(f.key); }
    const data = obj.data ?? {};
    const keys = [...order.filter((k) => k in data), ...Object.keys(data).filter((k) => !order.includes(k))];
    return keys.map((k) => [labels[k] ?? k, data[k]]);
  }

  // ошибки конкретного поля: сервер возвращает строки с подписью поля
  function fieldError(key) {
    const label = schema?.fields.find((f) => f.key === key)?.label;
    if (!label) return [];
    return fieldErrors.filter((e) => e.includes(label));
  }
</script>

<div class="spread head">
  <h1>DnD RFC — объекты сообщества</h1>
</div>
<p class="muted">
  Опишите класс, вид, предмет, заклинание — любую сущность базы DnD по её
  структуре. Объекты со ссылками на ещё не принятые объекты сохраняются как
  черновик; когда зависимости приняты, нажмите «Проверить и отправить».
  Принятые администратором объекты попадают в общую базу и доступны в редакторе.
</p>

{#if error}<p class="error">{error}</p>{/if}
{#if notice}<p class="notice">{notice}</p>{/if}

<div class="layout">
  <!-- ============ Форма ============ -->
  <div class="panel">
    <h3>{editingId ? 'Доработка объекта' : 'Новый объект'}</h3>

    <label class="field">
      <span>Тип объекта</span>
      <select value={category} onchange={(e) => selectCategory(e.target.value)} disabled={Boolean(editingId)}>
        <option value="">— выберите категорию —</option>
        {#each Object.entries(categories) as [index, description]}
          <option value={index} title={description}>{index}</option>
        {/each}
      </select>
    </label>
    {#if category && categories[category]}
      <p class="muted small">{categories[category]}</p>
    {/if}

    {#if loadingSchema}
      <p class="muted">Загрузка схемы…</p>
    {/if}

    {#if category && schema}
      <label class="field">
        <span>Название</span>
        <input bind:value={objName} placeholder="Например, Клинок тысячи истин" />
      </label>

      {#each schema.fields as f (f.key)}
        <label class="field">
          <span>
            {f.label}{#if f.required}<b class="req">*</b>{/if}
            {#if f.help}<i class="muted hint">— {f.help}</i>{/if}
          </span>

          {#if f.type === 'text'}
            <textarea rows="3" bind:value={fieldValues[f.key]}></textarea>
          {:else if f.type === 'int'}
            <input type="number" min={f.min} max={f.max} bind:value={fieldValues[f.key]} />
          {:else if f.type === 'bool'}
            <input type="checkbox" bind:checked={fieldValues[f.key]} />
          {:else if f.type === 'enum'}
            <select bind:value={fieldValues[f.key]}>
              <option value="">— не выбрано —</option>
              {#each f.options as opt}<option value={opt}>{opt}</option>{/each}
            </select>
          {:else if f.type === 'component_set'}
            <div class="checks-inline">
              {#each f.options as opt}
                <label class="chk">
                  <input
                    type="checkbox"
                    checked={(fieldValues[f.key] ?? []).includes?.(opt) ?? false}
                    onchange={(e) => {
                      const cur = Array.isArray(fieldValues[f.key]) ? [...fieldValues[f.key]] : [];
                      if (e.target.checked) { if (!cur.includes(opt)) cur.push(opt); }
                      else { const i = cur.indexOf(opt); if (i >= 0) cur.splice(i, 1); }
                      fieldValues[f.key] = cur;
                    }}
                  />
                  {opt}
                </label>
              {/each}
            </div>
          {:else if f.type === 'ref'}
            <select bind:value={fieldValues[f.key]}>
              <option value="">— не выбрано —</option>
              {#each refOptions[f.ref_category] ?? [] as opt}
                <option value={opt.index}>{opt.name}{opt.community ? ' (RFC)' : ''}</option>
              {/each}
            </select>
          {:else if f.type === 'ref_list'}
            <div class="ref-list">
              {#each refOptions[f.ref_category] ?? [] as opt}
                <label class="chk">
                  <input
                    type="checkbox"
                    checked={(fieldValues[f.key] ?? []).includes?.(opt.index) ?? false}
                    onchange={(e) => {
                      const cur = Array.isArray(fieldValues[f.key]) ? [...fieldValues[f.key]] : [];
                      if (e.target.checked) { if (!cur.includes(opt.index)) cur.push(opt.index); }
                      else { const i = cur.indexOf(opt.index); if (i >= 0) cur.splice(i, 1); }
                      fieldValues[f.key] = cur;
                    }}
                  />
                  {opt.name}{opt.community ? ' (RFC)' : ''}
                </label>
              {/each}
              {#if (refOptions[f.ref_category] ?? []).length === 0}
                <p class="muted small">Нет доступных объектов категории «{f.ref_category}».</p>
              {/if}
            </div>
          {:else}
            <input bind:value={fieldValues[f.key]} />
          {/if}

          {#each fieldError(f.key) as err}<span class="field-err">{err}</span>{/each}
        </label>
      {/each}

      <label class="field">
        <span>Дополнительные поля API (JSON-объект, необязательно)</span>
        <textarea rows="3" class="mono" bind:value={extraJson} placeholder={'{"custom": 1}'}></textarea>
      </label>

      {#if formError}<p class="error">{formError}</p>{/if}
      <div class="row">
        <button disabled={!objName.trim()} onclick={submit}>
          {editingId ? 'Сохранить' : 'Создать объект'}
        </button>
        {#if editingId}<button class="ghost" onclick={resetForm}>Отмена</button>{/if}
      </div>
    {/if}
  </div>

  <!-- ============ Список объектов ============ -->
  <div class="objects">
    {#if objects.length === 0}
      <p class="muted">Объектов пока нет — станьте первым автором.</p>
    {/if}
    {#each objects as obj (obj.id)}
      <div class="panel obj">
        <div class="spread">
          <b>{obj.name}</b>
          <span class="status {STATUS_CLASS[obj.status]}">{obj.status_label}</span>
        </div>
        <p class="muted small">
          {obj.category} · автор {obj.author_name} ·
          {new Date(obj.updated_at).toLocaleDateString('ru-RU')}
        </p>
        {#if obj.review_comment}
          <p class="review">Комментарий администратора: {obj.review_comment}</p>
        {/if}
        {#if obj.data.description}
          <p class="desc">{String(obj.data.description).slice(0, 160)}{String(obj.data.description).length > 160 ? '…' : ''}</p>
        {/if}
        <div class="row">
          <button class="ghost small" onclick={() => openView(obj)}>Открыть</button>
          {#if obj.is_mine && obj.status === 'draft'}
            <button class="small" onclick={() => trySubmit(obj)}>Проверить и отправить</button>
          {/if}
          {#if obj.is_mine && obj.status !== 'accepted'}
            <button class="small" onclick={() => startEdit(obj)}>
              {obj.status === 'rejected' ? 'Доработать' : 'Редактировать'}
            </button>
          {/if}
          {#if obj.is_mine || canReview}
            <button class="danger small" onclick={() => remove(obj)}>Удалить</button>
          {/if}
          {#if canReview && obj.status === 'pending'}
            <button class="small" onclick={() => accept(obj)}>Принять</button>
            <button class="ghost small" onclick={() => reject(obj)}>Отклонить</button>
          {/if}
        </div>
      </div>
    {/each}
  </div>
</div>

<!-- ============ Просмотр карточки ============ -->
{#if viewing}
  <Modal title={viewing.name} onclose={() => (viewing = null)}>
    <p class="muted view-meta">
      {viewing.category} · автор {viewing.author_name} ·
      <span class="status {STATUS_CLASS[viewing.status]}">{viewing.status_label}</span>
    </p>
    {#if viewing.review_comment}
      <p class="review">Комментарий администратора: {viewing.review_comment}</p>
    {/if}
    {#if viewError}<p class="error">{viewError}</p>{/if}

    <table class="view-table">
      <tbody>
        {#each viewRows(viewing) as [label, value]}
          <tr>
            <td class="key">{label}</td>
            <td>
              {#if Array.isArray(value)}
                {value.map(refLabel).join(', ')}
              {:else if value !== null && typeof value === 'object'}
                <DataTree data={value} />
              {:else}
                <span class="val">{String(value)}</span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>

    <div class="row view-actions">
      {#if viewing.is_mine && viewing.status === 'draft'}
        <button onclick={() => { const o = viewing; viewing = null; trySubmit(o); }}>Проверить и отправить</button>
      {/if}
      {#if viewing.is_mine && viewing.status !== 'accepted'}
        <button onclick={() => startEdit(viewing)}>
          {viewing.status === 'rejected' ? 'Доработать' : 'Редактировать'}
        </button>
      {/if}
      {#if canReview && viewing.status === 'pending'}
        <button onclick={() => accept(viewing)}>Принять</button>
        <button class="ghost" onclick={() => reject(viewing)}>Отклонить</button>
      {/if}
      {#if viewing.is_mine || canReview}
        <button class="danger" onclick={() => remove(viewing)}>Удалить</button>
      {/if}
    </div>
  </Modal>
{/if}

<style>
  .head { margin-bottom: 4px; }
  .small { font-size: 0.85rem; }
  .layout { display: grid; grid-template-columns: minmax(320px, 480px) minmax(0, 1fr); gap: 16px; align-items: start; margin-top: 12px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .objects { display: flex; flex-direction: column; gap: 12px; }
  .obj p { margin: 4px 0; }
  .desc { font-size: 0.9rem; }
  .notice { color: #8fbf9a; }
  .review { color: #e0b273; font-size: 0.9rem; }
  .req { color: #e08573; margin-left: 2px; }
  .hint { font-weight: 400; font-size: 0.8rem; }
  .field-err { color: #e08573; font-size: 0.8rem; margin-top: 2px; }
  .checks-inline, .ref-list { display: flex; flex-wrap: wrap; gap: 4px 14px; }
  .ref-list { flex-direction: column; max-height: 180px; overflow-y: auto; border: 1px solid var(--felt-3); border-radius: var(--radius); padding: 6px 10px; }
  .chk { display: flex; align-items: center; gap: 5px; font-weight: 400; }

  .status {
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em;
    padding: 2px 10px; border-radius: 10px; text-transform: uppercase; white-space: nowrap;
  }
  .status.draft { background: #3a3f4a; color: #cdd4df; }
  .status.pending { background: #4a4632; color: #e6d9a8; }
  .status.accepted { background: #3f6b4f; color: #eaf3ec; }
  .status.rejected { background: #6b3f3f; color: #f3eaea; }

  .view-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .view-table { width: 100%; border-collapse: collapse; margin: 10px 0; }
  .view-table td { padding: 4px 8px 4px 0; vertical-align: top; border-bottom: 1px solid rgba(0,0,0,0.08); }
  .view-table td.key { color: var(--ink-soft); white-space: nowrap; width: 1%; padding-right: 16px; }
  .view-table .val { white-space: pre-wrap; }
  .view-actions { margin-top: 14px; }
</style>
