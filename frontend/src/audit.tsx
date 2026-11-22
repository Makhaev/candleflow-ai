import { Fragment, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, dateTime, label } from './api'
import type { Audit } from './api'
import { ErrorNote, errorText } from './common'

const entities = {
  batches: 'Партия',
  receipts: 'Поступление',
  production_jobs: 'Задание',
  materials: 'Материал',
  recipes: 'Рецепт',
}

const fieldNames: Record<string, string> = {
  actor_id: 'Инициатор', code: 'Код', comment: 'Комментарий', completed_on: 'Дата завершения',
  currency: 'Валюта', exchange_rate: 'Курс', exchange_rate_on: 'Дата курса', exchange_rate_id: 'Котировка',
  id: 'Номер', material_code: 'Материал', name: 'Название', planned_on: 'Дата плана',
  quantity: 'Количество', received: 'Поступило', received_on: 'Дата поступления', receipt_id: 'Поступление',
  recipe_id: 'Рецепт', reserved: 'В резерве', status: 'Статус', supplier: 'Поставщик', supplier_id: 'Поставщик',
  unit: 'Единица измерения', unit_cost: 'Себестоимость за ед.', unit_price: 'Цена за ед.', created_by: 'Создал',
}

type Filters = { entity: string; actor: string; date_from: string; date_to: string }
const initialFilters: Filters = { entity: '', actor: '', date_from: '', date_to: '' }
const limit = 15

function display(value: unknown) {
  if (value === undefined || value === null || value === '') return '—'
  if (typeof value === 'string') return label[value] ?? value
  return JSON.stringify(value)
}

function AuditChanges({ row }: { row: Audit }) {
  const keys = [...new Set([...Object.keys(row.before ?? {}), ...Object.keys(row.after)])]
    .filter(key => JSON.stringify(row.before?.[key]) !== JSON.stringify(row.after[key]))
  if (!keys.length) return <p className="muted">Значения полей совпадают.</p>
  return <dl className="audit-diff">
    {keys.map(key => <div key={key}>
      <dt>{fieldNames[key] ?? key}</dt>
      <dd><span>Было</span>{display(row.before?.[key])}</dd>
      <dd><span>Стало</span>{display(row.after[key])}</dd>
    </div>)}
  </dl>
}

export function AuditLog({ goToBatch, goToJob }: { goToBatch: (id: number) => void; goToJob: (id: number) => void }) {
  const [draft, setDraft] = useState(initialFilters)
  const [filters, setFilters] = useState(initialFilters)
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState<Audit[]>([])
  const [total, setTotal] = useState(0)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    setBusy(true)
    setError('')
    const params = new URLSearchParams({ offset: String(page * limit), limit: String(limit) })
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
    api<{ items: Audit[]; total: number }>('/audit?' + params)
      .then(result => { if (active) { setRows(result.items); setTotal(result.total) } })
      .catch(err => { if (active) setError(errorText(err)) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [filters, page, attempt])

  function update(name: keyof Filters, value: string) {
    setDraft(previous => ({ ...previous, [name]: value }))
  }

  function apply(event: FormEvent) {
    event.preventDefault()
    setPage(0)
    setExpanded(null)
    setFilters({ ...draft, actor: draft.actor.trim() })
  }

  function changePage(next: number) {
    setExpanded(null)
    setPage(next)
  }

  return <>
    <header className="page-head"><div><h1>Журнал аудита</h1><p>Изменения по партиям, поступлениям и производству</p></div></header>
    <section className="panel audit-panel" aria-label="Журнал изменений" aria-busy={busy}>
      <form className="audit-controls" onSubmit={apply}>
        <div className="form-row">
          <label>Объект<select value={draft.entity} onChange={event => update('entity', event.target.value)}>
            <option value="">Все объекты</option>
            {Object.entries(entities).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select></label>
          <label>Сотрудник<input value={draft.actor} onChange={event => update('actor', event.target.value)}
            placeholder="Имя или фамилия" maxLength={160} /></label>
          <label>Дата с<input type="date" value={draft.date_from} max={draft.date_to || undefined}
            onChange={event => update('date_from', event.target.value)} /></label>
          <label>Дата по<input type="date" value={draft.date_to} min={draft.date_from || undefined}
            onChange={event => update('date_to', event.target.value)} /></label>
        </div>
        <div className="actions">
          <button type="submit">Применить фильтры</button>
          <button type="button" className="secondary" disabled={!Object.values(draft).some(Boolean) && !Object.values(filters).some(Boolean)}
            onClick={() => { setDraft(initialFilters); setFilters(initialFilters); changePage(0) }}>Сбросить</button>
        </div>
      </form>
      {error ? <div className="audit-feedback"><ErrorNote error={error} />
        <button className="secondary" onClick={() => setAttempt(current => current + 1)}>Повторить загрузку</button></div>
        : busy ? <p className="empty" role="status">Загружаем журнал…</p>
        : rows.length ? <>
          <div className="table-wrap audit-scroll" tabIndex={0} aria-label="Таблица аудита">
            <table className="audit-table">
              <thead><tr><th scope="col">Время (МСК)</th><th scope="col">Сотрудник</th><th scope="col">Объект</th>
                <th scope="col">Действие</th><th scope="col">Подробности</th></tr></thead>
              <tbody>{rows.map(row => {
                const entity = entities[row.entity as keyof typeof entities] ?? row.entity
                const isExpanded = expanded === row.id
                const id = Number(row.entity_id)
                const target = Number.isSafeInteger(id) && id > 0
                  ? row.entity === 'batches' ? goToBatch : row.entity === 'production_jobs' ? goToJob : null : null
                return <Fragment key={row.id}>
                  <tr>
                    <td><time dateTime={row.at}>{dateTime(row.at)}</time></td>
                    <td>{row.actor}</td>
                    <td>{target ? <button className="link" onClick={() => target(id)}>{entity} #{row.entity_id}</button>
                      : <>{entity} · {row.entity_id}</>}</td>
                    <td>{row.before ? 'Изменение' : 'Создание'}</td>
                    <td><button className="secondary compact" aria-expanded={isExpanded}
                      onClick={() => setExpanded(isExpanded ? null : row.id)}>{isExpanded ? 'Скрыть' : 'Показать'}</button></td>
                  </tr>
                  {isExpanded && <tr className="audit-expanded"><td colSpan={5}><AuditChanges row={row} /></td></tr>}
                </Fragment>
              })}</tbody>
            </table>
          </div>
          <div className="pagination">
            <span>{page * limit + 1}–{page * limit + rows.length} из {total}</span>
            <div className="actions">
              <button className="secondary compact" disabled={page === 0} onClick={() => changePage(page - 1)}>Назад</button>
              <button className="secondary compact" disabled={(page + 1) * limit >= total} onClick={() => changePage(page + 1)}>Далее</button>
            </div>
          </div>
        </> : <p className="empty" role="status">Нет изменений по выбранным фильтрам.</p>}
    </section>
  </>
}
