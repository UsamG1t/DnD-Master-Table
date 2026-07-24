// Операции изменения персонажа в формате журнала backend:
// {op_id, path, value, ts}. Тот же формат использует Android-клиент
// для офлайн-журнала.

/** Пути структуры персонажа, которые отслеживает редактор. */
export const TRACKED_PATHS = [
  'race', 'subrace', 'class', 'subclass', 'level',
  'background', 'alignment',
  'hp.current', 'hp.max', 'armor_class', 'speed', 'money',
  'stats.strength', 'stats.dexterity', 'stats.constitution',
  'stats.intelligence', 'stats.wisdom', 'stats.charisma',
  'asi', 'background_bonuses',
  'skills', 'spells', 'feats', 'items', 'notes',
];

export function getByPath(obj, path) {
  return path.split('.').reduce((node, part) => (node == null ? undefined : node[part]), obj);
}

/** Сравнивает старую и новую структуру и выдаёт операции для PATCH. */
export function diffOps(oldData, newData) {
  const ops = [];
  const ts = Date.now();
  for (const path of TRACKED_PATHS) {
    const before = getByPath(oldData, path);
    const after = getByPath(newData, path);
    if (JSON.stringify(before ?? null) !== JSON.stringify(after ?? null)) {
      ops.push({
        op_id: crypto.randomUUID(),
        path,
        value: after === undefined ? null : after,
        ts,
      });
    }
  }
  return ops;
}
