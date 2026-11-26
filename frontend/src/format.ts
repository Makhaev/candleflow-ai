import { day, label, quantity } from './api'
import type { Audit } from './api'

export const fieldNames: Record<string, string> = {
  code: 'Код', comment: 'Комментарий', completed_on: 'Дата завершения', currency: 'Валюта',
  exchange_rate: 'Курс', exchange_rate_on: 'Дата курса', material_code: 'Материал', name: 'Название',
  planned_on: 'Дата плана', quantity: 'Количество', received: 'Поступило', received_on: 'Дата поступления',
  receipt_id: 'Поступление', recipe_id: 'Рецепт', reserved: 'В резерве', status: 'Статус', supplier: 'Поставщик',
  unit: 'Единица', unit_cost: 'Себестоимость за ед., ₽', unit_price: 'Цена за ед.',
}

// The same column means different things for different records.
const entityFieldNames: Record<string, Record<string, string>> = {
  batches: { quantity: 'На складе' },
  production_jobs: { quantity: 'Свечей' },
}

// Technical columns that change together with a visible field and only add noise.
const hidden = new Set(['id', 'actor_id', 'created_by', 'exchange_rate_id', 'supplier_id'])
const numeric = new Set(['quantity', 'received', 'reserved', 'unit_cost', 'unit_price', 'exchange_rate'])
const dates = new Set(['received_on', 'planned_on', 'completed_on', 'exchange_rate_on'])

export function formatValue(key: string, value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  if (typeof value !== 'string' && typeof value !== 'number') return JSON.stringify(value)
  const text = String(value)
  if (numeric.has(key)) return quantity(text)
  if (dates.has(key)) return day(text)
  return label[text] ?? text
}

export type Change = { key: string; field: string; from: string; to: string }

export function changes(row: Audit): Change[] {
  return [...new Set([...Object.keys(row.before ?? {}), ...Object.keys(row.after)])]
    .filter(key => !hidden.has(key) && JSON.stringify(row.before?.[key]) !== JSON.stringify(row.after[key]))
    .map(key => ({
      key, field: entityFieldNames[row.entity]?.[key] ?? fieldNames[key] ?? key,
      from: formatValue(key, row.before?.[key]), to: formatValue(key, row.after[key]),
    }))
}

/** One line for a list: «Статус: На проверке → Принята · Резерв: 0 → 2,6». */
export function summary(row: Audit, limit = 2): string {
  if (!row.before) return 'Создание записи'
  const list = changes(row)
  if (!list.length) return 'Без изменений значений'
  const shown = list.slice(0, limit).map(item => `${item.field}: ${item.from} → ${item.to}`).join(' · ')
  return list.length > limit ? `${shown} · ещё ${list.length - limit}` : shown
}
