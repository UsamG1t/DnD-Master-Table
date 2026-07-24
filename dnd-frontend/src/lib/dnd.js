// Данные DnD из backend + правила доступности по уровню + подписи.
import { api } from './api.js';

const listCache = new Map();
const infoCache = new Map();
const rawCache = new Map();

/** Кеш промисов. Упавший промис ВЫСЕЛЯЕТСЯ из кеша, чтобы повторная
 *  попытка (кнопка «Обновить справочники») снова ушла в сеть, а не
 *  вернула закешированную ошибку. */
function cached(map, key, factory) {
  if (!map.has(key)) {
    const promise = factory().catch((e) => {
      map.delete(key);
      throw e;
    });
    map.set(key, promise);
  }
  return map.get(key);
}

/** Список записей категории: [{index, name}] — для всплывающих списков. */
export function getList(category) {
  return cached(listCache, category, () => api(`/dnd/${category}`));
}

/** Сжатое описание для кнопки info (+ source_url). */
export function getInfo(category, index) {
  return cached(infoCache, `${category}/${index}`, () => api(`/dnd/${category}/${index}`));
}

/** Полный ответ базы-источника (для зависимых списков). */
export function getRaw(category, index) {
  return cached(rawCache, `${category}/${index}`, () => api(`/dnd/${category}/${index}/raw`));
}

/** Заклинания, доступные классу: [{index, name, level}].
 *  Требует эндпоинт /dnd/classes/{index}/spells (backend-patch). */
export function getClassSpells(classIndex) {
  return cached(rawCache, `class-spells/${classIndex}`, () =>
    api(`/dnd/classes/${classIndex}/spells`)
  );
}

/** Все навыки с их характеристиками: [{index, name, ability}], где
 *  ability — короткий индекс ('str'|'dex'|...). Собирается из списка
 *  навыков и их raw-описаний; всё кешируется. */
export function getSkillsFull() {
  return cached(rawCache, 'skills-full', async () => {
    const list = await getList('skills');
    const raws = await Promise.all(list.map((s) => getRaw('skills', s.index)));
    return list.map((s, i) => ({
      index: s.index,
      name: s.name,
      ability: raws[i]?.ability_score?.index ?? 'str',
    }));
  });
}

/** Все черты с типом и требованиями: [{index, name, type, min_level, feature_named}].
 *  Типы 2024: origin | general (с 4 ур.) | fighting-style | epic-boon (с 19 ур.). */
export function getFeatsFull() {
  return cached(rawCache, 'feats-full', async () => {
    const list = await getList('feats');
    const raws = await Promise.all(list.map((f) => getRaw('feats', f.index)));
    return list.map((f, i) => ({
      index: f.index,
      name: f.name,
      community: f.community ?? false,
      type: raws[i]?.type ?? null,
      min_level: raws[i]?.prerequisites?.minimum_level ?? null,
      feature_named: raws[i]?.prerequisites?.feature_named ?? null,
    }));
  });
}

export const FEAT_TYPE_LABELS = {
  origin: 'Черты происхождения',
  general: 'Общие черты',
  'fighting-style': 'Боевые стили',
  'epic-boon': 'Эпические дары',
};

/** Доступность черты по её собственному требованию минимального уровня. */
export function featAvailableByLevel(feat, charLevel) {
  return !feat?.min_level || charLevel >= feat.min_level;
}

/* ---------- Правила доступности по уровню ----------
 * Логика та же для веба и будущего Android-клиента: недоступное можно
 * выбрать, но в итоговой панели оно бледно-серое и не кликабельное. */

/** Черты открываются с 4 уровня (первое повышение характеристик). */
export function featAvailable(level) {
  return level >= 4;
}

/** Подклассы (архетипы) в среднем открываются с 3 уровня. */
export function subclassAvailable(level) {
  return level >= 3;
}

/** Максимальный круг заклинаний для полного заклинателя. */
export function maxSpellLevel(level) {
  return Math.min(9, Math.ceil(level / 2));
}

export function spellAvailable(spellLevel, charLevel) {
  return spellLevel <= maxSpellLevel(charLevel);
}

/* ---------- Характеристики: модификаторы, мастерство, point buy ---------- */

/** Модификатор характеристики: floor((значение − 10) / 2). */
export function abilityMod(value) {
  return Math.floor((value - 10) / 2);
}

export function fmtMod(mod) {
  return mod >= 0 ? `+${mod}` : `−${Math.abs(mod)}`;
}

/** Бонус мастерства по уровню (PHB): +2 на 1–4, растёт на 5, 9, 13, 17. */
export function proficiencyBonus(level) {
  return 2 + Math.floor((Math.max(1, level) - 1) / 4);
}

/** Бонус за выбранный навык (по правилам проекта — плоские +2). */
export const SKILL_PROFICIENCY_BONUS = 2;

/** Стоимость значений при наборе за очки (PHB point buy, 27 очков). */
export const POINT_BUY_COSTS = { 8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9 };
export const POINT_BUY_BUDGET = 27;
export const POINT_BUY_MIN = 8;
export const POINT_BUY_MAX = 15;
export const ABILITY_CAP = 20;

/** Уровни, на которых доступно «Улучшение характеристик» (+2 очка каждое). */
export const ASI_LEVELS = [4, 8, 12, 16, 19];

/* ---------- Навыки: соответствие характеристикам ---------- */

/** Короткий индекс характеристики -> трёхбуквенная русская метка. */
export const ABILITY_SHORT_RU = {
  str: 'Сил', dex: 'Лов', con: 'Тел', int: 'Инт', wis: 'Мдр', cha: 'Чар',
};

/** Короткий индекс -> ключ в data.stats. */
export const ABILITY_KEY_BY_SHORT = {
  str: 'strength', dex: 'dexterity', con: 'constitution',
  int: 'intelligence', wis: 'wisdom', cha: 'charisma',
};

/** Подпись навыка: «Intimidation (Чар)». */
export function skillLabel(skill) {
  return `${skill.name} (${ABILITY_SHORT_RU[skill.ability] ?? '?'})`;
}

/** Рекурсивно собирает индексы навыков (skill-*) из структур владений
 *  класса/расы: proficiency_choices, starting_proficiencies и т.п. */
export function collectSkillIndexes(node) {
  const found = new Set();
  const walk = (n) => {
    if (!n || typeof n !== 'object') return;
    if (typeof n.index === 'string' && n.index.startsWith('skill-')) {
      found.add(n.index.slice('skill-'.length));
    }
    for (const value of Object.values(n)) {
      if (value && typeof value === 'object') walk(value);
    }
  };
  walk(node);
  return found;
}

/* ---------- Русские подписи полей сжатых описаний ---------- */
export const FIELD_LABELS = {
  name: 'Название',
  level: 'Круг',
  school: 'Школа',
  casting_time: 'Время накладывания',
  range: 'Дистанция',
  components: 'Компоненты',
  material: 'Материал',
  duration: 'Длительность',
  concentration: 'Концентрация',
  ritual: 'Ритуал',
  classes: 'Классы',
  description: 'Описание',
  higher_level: 'На больших кругах',
  hit_die: 'Кость хитов',
  proficiencies: 'Владения',
  saving_throws: 'Спасброски',
  spellcasting_ability: 'Заклинательная характеристика',
  subclasses: 'Подклассы',
  speed: 'Скорость',
  size: 'Размер',
  size_description: 'О размере',
  ability_bonuses: 'Бонусы характеристик',
  traits: 'Особенности',
  languages: 'Языки',
  alignment: 'Мировоззрение',
  age: 'Возраст',
  prerequisites: 'Требования',
  category: 'Категория',
  cost: 'Стоимость',
  weight: 'Вес',
  damage: 'Урон',
  armor_class: 'Класс доспеха',
  properties: 'Свойства',
};

export const ABILITIES = [
  ['strength', 'Сила'],
  ['dexterity', 'Ловкость'],
  ['constitution', 'Телосложение'],
  ['intelligence', 'Интеллект'],
  ['wisdom', 'Мудрость'],
  ['charisma', 'Харизма'],
];
