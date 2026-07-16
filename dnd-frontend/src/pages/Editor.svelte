<script>
  // Редактор персонажа.
  //
  // Справочники загружаются «планом обновления» (кнопка «Обновить
  // справочники»): по текущему состоянию листа собирается список запросов,
  // они кешируются и не повторяются; упавшие запросы показывают текст
  // ошибки и при повторном нажатии уходят в сеть заново.
  //
  // Сохранённое состояние персонажа отображается сразу: у каждого селекта
  // есть резервный option с сохранённым значением, выбор не «сбрасывается».
  import { api } from '../lib/api.js';
  import { navigate } from '../lib/router.svelte.js';
  import {
    getList, getRaw, getClassSpells, getSkillsFull, getFeatsFull,
    FEAT_TYPE_LABELS, featAvailableByLevel,
    subclassAvailable, spellAvailable, maxSpellLevel,
    abilityMod, fmtMod, proficiencyBonus, SKILL_PROFICIENCY_BONUS,
    POINT_BUY_COSTS, POINT_BUY_BUDGET, POINT_BUY_MIN, POINT_BUY_MAX,
    ABILITY_CAP, ASI_LEVELS, ABILITIES,
    ABILITY_KEY_BY_SHORT, skillLabel, collectSkillIndexes,
  } from '../lib/dnd.js';
  import { diffOps } from '../lib/ops.js';
  import InfoButton from '../components/InfoButton.svelte';

  let { charId = null } = $props();

  const zeroBonuses = () => ({
    strength: 0, dexterity: 0, constitution: 0,
    intelligence: 0, wisdom: 0, charisma: 0,
  });
  const emptyData = () => ({
    race: null, subrace: null, class: null, subclass: null, level: 1,
    background: null, alignment: null,
    stats: { strength: 8, dexterity: 8, constitution: 8, intelligence: 8, wisdom: 8, charisma: 8 },
    asi: { taken: [], bonuses: zeroBonuses() },
    background_bonuses: zeroBonuses(),
    hp: { current: 10, max: 10 },
    armor_class: 10, speed: 30, money: 0,
    skills: [], spells: [], feats: [], items: [], notes: '',
  });

  let name = $state('');
  let data = $state(emptyData());
  let original = null; // снимок для диффа при сохранении
  let loading = $state(Boolean(charId));
  let saving = $state(false);
  let error = $state('');
  let savedNote = $state('');

  // Справочники (присваиваются ЯВНО — деструктуризация массива
  // в $state-переменные ненадёжна в Svelte 5)
  let races = $state([]);
  let classes = $state([]);
  let backgrounds = $state([]);
  let alignments = $state([]);
  let skillsFull = $state([]);  // [{index, name, ability}]
  let featsFull = $state([]);   // [{index, name, type, min_level}]
  let equipment = $state([]);

  // Зависимые от выбора данные
  let raceRaw = $state(null);       // вид: подвиды и особенности
  let classRaw = $state(null);      // класс: подклассы, spellcasting, владения, снаряжение
  let backgroundRaw = $state(null); // предыстория: бонусы, черта, владения, снаряжение
  let classSpells = $state(null);
  let spellsError = $state('');
  let spellSearch = $state('');

  // План обновления справочников
  let plan = $state([]);       // [{label, status: 'wait'|'run'|'ok'|'fail', error}]
  let updating = $state(false);

  const isCaster = $derived(Boolean(classRaw?.spellcasting));
  const subraces = $derived(raceRaw?.subraces ?? []);
  const subclasses = $derived(classRaw?.subclasses ?? []);
  const filteredSpells = $derived(
    (classSpells ?? []).filter((s) => s.name.toLowerCase().includes(spellSearch.toLowerCase()))
  );
  const spellLevelByIndex = $derived(
    Object.fromEntries((classSpells ?? []).map((s) => [s.index, s.level]))
  );

  /* ---------- Очки характеристик ---------- */
  const prof = $derived(proficiencyBonus(data.level));
  const spent = $derived(
    ABILITIES.reduce((sum, [key]) => sum + (POINT_BUY_COSTS[data.stats[key]] ?? 0), 0)
  );
  const outOfRange = $derived(
    ABILITIES.some(([key]) => data.stats[key] < POINT_BUY_MIN || data.stats[key] > POINT_BUY_MAX)
  );
  // Улучшения считаются только если уровень персонажа их уже открыл
  const effectiveAsi = $derived(data.asi.taken.filter((l) => l <= data.level));
  const asiBudget = $derived(effectiveAsi.length * 2);
  const asiSpent = $derived(
    ABILITIES.reduce((sum, [key]) => sum + (data.asi.bonuses[key] ?? 0), 0)
  );
  const total = (key) =>
    (data.stats[key] ?? 0) + (data.asi.bonuses[key] ?? 0) + (data.background_bonuses[key] ?? 0);

  /* Бонусы предыстории (2024): 3 очка на три указанные характеристики,
     не больше 2 в одну (+2/+1 или +1/+1/+1). */
  const BG_BUDGET = 3;
  const bgAbilityRefs = $derived(backgroundRaw?.ability_scores ?? []);
  const bgKeys = $derived(bgAbilityRefs.map((r) => ABILITY_KEY_BY_SHORT[r.index]).filter(Boolean));
  const bgSpent = $derived(
    ABILITIES.reduce((sum, [key]) => sum + (data.background_bonuses[key] ?? 0), 0)
  );
  function canIncBg(key) {
    return bgKeys.includes(key) && bgSpent < BG_BUDGET
      && (data.background_bonuses[key] ?? 0) < 2 && total(key) < ABILITY_CAP;
  }
  function incBg(key) { if (canIncBg(key)) data.background_bonuses[key] += 1; }
  function decBg(key) { if (data.background_bonuses[key] > 0) data.background_bonuses[key] -= 1; }

  function canIncBase(key) {
    const v = data.stats[key];
    if (v >= POINT_BUY_MAX) return false;
    const delta = (POINT_BUY_COSTS[v + 1] ?? 99) - (POINT_BUY_COSTS[v] ?? 0);
    return spent + delta <= POINT_BUY_BUDGET;
  }
  function incBase(key) { if (canIncBase(key)) data.stats[key] += 1; }
  function decBase(key) { if (data.stats[key] > POINT_BUY_MIN) data.stats[key] -= 1; }

  function canIncAsi(key) {
    return asiSpent < asiBudget && total(key) < ABILITY_CAP;
  }
  function incAsi(key) { if (canIncAsi(key)) data.asi.bonuses[key] += 1; }
  function decAsi(key) { if (data.asi.bonuses[key] > 0) data.asi.bonuses[key] -= 1; }

  function toggleAsi(level) {
    const i = data.asi.taken.indexOf(level);
    if (i >= 0) data.asi.taken.splice(i, 1);
    else data.asi.taken.push(level);
  }

  /* ---------- Навыки ---------- */
  // Доступность навыков определяется классом и расой: собираем индексы
  // skill-* из владений класса (proficiency_choices) и расы
  // (starting_proficiencies + варианты на выбор).
  const classSkillSet = $derived(
    classRaw ? collectSkillIndexes(classRaw.proficiency_choices) : new Set()
  );
  const raceSkillSet = $derived(
    raceRaw
      ? new Set([
          ...collectSkillIndexes(raceRaw.starting_proficiencies),
          ...collectSkillIndexes(raceRaw.starting_proficiency_options),
        ])
      : new Set()
  );
  const allowedSkills = $derived(new Set([...classSkillSet, ...raceSkillSet]));
  const hasSkillSources = $derived(Boolean(classRaw || raceRaw));
  // Сколько навыков позволяет выбрать класс (сумма choose по спискам с навыками)
  const classChoose = $derived(
    (classRaw?.proficiency_choices ?? []).reduce(
      (n, choice) => n + (collectSkillIndexes(choice).size ? (choice.choose ?? 0) : 0),
      0
    )
  );

  /** Бонус броска по навыку: БМ + модификатор характеристики
   *  + бонус навыка (+2, если навык выбран). */
  function skillBonus(skill) {
    const key = ABILITY_KEY_BY_SHORT[skill.ability] ?? 'strength';
    const picked = data.skills.includes(skill.index);
    return prof + abilityMod(total(key)) + (picked ? SKILL_PROFICIENCY_BONUS : 0);
  }

  /* ---------- План обновления справочников ---------- */
  const nameOf = (list, index) => list.find((i) => i.index === index)?.name ?? index;
  const skillByIndex = (index) => skillsFull.find((s) => s.index === index);

  async function runUpdate() {
    if (updating) return;
    updating = true;
    spellsError = '';
    try {
      const tasks = [];
      const need = (cond, label, fn) => { if (cond) tasks.push({ label, fn }); };

      need(!races.length, 'Получить список рас', async () => { races = await getList('races'); });
      need(!classes.length, 'Получить список классов', async () => { classes = await getList('classes'); });
      need(!backgrounds.length, 'Получить список предысторий', async () => { backgrounds = await getList('backgrounds'); });
      need(!alignments.length, 'Получить список мировоззрений', async () => { alignments = await getList('alignments'); });
      need(!skillsFull.length, 'Получить навыки и их характеристики', async () => { skillsFull = await getSkillsFull(); });
      need(!featsFull.length, 'Получить черты и их требования', async () => { featsFull = await getFeatsFull(); });
      need(!equipment.length, 'Получить список снаряжения', async () => { equipment = await getList('equipment'); });
      need(data.race && !raceRaw, `Получить данные расы «${data.race}»`, async () => { raceRaw = await getRaw('races', data.race); });
      need(data.class && !classRaw, `Получить данные класса «${data.class}»`, async () => { classRaw = await getRaw('classes', data.class); });
      need(data.background && !backgroundRaw, `Получить данные предыстории «${data.background}»`, async () => { backgroundRaw = await getRaw('backgrounds', data.background); });

      plan = tasks.map((t) => ({ label: t.label, status: 'wait', error: '' }));
      for (let i = 0; i < tasks.length; i++) {
        plan[i].status = 'run';
        try {
          await tasks[i].fn();
          plan[i].status = 'ok';
        } catch (e) {
          plan[i].status = 'fail';
          plan[i].error = e.message;
        }
      }

      // Второй этап зависит от результата первого: заклинания есть только
      // у заклинателей, это видно из данных класса
      if (data.class && classRaw?.spellcasting && !classSpells) {
        const step = {
          label: `Получить заклинания класса «${nameOf(classes, data.class)}»`,
          status: 'run', error: '',
        };
        plan = [...plan, step];
        try {
          classSpells = await getClassSpells(data.class);
          step.status = 'ok';
        } catch (e) {
          step.status = 'fail';
          step.error = `${e.message}. Вероятно, не применён backend-patch (см. README).`;
          spellsError = 'Список заклинаний класса недоступен: примените backend-patch (см. README).';
        }
        plan = [...plan];
      }
    } finally {
      updating = false;
    }
  }

  async function init() {
    try {
      if (charId) {
        const char = await api(`/characters/${charId}`);
        name = char.name;
        const base = emptyData();
        data = {
          ...base,
          ...char.data,
          stats: { ...base.stats, ...(char.data.stats ?? {}) },
          asi: {
            taken: char.data.asi?.taken ?? [],
            bonuses: { ...zeroBonuses(), ...(char.data.asi?.bonuses ?? {}) },
          },
          background_bonuses: { ...zeroBonuses(), ...(char.data.background_bonuses ?? {}) },
          hp: { ...base.hp, ...(char.data.hp ?? {}) },
        };
        original = structuredClone($state.snapshot(data));
      }
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
    await runUpdate();
  }
  init();

  function selectRace(raceIndex) {
    data.race = raceIndex || null;
    data.subrace = null;
    raceRaw = null;
    if (data.race) runUpdate();
  }

  function selectClass(classIndex) {
    data.class = classIndex || null;
    data.subclass = null;
    data.spells = []; // заклинания принадлежат классу
    classRaw = null;
    classSpells = null;
    if (data.class) runUpdate();
  }

  function selectBackground(bgIndex) {
    data.background = bgIndex || null;
    backgroundRaw = null;
    data.background_bonuses = zeroBonuses(); // бонусы принадлежат предыстории
    if (data.background) runUpdate();
  }

  /** Добавляет вариант снаряжения (пакет предметов или золото) в инвентарь. */
  function applyEquipmentOption(option) {
    if (option.option_type === 'money') {
      data.money = (Number(data.money) || 0) + (option.count ?? 0);
      return;
    }
    for (const item of option.items ?? [option]) {
      if (item.option_type === 'money') {
        data.money = (Number(data.money) || 0) + (item.count ?? 0);
        continue;
      }
      const ref = item.of ?? {};
      data.items.push({
        name: ref.name ?? ref.index ?? 'предмет',
        qty: item.count ?? 1,
        note: ref.note ?? '',
        ...(String(ref.url ?? '').includes('/equipment/') ? { index: ref.index } : {}),
      });
    }
  }

  function addOriginFeat() {
    const index = backgroundRaw?.feat?.index;
    if (index && !data.feats.includes(index)) data.feats.push(index);
  }

  const featByIndex = (index) => featsFull.find((f) => f.index === index);

  function toggle(list, index) {
    const i = list.indexOf(index);
    if (i >= 0) list.splice(i, 1);
    else list.push(index);
  }

  function addItem() {
    data.items.push({ name: '', qty: 1, note: '' });
  }

  function addEquipment(index) {
    if (!index) return;
    const item = equipment.find((e) => e.index === index);
    data.items.push({ name: item?.name ?? index, qty: 1, note: '', index });
  }

  async function save() {
    error = '';
    savedNote = '';
    saving = true;
    try {
      const snapshot = structuredClone($state.snapshot(data));
      if (charId) {
        const operations = diffOps(original, snapshot);
        if (operations.length) {
          await api(`/characters/${charId}`, { method: 'PATCH', body: { operations } });
          original = snapshot;
        }
        savedNote = operations.length ? 'Изменения сохранены' : 'Изменений нет';
      } else {
        const created = await api('/characters', {
          method: 'POST',
          body: { name: name || 'Безымянный герой', data: snapshot },
        });
        // Навигация перемонтирует редактор уже в режиме правки
        navigate(`/characters/${created.id}`);
      }
    } catch (e) {
      error = e.message;
    } finally {
      saving = false;
    }
  }
</script>

{#if loading}
  <p class="muted">Загрузка листа…</p>
{:else}
  <div class="spread head">
    <h1>{charId ? `Лист: ${name}` : 'Новый персонаж'}</h1>
    <div class="row">
      {#if savedNote}<span class="muted">{savedNote}</span>{/if}
      <button class="ghost" disabled={updating} aria-busy={updating} onclick={runUpdate}>
        {updating ? 'Обновляю…' : 'Обновить справочники'}
      </button>
      <button class="ghost" onclick={() => navigate('/characters')}>К списку</button>
      <button disabled={saving} onclick={save}>{charId ? 'Сохранить изменения' : 'Создать персонажа'}</button>
    </div>
  </div>
  {#if error}<p class="error">{error}</p>{/if}

  {#if plan.length}
    <div class="panel plan mono">
      {#each plan as step}
        <span class="step {step.status}" title={step.error}>
          {#if step.status === 'ok'}✓{:else if step.status === 'fail'}✗{:else if step.status === 'run'}…{:else}·{/if}
          {step.label}
        </span>
      {/each}
    </div>
    {#each plan.filter((s) => s.status === 'fail') as step}
      <p class="error">{step.label} — {step.error}</p>
    {/each}
  {/if}

  <div class="layout">
    <!-- ================= Форма ================= -->
    <div class="form">
      <div class="panel">
        <div class="rule" style="color: var(--gold)">Основное</div>

        <label class="field">
          <span>Имя персонажа</span>
          <input bind:value={name} placeholder="Например, Ойлин из Глубоководья" />
        </label>

        <div class="pair">
          <label class="field">
            <span>Вид (раса) {#if data.race}<InfoButton category="races" index={data.race} />{/if}</span>
            <select value={data.race ?? ''} onchange={(e) => selectRace(e.target.value)}>
              <option value="">— выберите —</option>
              {#if data.race && !races.some((r) => r.index === data.race)}
                <option value={data.race}>{data.race}</option>
              {/if}
              {#each races as r}<option value={r.index}>{r.name}</option>{/each}
            </select>
          </label>

          {#if data.subrace || subraces.length}
            <label class="field">
              <span>Подвид {#if data.subrace}<InfoButton category="subraces" index={data.subrace} />{/if}</span>
              <select bind:value={data.subrace}>
                <option value={null}>— нет —</option>
                {#if data.subrace && !subraces.some((s) => s.index === data.subrace)}
                  <option value={data.subrace}>{data.subrace}</option>
                {/if}
                {#each subraces as s}<option value={s.index}>{s.name}</option>{/each}
              </select>
            </label>
          {/if}
        </div>

        <div class="pair">
          <label class="field">
            <span>Класс {#if data.class}<InfoButton category="classes" index={data.class} />{/if}</span>
            <select value={data.class ?? ''} onchange={(e) => selectClass(e.target.value)}>
              <option value="">— выберите —</option>
              {#if data.class && !classes.some((c) => c.index === data.class)}
                <option value={data.class}>{data.class}</option>
              {/if}
              {#each classes as c}<option value={c.index}>{c.name}</option>{/each}
            </select>
          </label>

          {#if data.subclass || subclasses.length}
            <label class="field">
              <span>
                Подкласс {#if data.subclass}<InfoButton category="subclasses" index={data.subclass} />{/if}
                {#if !subclassAvailable(data.level)}<i class="muted">(откроется с 3 уровня)</i>{/if}
              </span>
              <select bind:value={data.subclass}>
                <option value={null}>— нет —</option>
                {#if data.subclass && !subclasses.some((s) => s.index === data.subclass)}
                  <option value={data.subclass}>{data.subclass}</option>
                {/if}
                {#each subclasses as s}<option value={s.index}>{s.name}</option>{/each}
              </select>
            </label>
          {/if}
        </div>

        <div class="pair">
          <label class="field">
            <span>Уровень</span>
            <input type="number" min="1" max="20" bind:value={data.level} />
          </label>
          <label class="field">
            <span>Предыстория {#if data.background}<InfoButton category="backgrounds" index={data.background} />{/if}</span>
            <select value={data.background ?? ''} onchange={(e) => selectBackground(e.target.value)}>
              <option value="">— нет —</option>
              {#if data.background && !backgrounds.some((b) => b.index === data.background)}
                <option value={data.background}>{data.background}</option>
              {/if}
              {#each backgrounds as b}<option value={b.index}>{b.name}</option>{/each}
            </select>
          </label>
          <label class="field">
            <span>Мировоззрение {#if data.alignment}<InfoButton category="alignments" index={data.alignment} />{/if}</span>
            <select bind:value={data.alignment}>
              <option value={null}>— нет —</option>
              {#if data.alignment && !alignments.some((a) => a.index === data.alignment)}
                <option value={data.alignment}>{data.alignment}</option>
              {/if}
              {#each alignments as a}<option value={a.index}>{a.name}</option>{/each}
            </select>
          </label>
        </div>
      </div>

      {#if data.background && backgroundRaw}
        <div class="panel">
          <div class="rule" style="color: var(--gold)">Предыстория · {backgroundRaw.name}</div>

          {#if bgAbilityRefs.length}
            <p class="muted">
              Предыстория даёт 3 очка к характеристикам {bgAbilityRefs.map((r) => r.name).join(', ')}
              (+2/+1 или +1/+1/+1, распределено {bgSpent} / {BG_BUDGET}):
            </p>
            <div class="row" style="gap: 6px 20px">
              {#each bgAbilityRefs as ref}
                {@const key = ABILITY_KEY_BY_SHORT[ref.index]}
                <span class="mono">
                  {ref.name}
                  <button class="ghost small" onclick={() => decBg(key)} disabled={!data.background_bonuses[key]}>−</button>
                  <span class="val">+{data.background_bonuses[key] ?? 0}</span>
                  <button class="ghost small" onclick={() => incBg(key)} disabled={!canIncBg(key)}>+</button>
                </span>
              {/each}
            </div>
          {/if}

          {#if backgroundRaw.feat}
            <p style="margin-bottom: 4px">
              Черта происхождения: <b>{backgroundRaw.feat.name}</b>
              {#if backgroundRaw.feat.note}({backgroundRaw.feat.note}){/if}
              {#if backgroundRaw.feat.index}<InfoButton category="feats" index={backgroundRaw.feat.index} />{/if}
              <button class="ghost small" onclick={addOriginFeat}
                disabled={!backgroundRaw.feat.index || data.feats.includes(backgroundRaw.feat.index)}>
                {data.feats.includes(backgroundRaw.feat?.index) ? 'В чертах' : 'Добавить в черты'}
              </button>
            </p>
          {/if}

          {#if backgroundRaw.proficiencies?.length}
            <p class="muted">Владения: {backgroundRaw.proficiencies.map((p) => p.name).join(', ')}</p>
          {/if}

          {#each backgroundRaw.equipment_options ?? [] as choice}
            <p class="muted" style="margin-bottom: 4px">{choice.desc}</p>
            <div class="row">
              {#each choice.from?.options ?? [] as option, oi}
                <button class="ghost small" onclick={() => applyEquipmentOption(option)}>
                  {option.option_type === 'money'
                    ? `Взять ${option.count} ${(option.unit ?? 'gp').toUpperCase()}`
                    : `Добавить пакет ${String.fromCharCode(65 + oi)} в предметы`}
                </button>
              {/each}
            </div>
          {/each}
        </div>
      {/if}

      <div class="panel">
        <div class="rule" style="color: var(--gold)">Очки и характеристики</div>
        <p class="muted">
          Стартовый набор — {POINT_BUY_BUDGET} очков, значения от {POINT_BUY_MIN} до {POINT_BUY_MAX}
          (9-е и 10-е очко значения стоят дороже). Улучшения характеристик открываются на уровнях
          {ASI_LEVELS.join(', ')} и дают по 2 дополнительных очка сверх стартовых.
        </p>

        <div class="budget mono">
          <span class:overspent={spent > POINT_BUY_BUDGET}>Стартовые очки: {spent} / {POINT_BUY_BUDGET}</span>
          <span class:overspent={asiSpent > asiBudget}>Очки улучшений: {asiSpent} / {asiBudget}</span>
          {#if data.background}
            <span class:overspent={bgSpent > BG_BUDGET}>Очки предыстории: {bgSpent} / {BG_BUDGET}</span>
          {/if}
          <span>Бонус мастерства: {fmtMod(prof)} <i class="muted">(растёт на 5, 9, 13, 17 уровнях)</i></span>
        </div>
        {#if outOfRange}
          <p class="error">Часть значений вне диапазона {POINT_BUY_MIN}–{POINT_BUY_MAX} (лист создан до введения очков) — уменьшите их кнопкой «−».</p>
        {/if}

        <div class="asi-row">
          <span class="muted">Улучшения:</span>
          {#each ASI_LEVELS as lvl}
            <label class="row asi" class:locked={data.asi.taken.includes(lvl) && lvl > data.level}>
              <input
                type="checkbox"
                checked={data.asi.taken.includes(lvl)}
                disabled={lvl > data.level && !data.asi.taken.includes(lvl)}
                onchange={() => toggleAsi(lvl)}
              />
              <span>ур. {lvl}</span>
            </label>
          {/each}
        </div>

        <table class="abilities">
          <thead>
            <tr>
              <th></th><th>Старт</th><th>Улучшения</th><th>Предыстория</th><th>Итог</th><th>Бонус к броскам</th>
            </tr>
          </thead>
          <tbody>
            {#each ABILITIES as [key, label]}
              <tr>
                <td class="ab-name">{label}</td>
                <td class="mono">
                  <button class="ghost small" onclick={() => decBase(key)} disabled={data.stats[key] <= POINT_BUY_MIN}>−</button>
                  <span class="val">{data.stats[key]}</span>
                  <button class="ghost small" onclick={() => incBase(key)} disabled={!canIncBase(key)}>+</button>
                </td>
                <td class="mono">
                  <button class="ghost small" onclick={() => decAsi(key)} disabled={!data.asi.bonuses[key]}>−</button>
                  <span class="val">+{data.asi.bonuses[key]}</span>
                  <button class="ghost small" onclick={() => incAsi(key)} disabled={!canIncAsi(key)}>+</button>
                </td>
                <td class="mono">{data.background_bonuses[key] ? `+${data.background_bonuses[key]}` : '—'}</td>
                <td class="mono total-cell">{total(key)}</td>
                <td class="mono mod-cell">{fmtMod(abilityMod(total(key)))}</td>
              </tr>
            {/each}
          </tbody>
        </table>

        <div class="pair" style="margin-top: 12px">
          <label class="field"><span>Хиты (текущие)</span><input type="number" min="0" bind:value={data.hp.current} /></label>
          <label class="field"><span>Хиты (максимум)</span><input type="number" min="1" bind:value={data.hp.max} /></label>
          <label class="field"><span>Класс доспеха</span><input type="number" min="0" bind:value={data.armor_class} /></label>
          <label class="field"><span>Скорость</span><input type="number" min="0" bind:value={data.speed} /></label>
          <label class="field"><span>Золото</span><input type="number" min="0" bind:value={data.money} /></label>
        </div>
      </div>

      <div class="panel">
        <div class="rule" style="color: var(--gold)">Навыки</div>
        {#if !skillsFull.length}
          <p class="muted">Навыки не загружены — нажмите «Обновить справочники».</p>
        {:else if !hasSkillSources}
          <p class="muted">Выберите класс и расу — от них зависит, какие навыки доступны для выбора.</p>
        {:else}
          <p class="muted">
            Доступность определяется классом и расой{#if classChoose}
              , класс позволяет выбрать {classChoose} из отмеченных доступными{/if}.
            Выбранный навык даёт +{SKILL_PROFICIENCY_BONUS} к броскам по нему.
          </p>
        {/if}
        <div class="checks">
          {#each skillsFull as skill}
            {@const picked = data.skills.includes(skill.index)}
            {@const allowed = allowedSkills.has(skill.index)}
            <label class="row" class:dimmed={hasSkillSources && !allowed && !picked}>
              <input
                type="checkbox"
                checked={picked}
                disabled={!picked && (!hasSkillSources || !allowed)}
                onchange={() => toggle(data.skills, skill.index)}
              />
              <span class:locked={picked && hasSkillSources && !allowed}>
                {skillLabel(skill)}
                <b class="mono bonus">{fmtMod(skillBonus(skill))}</b>
              </span>
              <InfoButton category="skills" index={skill.index} />
            </label>
          {/each}
        </div>
      </div>

      {#if isCaster || data.spells.length}
        <!-- Секция появляется только у заклинателей: список содержит
             только заклинания выбранного класса -->
        <div class="panel">
          <div class="rule" style="color: var(--gold)">Заклинания{#if data.class} · {nameOf(classes, data.class)}{/if}</div>
          {#if spellsError}
            <p class="error">{spellsError}</p>
          {:else if classSpells === null}
            <p class="muted">Заклинания не загружены — нажмите «Обновить справочники».</p>
          {:else}
            <p class="muted">
              Доступны круги до {maxSpellLevel(data.level)}-го. Заклинания старших кругов можно
              выбрать — в итоговой панели они будут серыми, пока не хватает уровня.
            </p>
            <label class="field"><span>Поиск</span><input bind:value={spellSearch} placeholder="fire..." /></label>
            <div class="checks tall">
              {#each filteredSpells as spell}
                <label class="row">
                  <input
                    type="checkbox"
                    checked={data.spells.includes(spell.index)}
                    onchange={() => toggle(data.spells, spell.index)}
                  />
                  <span>{spell.name} <i class="muted">· круг {spell.level}</i></span>
                  <InfoButton category="spells" index={spell.index} />
                </label>
              {/each}
            </div>
          {/if}
        </div>
      {/if}

      <div class="panel">
        <div class="rule" style="color: var(--gold)">Черты</div>
        {#if !featsFull.length}
          <p class="muted">Список черт не загружен — нажмите «Обновить справочники».</p>
        {:else}
          <p class="muted">
            Требования уровня берутся из базы: общие черты — с 4 уровня, эпические дары — с 19-го.
            Недоступные можно выбрать — в итоговой панели они будут серыми.
          </p>
        {/if}
        {#each Object.entries(FEAT_TYPE_LABELS) as [type, label]}
          {@const group = featsFull.filter((f) => (f.type ?? 'general') === type)}
          {#if group.length}
            <div class="feat-group">{label}{#if group[0].min_level} <i class="muted">· с {group[0].min_level} уровня</i>{/if}</div>
            <div class="checks">
              {#each group as feat}
                <label class="row">
                  <input
                    type="checkbox"
                    checked={data.feats.includes(feat.index)}
                    onchange={() => toggle(data.feats, feat.index)}
                  />
                  <span class:locked={data.feats.includes(feat.index) && !featAvailableByLevel(feat, data.level)}>
                    {feat.name}{#if feat.community} <span class="badge">RFC</span>{/if}
                  </span>
                  <InfoButton category="feats" index={feat.index} />
                </label>
              {/each}
            </div>
          {/if}
        {/each}
      </div>

      <div class="panel">
        <div class="rule" style="color: var(--gold)">Предметы с собой</div>
        {#each data.items as item, i}
          <div class="item-row">
            <input placeholder="Название" bind:value={item.name} />
            <input type="number" min="1" bind:value={item.qty} title="Количество" />
            <input placeholder="Заметка" bind:value={item.note} />
            {#if item.index}<InfoButton category="equipment" index={item.index} />{/if}
            <button class="ghost small" onclick={() => data.items.splice(i, 1)}>×</button>
          </div>
        {/each}
        {#if classRaw?.starting_equipment_options?.length}
          <div class="rule" style="color: var(--gold)">Стартовое снаряжение класса</div>
          {#each classRaw.starting_equipment_options as choice}
            <p class="muted" style="margin-bottom: 4px">{choice.desc}</p>
            <div class="row">
              {#each choice.from?.options ?? [] as option, oi}
                <button class="ghost small" onclick={() => applyEquipmentOption(option)}>
                  {option.option_type === 'money'
                    ? `Взять ${option.count} ${(option.unit ?? 'gp').toUpperCase()}`
                    : `Добавить пакет ${String.fromCharCode(65 + oi)} в предметы`}
                </button>
              {/each}
            </div>
          {/each}
        {/if}
        <div class="row" style="margin-top: 8px">
          <button class="ghost small" onclick={addItem}>Добавить предмет</button>
          <select style="width: auto" onchange={(e) => { addEquipment(e.target.value); e.target.value = ''; }}>
            <option value="">…или из базы снаряжения</option>
            {#each equipment as eq}<option value={eq.index}>{eq.name}</option>{/each}
          </select>
        </div>
      </div>

      <div class="panel">
        <div class="rule" style="color: var(--gold)">Заметки</div>
        <textarea rows="4" bind:value={data.notes} placeholder="История, цели, связи…"></textarea>
      </div>
    </div>

    <!-- ================= Итоговая панель — бумажный лист ================= -->
    <aside class="paper summary">
      <h2>{name || 'Безымянный герой'}</h2>
      <p class="muted">
        {[data.race && nameOf(races, data.race),
          data.subrace && nameOf(subraces, data.subrace),
          data.class && nameOf(classes, data.class),
          `уровень ${data.level}`].filter(Boolean).join(' · ')}
      </p>

      <div class="rule">Характеристики</div>
      <div class="sum-stats mono">
        {#each ABILITIES as [key, label]}
          <div class="stat">
            <span>{label.slice(0, 3)}</span>
            <b>{total(key)}</b>
            <i>{fmtMod(abilityMod(total(key)))}</i>
          </div>
        {/each}
      </div>
      <p class="mono">
        Мастерство {fmtMod(prof)} · Хиты {data.hp.current}/{data.hp.max} · КД {data.armor_class} ·
        Скорость {data.speed} · Золото {data.money}
      </p>

      {#if data.subclass}
        <div class="rule">Подкласс</div>
        <div class="entry" class:locked={!subclassAvailable(data.level)}>
          {nameOf(subclasses, data.subclass)}
          <InfoButton category="subclasses" index={data.subclass} disabled={!subclassAvailable(data.level)} />
          {#if !subclassAvailable(data.level)}<span class="why">с 3 уровня</span>{/if}
        </div>
      {/if}

      {#if data.background || data.alignment}
        <div class="rule">Происхождение</div>
        {#if data.background}
          <div class="entry">{nameOf(backgrounds, data.background)} <InfoButton category="backgrounds" index={data.background} /></div>
        {/if}
        {#if data.alignment}
          <div class="entry">{nameOf(alignments, data.alignment)} <InfoButton category="alignments" index={data.alignment} /></div>
        {/if}
      {/if}

      {#if data.skills.length}
        <div class="rule">Навыки</div>
        {#each data.skills as index}
          {@const skill = skillByIndex(index)}
          {@const allowed = !hasSkillSources || allowedSkills.has(index)}
          <div class="entry" class:locked={!allowed}>
            {#if skill}
              {skillLabel(skill)} <b class="mono">{fmtMod(skillBonus(skill))}</b>
            {:else}
              {index}
            {/if}
            <InfoButton category="skills" index={index} disabled={!allowed} />
            {#if !allowed}<span class="why">недоступен классу/расе</span>{/if}
          </div>
        {/each}
      {/if}

      {#if data.spells.length}
        <div class="rule">Заклинания</div>
        {#each data.spells as index}
          {@const level = spellLevelByIndex[index]}
          {@const ok = level === undefined || spellAvailable(level, data.level)}
          <div class="entry" class:locked={!ok}>
            {nameOf(classSpells ?? [], index)}
            {#if level !== undefined}<i class="muted">· круг {level}</i>{/if}
            <InfoButton category="spells" index={index} disabled={!ok} />
            {#if !ok}<span class="why">не хватает уровня</span>{/if}
          </div>
        {/each}
      {/if}

      {#if data.feats.length}
        <div class="rule">Черты</div>
        {#each data.feats as index}
          {@const feat = featByIndex(index)}
          {@const ok = featAvailableByLevel(feat, data.level)}
          <div class="entry" class:locked={!ok}>
            {feat?.name ?? index}
            <InfoButton category="feats" index={index} disabled={!ok} />
            {#if !ok}<span class="why">с {feat.min_level} уровня</span>{/if}
          </div>
        {/each}
      {/if}

      {#if data.items.length}
        <div class="rule">Снаряжение</div>
        {#each data.items as item}
          <div class="entry">
            {item.name || '—'}{#if item.qty > 1} ×{item.qty}{/if}
            {#if item.note}<i class="muted"> — {item.note}</i>{/if}
            {#if item.index}<InfoButton category="equipment" index={item.index} />{/if}
          </div>
        {/each}
      {/if}

      {#if data.notes}
        <div class="rule">Заметки</div>
        <p class="notes">{data.notes}</p>
      {/if}
    </aside>
  </div>
{/if}

<style>
  .head { margin-bottom: 12px; }
  .plan { display: flex; gap: 6px 18px; flex-wrap: wrap; font-size: 0.78rem; margin-bottom: 16px; padding: 8px 12px; }
  .step { color: var(--text-dim); }
  .step.ok { color: #8fbf9a; }
  .step.run { color: var(--gold); }
  .step.fail { color: #e08573; }

  .layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 420px); gap: 20px; align-items: start; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .form { display: flex; flex-direction: column; gap: 16px; }
  .pair { display: flex; gap: 14px; flex-wrap: wrap; }
  .pair .field { flex: 1; min-width: 160px; }

  .budget { display: flex; gap: 8px 22px; flex-wrap: wrap; margin-bottom: 10px; font-size: 0.85rem; }
  .overspent { color: #e08573; font-weight: 700; }
  .asi-row { display: flex; gap: 6px 14px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
  .asi-row .asi { gap: 4px; }

  table.abilities { border-collapse: collapse; width: 100%; }
  table.abilities th {
    text-align: left; font-size: 0.72rem; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--text-dim); font-weight: 500; padding: 2px 8px;
  }
  table.abilities td { padding: 3px 8px; border-top: 1px solid var(--felt-3); white-space: nowrap; }
  .ab-name { font-weight: 700; }
  .val { display: inline-block; min-width: 26px; text-align: center; }
  .total-cell { font-weight: 700; }
  .mod-cell { color: var(--gold); font-weight: 700; }

  .feat-group {
    font-family: var(--font-display); font-weight: 700; font-size: 0.85rem;
    letter-spacing: 0.05em; margin: 10px 0 4px; color: var(--text-dim);
  }
  .checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 4px 14px; }
  .checks.tall { max-height: 320px; overflow-y: auto; }
  .checks .dimmed { opacity: 0.45; }
  .bonus { color: var(--gold); margin-left: 4px; }
  .item-row { display: grid; grid-template-columns: 2fr 70px 2fr auto auto; gap: 8px; align-items: center; margin-bottom: 6px; }

  .summary { position: sticky; top: 16px; }
  .sum-stats { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
  .sum-stats .stat {
    display: flex; flex-direction: column; align-items: center;
    border: 1px solid #cabfa4; border-radius: 4px; padding: 3px 8px;
  }
  .sum-stats .stat span { font-size: 0.62rem; text-transform: uppercase; color: var(--ink-soft); }
  .sum-stats .stat i { font-style: normal; font-size: 0.72rem; color: var(--gold-dim); }
  .entry { padding: 2px 0; }
  .why { font-size: 0.72rem; color: inherit; border: 1px solid currentColor; border-radius: 8px; padding: 0 6px; margin-left: 6px; }
  .notes { white-space: pre-wrap; }
</style>
