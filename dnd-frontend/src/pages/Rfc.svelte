<script>
  // DnD RFC: объекты сообщества. Любой пользователь описывает объект любой
  // категории базы DnD, объект висит «На обработке», пока администратор
  // сервера не примет его (тогда он попадает в community-кеш и в списки
  // редактора) или не отклонит с возможностью доработки.
  import { api } from '../lib/api.js';
  import Modal from '../components/Modal.svelte';
  import DataTree from '../components/DataTree.svelte';

  let categories = $state({});   // {index: описание} из backend
  let objects = $state([]);
  let canReview = $state(false);
  let error = $state('');
  let notice = $state('');

  // Форма
  let editingId = $state(null); // null = создание нового
  let category = $state('');
  let objName = $state('');
  let fieldValues = $state({});
  let extraJson = $state('');
  let formError = $state('');

  // Просмотр карточки
  let viewing = $state(null);

  /* Поля описания по категориям (типы: text | number | textarea | csv).
     Для категорий без шаблона — базовые name+description + JSON. */
  const FIELD_TEMPLATES = {
    spells: [
      ['level', 'Круг (0 — заговор)', 'number'],
      ['school', 'Школа магии', 'text'],
      ['casting_time', 'Время накладывания', 'text'],
      ['range', 'Дистанция', 'text'],
      ['components', 'Компоненты (В, С, М — через запятую)', 'csv'],
      ['duration', 'Длительность', 'text'],
      ['classes', 'Классы (через запятую)', 'csv'],
      ['description', 'Описание', 'textarea'],
      ['higher_level', 'На больших кругах', 'textarea'],
    ],
    classes: [
      ['hit_die', 'Кость хитов (число, напр. 10)', 'number'],
      ['primary_ability', 'Основная характеристика', 'text'],
      ['saving_throws', 'Спасброски (через запятую)', 'csv'],
      ['proficiencies', 'Владения (через запятую)', 'csv'],
      ['description', 'Описание', 'textarea'],
    ],
    species: [
      ['type', 'Тип существа (напр. Humanoid)', 'text'],
      ['size', 'Размер', 'text'],
      ['speed', 'Скорость', 'number'],
      ['traits', 'Особенности (через запятую)', 'csv'],
      ['description', 'Описание', 'textarea'],
    ],
    subspecies: [
      ['species', 'Вид-родитель', 'text'],
      ['damage_type', 'Тип урона (если есть)', 'text'],
      ['traits', 'Особенности (через запятую)', 'csv'],
      ['description', 'Описание', 'textarea'],
    ],
    backgrounds: [
      ['ability_scores', 'Три характеристики (напр. INT, WIS, CHA)', 'csv'],
      ['origin_feat', 'Черта происхождения', 'text'],
      ['proficiencies', 'Владения: 2 навыка + инструмент (через запятую)', 'csv'],
      ['equipment', 'Снаряжение: пакет или золото', 'textarea'],
      ['description', 'Описание', 'textarea'],
    ],
    feats: [
      ['type', 'Тип: origin / general / fighting-style / epic-boon', 'text'],
      ['minimum_level', 'Минимальный уровень (пусто, если нет)', 'number'],
      ['repeatable', 'Можно ли брать повторно (текст условия)', 'text'],
      ['description', 'Описание', 'textarea'],
    ],
    equipment: [
      ['category', 'Категория снаряжения', 'text'],
      ['cost', 'Стоимость (напр. 15 gp)', 'text'],
      ['weight', 'Вес', 'number'],
      ['damage', 'Урон (напр. 1d8 slashing)', 'text'],
      ['mastery', 'Свойство мастерства (2024)', 'text'],
      ['properties', 'Свойства (через запятую)', 'csv'],
      ['description', 'Описание', 'textarea'],
    ],
    'magic-items': [
      ['category', 'Категория', 'text'],
      ['rarity', 'Редкость', 'text'],
      ['description', 'Описание', 'textarea'],
    ],
    skills: [
      ['ability', 'Характеристика (STR/DEX/CON/INT/WIS/CHA)', 'text'],
      ['description', 'Описание', 'textarea'],
    ],
    traits: [
      ['species', 'Вид (через запятую, если несколько)', 'csv'],
      ['description', 'Описание', 'textarea'],
    ],
    poisons: [
      ['poison_type', 'Тип (contact / ingested / inhaled / injury)', 'text'],
      ['price', 'Цена', 'text'],
      ['description', 'Описание', 'textarea'],
    ],
  };
  const GENERIC_TEMPLATE = [['description', 'Описание', 'textarea']];

  const template = $derived(FIELD_TEMPLATES[category] ?? (category ? GENERIC_TEMPLATE : []));

  const STATUS_CLASS = { pending: 'pending', accepted: 'accepted', rejected: 'rejected' };

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
    objName = '';
    fieldValues = {};
    extraJson = '';
    formError = '';
  }

  function selectCategory(value) {
    category = value;
    fieldValues = {};
  }

  function buildData() {
    const data = {};
    for (const [key, , type] of template) {
      const raw = fieldValues[key];
      if (raw === undefined || raw === null || String(raw).trim() === '') continue;
      if (type === 'number') data[key] = Number(raw);
      else if (type === 'csv') data[key] = String(raw).split(',').map((s) => s.trim()).filter(Boolean);
      else data[key] = raw;
    }
    if (extraJson.trim()) {
      const extra = JSON.parse(extraJson); // ошибки ловим выше
      if (typeof extra !== 'object' || Array.isArray(extra)) {
        throw new Error('Дополнительные поля должны быть JSON-объектом');
      }
      Object.assign(data, extra);
    }
    return data;
  }

  async function submit() {
    formError = '';
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
      if (editingId) {
        await api(`/rfc/objects/${editingId}`, { method: 'PUT', body });
        notice = 'Объект обновлён и снова отправлен на обработку';
      } else {
        await api('/rfc/objects', { method: 'POST', body });
        notice = 'Объект отправлен на обработку';
      }
      resetForm();
      await load();
    } catch (e) {
      formError = e.message;
    }
  }

  function startEdit(obj) {
    viewing = null;
    editingId = obj.id;
    category = obj.category;
    objName = obj.name;
    const known = new Set((FIELD_TEMPLATES[obj.category] ?? GENERIC_TEMPLATE).map(([k]) => k));
    const values = {};
    const extra = {};
    for (const [key, value] of Object.entries(obj.data ?? {})) {
      if (known.has(key)) values[key] = Array.isArray(value) ? value.join(', ') : value;
      else extra[key] = value;
    }
    fieldValues = values;
    extraJson = Object.keys(extra).length ? JSON.stringify(extra, null, 2) : '';
    window.scrollTo({ top: 0 });
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
    try {
      await api(`/rfc/objects/${obj.id}/accept`, { method: 'POST' });
      if (viewing?.id === obj.id) viewing = null;
      await load();
    } catch (e) {
      alert(e.message);
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

  // Поля объекта для просмотра: подписи из шаблона категории, затем прочее
  function viewRows(obj) {
    const tmpl = FIELD_TEMPLATES[obj.category] ?? GENERIC_TEMPLATE;
    const labels = Object.fromEntries(tmpl.map(([k, label]) => [k, label]));
    const order = tmpl.map(([k]) => k);
    const data = obj.data ?? {};
    const keys = [...order.filter((k) => k in data), ...Object.keys(data).filter((k) => !order.includes(k))];
    return keys.map((k) => [labels[k] ?? k, data[k]]);
  }
</script>

<div class="spread head">
  <h1>DnD RFC — объекты сообщества</h1>
</div>
<p class="muted">
  Опишите свой класс, вид, предмет, заклинание — любую сущность базы DnD.
  Объект будет «На обработке», пока администратор сервера не примет его:
  принятые объекты попадают в общую базу и появляются в списках редактора персонажей.
</p>

{#if error}<p class="error">{error}</p>{/if}
{#if notice}<p class="muted">{notice}</p>{/if}

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
      <p class="muted">{categories[category]}</p>
    {/if}

    {#if category}
      <label class="field">
        <span>Название</span>
        <input bind:value={objName} placeholder="Например, Клинок тысячи истин" />
      </label>

      {#each template as [key, label, type]}
        <label class="field">
          <span>{label}</span>
          {#if type === 'textarea'}
            <textarea rows="3" bind:value={fieldValues[key]}></textarea>
          {:else if type === 'number'}
            <input type="number" bind:value={fieldValues[key]} />
          {:else}
            <input bind:value={fieldValues[key]} />
          {/if}
        </label>
      {/each}

      <label class="field">
        <span>Дополнительные поля API (JSON-объект, необязательно)</span>
        <textarea rows="3" class="mono" bind:value={extraJson} placeholder={'{"weight": 3}'}></textarea>
      </label>

      {#if formError}<p class="error">{formError}</p>{/if}
      <div class="row">
        <button disabled={!objName.trim()} onclick={submit}>
          {editingId ? 'Отправить доработку' : 'Отправить на обработку'}
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
        <p class="muted">
          {obj.category} · автор {obj.author_name} ·
          {new Date(obj.updated_at).toLocaleDateString('ru-RU')}
        </p>
        {#if obj.review_comment}
          <p class="review">Комментарий администратора: {obj.review_comment}</p>
        {/if}
        {#if obj.data.description}
          <p class="desc">{String(obj.data.description).slice(0, 200)}{String(obj.data.description).length > 200 ? '…' : ''}</p>
        {/if}
        <div class="row">
          <button class="ghost small" onclick={() => (viewing = obj)}>Открыть</button>
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

    <table class="view-table">
      <tbody>
        {#each viewRows(viewing) as [label, value]}
          <tr>
            <td class="key">{label}</td>
            <td>
              {#if value !== null && typeof value === 'object'}
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
  .layout { display: grid; grid-template-columns: minmax(300px, 460px) minmax(0, 1fr); gap: 16px; align-items: start; margin-top: 12px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .objects { display: flex; flex-direction: column; gap: 12px; }
  .obj p { margin: 4px 0; }
  .desc { font-size: 0.9rem; }
  .review { color: #e0b273; font-size: 0.9rem; }
  .status {
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em;
    padding: 2px 10px; border-radius: 10px; text-transform: uppercase;
  }
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
