import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, day, label, quantity } from './api'
import type { Audit, Batch, Job, Material, Recipe, User } from './api'
import { Drawer, ErrorNote, errorText, Status } from './common'

type Catalog = { user: User; materials: Material[]; reloadCatalog: () => Promise<void> }
const pageSize = 12
const columns: { id: keyof Batch; title: string; type?: string }[] = [
  { id: 'id', title: 'Партия', type: 'number' },
  { id: 'material_code', title: 'Материал' },
  { id: 'supplier', title: 'Поставщик' },
  { id: 'received_on', title: 'Поступление', type: 'date' },
  { id: 'status', title: 'Статус' },
  { id: 'quantity', title: 'Остаток', type: 'number' },
  { id: 'reserved', title: 'В резерве', type: 'number' },
  { id: 'unit_cost', title: 'За ед., ₽', type: 'number' },
  { id: 'currency', title: 'Валюта' },
  { id: 'received', title: 'Поступило', type: 'number' },
  { id: 'unit_price', title: 'Цена за ед.', type: 'number' },
  { id: 'exchange_rate', title: 'Курс', type: 'number' },
  { id: 'exchange_rate_on', title: 'Дата курса', type: 'date' },
  { id: 'comment', title: 'Комментарий' },
]
const statusOptions = ['on_review', 'accepted', 'rejected']
const currencies = ['RUB', 'USD', 'EUR']

function useHash(pattern: RegExp) {
  const read = () => Number(location.hash.match(pattern)?.[1]) || null
  const [id, setId] = useState(read)
  useEffect(() => {
    const sync = () => setId(read())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  })
  return [id, (next: number | null) => {
    if (!next) history.replaceState(null, '', location.pathname + location.search)
    setId(next)
  }] as const
}

function SelectMaterial({ materials }: { materials: Material[] }) {
  return <select name="material_code" required defaultValue="">
    <option value="" disabled>Выберите материал</option>
    {materials.map(material => <option value={material.code} key={material.code}>
      {material.name} · {material.unit}
    </option>)}
  </select>
}

function formData(event: FormEvent<HTMLFormElement>) {
  event.preventDefault()
  return new FormData(event.currentTarget)
}

type ReceiptLine = { key: number }
function ReceiptForm({ user, materials, done }: { user: User; materials: Material[]; done: () => void }) {
  const [lines, setLines] = useState<ReceiptLine[]>([{ key: 1 }])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = formData(event)
    setError('')
    setBusy(true)
    try {
      await api('/receipts', { supplier: data.get('supplier'), received_on: data.get('received_on'),
        lines: data.getAll('material_code').map((code, index) => ({
          material_code: code, quantity: data.getAll('quantity')[index],
          unit_price: data.getAll('unit_price')[index], currency: data.getAll('currency')[index],
          comment: data.getAll('comment')[index],
        })),
      })
      done()
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="edit-form">
    <div className="form-row"><label>Поставщик<input name="supplier" required maxLength={160} /></label>
      <label>Дата<input name="received_on" type="date" required defaultValue={user.today} /></label></div>
    {lines.map((line, index) => <fieldset key={line.key}><legend>Строка {index + 1}</legend>
      <div className="form-row">
        <label>Материал<SelectMaterial materials={materials} /></label>
        <label>Количество<input name="quantity" type="number" step="0.000001" min="0.000001" required /></label>
        <label>Цена за ед.<input name="unit_price" type="number" step="0.000001" min="0" required /></label>
        <label>Валюта<select name="currency">{currencies.map(item => <option key={item}>{item}</option>)}</select></label>
      </div>
      <div className="form-row"><label>Комментарий<input name="comment" maxLength={500} /></label>
        {lines.length > 1 && <button type="button" className="secondary"
          onClick={() => setLines(previous => previous.filter(item => item.key !== line.key))}>Убрать строку</button>}
      </div>
    </fieldset>)}
    <ErrorNote error={error} />
    <div className="actions">
      <button type="button" className="secondary" disabled={lines.length >= 100}
        onClick={() => setLines(previous => [...previous, { key: previous.at(-1)!.key + 1 }])}>Добавить строку</button>
      <button disabled={busy}>{busy ? 'Сохраняем…' : 'Оформить поступление'}</button>
    </div>
  </form>
}

function MaterialForm({ done }: { done: () => void }) {
  const [error, setError] = useState('')
  async function submit(event: FormEvent<HTMLFormElement>) {
    try { await api('/materials', Object.fromEntries(formData(event))); done() }
    catch (err) { setError(errorText(err)) }
  }
  return <form className="edit-form" onSubmit={submit}>
    <div className="form-row"><label>Код<input name="code" pattern="[A-Z][A-Z0-9_\-]{0,31}" required /></label>
      <label>Название<input name="name" maxLength={160} required /></label>
      <label>Единица<input name="unit" maxLength={160} required /></label></div>
    <ErrorNote error={error} /><button>Создать материал</button>
  </form>
}

function BatchCard({ id, canEdit, updated, close }: { id: number; canEdit: boolean; updated: () => void; close: () => void }) {
  const [record, setRecord] = useState<{ batch: Batch; history: Audit[] } | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    api<{ batch: Batch; history: Audit[] }>('/batches/' + id).then(value => {
      if (active) setRecord(value)
    }).catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [id])
  async function inspect(status: string) {
    setError('')
    setBusy(true)
    try {
      await api('/batches/' + id + '/inspection', { status })
      setRecord(await api('/batches/' + id))
      updated()
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <Drawer title={'Партия #' + id} close={close}>
    <ErrorNote error={error} />
    {record && <>
      <dl className="facts">{columns.filter(column => column.id !== 'id').map(column =>
        <div key={column.id}><dt>{column.title}</dt><dd>{batchValue(record.batch, column.id)}</dd></div>)}</dl>
      {canEdit && record.batch.status === 'on_review' && <div className="actions">
        <button disabled={busy} onClick={() => inspect('accepted')}>Принять</button>
        <button className="secondary" disabled={busy} onClick={() => inspect('rejected')}>Отметить брак</button>
      </div>}
      <h3>История изменений</h3>
      <ol className="timeline">{[...record.history].reverse().map(row => <AuditRow key={row.id} row={row} />)}</ol>
    </>}
  </Drawer>
}

function AuditRow({ row }: { row: Audit }) {
  const changed = columns.filter(column => column.id !== 'id' && row.after[column.id] !== row.before?.[column.id])
  return <li><strong>{row.actor}</strong> <time>{new Date(row.at).toLocaleString('ru-RU')}</time>
    <details><summary>{row.before ? 'Изменения' : 'Поступление'}</summary>
      <div>{changed.map(column => {
        const from = row.before?.[column.id]
        const to = row.after[column.id]
        const format = (value: unknown) => value == null || value === '' ? '—' : (label[String(value)] ?? String(value))
        return <p key={column.id}>{column.title} · {row.before ? format(from) + ' → ' : ''}{format(to)}</p>
      })}</div>
    </details>
  </li>
}

function batchValue(batch: Batch, key: keyof Batch, materials: Material[] = []) {
  if (key === 'status') return <Status value={batch.status} />
  if (key === 'received_on' || key === 'exchange_rate_on') return day(batch[key])
  if (key === 'material_code') return materials.find(material => material.code === batch.material_code)?.name ?? batch.material_code
  if (columns.find(column => column.id === key)?.type === 'number') {
    return quantity(String(batch[key])) + (['quantity', 'received', 'reserved'].includes(key)
      ? ' ' + (materials.find(material => material.code === batch.material_code)?.unit ?? '') : '')
  }
  return batch[key] || '—'
}

export function Warehouse({ user, materials, reloadCatalog }: Catalog) {
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [applied, setApplied] = useState(filters)
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ id: keyof Batch; descending: boolean }>({ id: 'id', descending: true })
  const [rows, setRows] = useState<Batch[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const [form, setForm] = useState<'receipt' | 'material' | null>(null)
  const [revision, setRevision] = useState(0)
  const [selected, select] = useHash(/^#batch-(\d+)$/)
  const canEdit = user.role === 'storekeeper' || user.role === 'manager'
  useEffect(() => {
    const timer = setTimeout(() => { setApplied(filters); setPage(0) }, 250)
    return () => clearTimeout(timer)
  }, [filters])
  useEffect(() => {
    let active = true
    setError('')
    setBusy(true)
    const options = new URLSearchParams({ sort: sort.id, descending: String(sort.descending),
      offset: String(page * pageSize), limit: String(pageSize) })
    for (const [key, value] of Object.entries(applied)) if (value) options.set(key, value)
    api<{ items: Batch[]; total: number }>('/batches?' + options).then(result => {
      if (active) { setRows(result.items); setTotal(result.total) }
    }).catch(err => { if (active) setError(errorText(err)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [applied, page, sort, revision])
  const updated = () => setRevision(previous => previous + 1)
  const updateFilter = (id: string, value: string) => setFilters(previous => ({ ...previous, [id]: value }))
  return <>
    <header className="page-head"><div><h1>Партии сырья</h1><p>Поступления, проверка качества и складские остатки</p></div>
      {canEdit && <div className="actions"><button className="secondary" onClick={() => setForm('material')}>Создать материал</button>
        <button onClick={() => setForm('receipt')}>Новое поступление</button></div>}
    </header>
    {form && <Drawer title={form === 'receipt' ? 'Новое поступление' : 'Новый материал'} close={() => setForm(null)}>
      {form === 'receipt' ? <ReceiptForm user={user} materials={materials} done={() => {
        setForm(null); setPage(0); updated()
      }} /> : <MaterialForm done={() => { setForm(null); void reloadCatalog() }} />}
    </Drawer>}
    {selected && <BatchCard key={selected} id={selected} canEdit={canEdit} updated={updated} close={() => select(null)} />}
    <ErrorNote error={error} />
    <section className="panel" aria-label="Реестр партий" aria-busy={busy}>
      <div className="panel-toolbar"><strong>Реестр партий <span className="count">{total}</span></strong>
        <button className="secondary compact" disabled={!Object.values(filters).some(Boolean)}
          onClick={() => setFilters({})}>Сбросить фильтры</button></div>
      <div className="table-wrap"><table className="batch-table">
        <thead><tr>{columns.map(column => <th key={column.id}
          aria-sort={sort.id === column.id ? (sort.descending ? 'descending' : 'ascending') : 'none'}>
          <button onClick={() => { setSort({ id: column.id, descending: sort.id === column.id && !sort.descending }); setPage(0) }}>
            {column.title}{sort.id === column.id && <span className="sort" aria-hidden="true">{sort.descending ? '▾' : '▴'}</span>}
          </button>
        </th>)}</tr>
        <tr>{columns.map(column => <th key={column.id} className="filter">
          {['status', 'currency', 'material_code'].includes(column.id)
            ? <select aria-label={'Фильтр: ' + column.title} value={filters[column.id] ?? ''}
                onChange={event => updateFilter(column.id, event.target.value)}>
                <option value="">Все</option>
                {(column.id === 'status' ? statusOptions : column.id === 'currency' ? currencies : materials.map(m => m.code))
                  .map(value => <option value={value} key={value}>
                    {label[value] ?? materials.find(m => m.code === value)?.name ?? value}
                  </option>)}
              </select>
            : <input aria-label={'Фильтр: ' + column.title} placeholder="Все"
                value={filters[column.id] ?? ''} type={column.type ?? 'text'} step="any"
                onChange={event => updateFilter(column.id, event.target.value)} />}
        </th>)}</tr></thead>
        <tbody>{rows.map(batch => <tr key={batch.id}>{columns.map(column => <td key={column.id}>
          {column.id === 'id' ? <a href={'#batch-' + batch.id} onClick={() => select(batch.id)}>#{batch.id}</a>
            : batchValue(batch, column.id, materials)}
        </td>)}</tr>)}</tbody>
      </table></div>
      {!rows.length && <p className="empty">{busy ? 'Загружаем партии…' : 'Партии не найдены'}</p>}
      <div className="pagination">
        <span>{total ? (page * pageSize + 1) + '–' + Math.min((page + 1) * pageSize, total) + ' из ' + total : '0 партий'}</span>
        <div className="actions"><button className="secondary compact" disabled={page === 0}
          onClick={() => setPage(page - 1)}>Назад</button>
          <button className="secondary compact" disabled={(page + 1) * pageSize >= total}
            onClick={() => setPage(page + 1)}>Далее</button></div>
      </div>
    </section>
  </>
}

function RecipeForm({ materials, done }: { materials: Material[]; done: () => void }) {
  const [error, setError] = useState('')
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = formData(event)
    const ingredients = Object.fromEntries(materials.flatMap(material => {
      const value = data.get(material.code)
      return value ? [[material.code, value]] : []
    }))
    try { await api('/recipes', { name: data.get('recipe_name'), ingredients }); done() }
    catch (err) { setError(errorText(err)) }
  }
  return <form className="edit-form" onSubmit={submit}>
    <label>Название рецепта<input name="recipe_name" required maxLength={160} /></label>
    <fieldset><legend>Нормы на одну свечу</legend><div className="form-row">
      {materials.map(material => <label key={material.code}>{material.name} · {material.unit}
        <input type="number" name={material.code} min="0.000001" step="0.000001" />
      </label>)}</div></fieldset>
    <ErrorNote error={error} /><button>Сохранить рецепт</button>
  </form>
}

function PlanForm({ recipes, today, done }: { recipes: Recipe[]; today: string; done: (id: number) => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = Object.fromEntries(formData(event))
    setBusy(true)
    const intent = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null
    const path = intent?.value === 'plan' ? '/jobs' : '/jobs/drafts'
    try { done((await api<Job>(path, { ...data, recipe_id: Number(data.recipe_id) })).id) }
    catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <form className="edit-form" onSubmit={submit}>
    <div className="form-row"><label>Рецепт<select name="recipe_id" required defaultValue="">
      <option value="" disabled>Выберите рецепт</option>
      {recipes.map(recipe => <option value={recipe.id} key={recipe.id}>{recipe.name}</option>)}
    </select></label><label>Количество свечей<input name="quantity" type="number" min="1" step="1" required /></label>
      <label>Дата<input name="planned_on" type="date" defaultValue={today} required /></label></div>
    <ErrorNote error={error} /><div className="actions">
      <button disabled={busy} value="draft">Сохранить черновик</button>
      <button className="secondary" disabled={busy} value="plan">Запланировать сразу</button>
    </div>
  </form>
}

function jobStatus(job: Job) {
  return job.status === 'planned' && job.components.some(row => row.signal !== 'green') ? 'shortage' : job.status
}

function JobCard({ id, canEdit, close, updated }: { id: number; canEdit: boolean; close: () => void; updated: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [cost, setCost] = useState<{ total_rub: string; kind: string } | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    Promise.all([api<Job>('/jobs/' + id), api<{ total_rub: string; kind: string }>('/jobs/' + id + '/cost')])
      .then(([value, price]) => { if (active) { setJob(value); setCost(price) } })
      .catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [id])
  async function change(action: string, body: unknown = {}) {
    setBusy(true)
    setError('')
    try {
      const path = action === 'complete' ? '/completion/' + id : '/jobs/' + id + '/' + action
      setJob(await api<Job>(path, body))
      updated()
      setCost(await api('/jobs/' + id + '/cost'))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <Drawer title={job ? 'Задание #' + id + ' · ' + job.recipe : 'Задание #' + id} close={close}>
    {job && <>
      <div className="job-meta"><Status value={jobStatus(job)} /><span>{day(job.planned_on)}</span>
        <span>{quantity(job.quantity)} свечей</span>
        {cost && <strong>{({ actual: 'Фактическая себестоимость', issued: 'Стоимость выданного сырья', reserved: 'Стоимость резерва' })[cost.kind] ?? 'Себестоимость'} · {quantity(cost.total_rub)} ₽</strong>}
      </div>
      <div className="table-wrap"><table>
        <thead><tr><th>Компонент</th><th>Потребность</th><th>Резерв</th>{job.status === 'in_progress' && <th>Списано при запуске</th>}<th>{job.status === 'completed' ? 'Фактический расход' : 'Дефицит'}</th></tr></thead>
        <tbody>{job.components.map(component => <tr key={component.code}>
          <td>{['planned', 'draft'].includes(job.status) && <span className={'signal ' + component.signal}
            role="img" aria-label={{ green: 'Полный резерв', yellow: 'Частичный резерв', red: 'Без резерва' }[component.signal]} />}
            {component.name}</td>
          {[component.required, component.reserved, ...(job.status === 'in_progress' ? [component.issued] : []), job.status === 'completed' ? component.consumed : component.missing]
            .map((amount, index) => <td key={index}>{quantity(amount)} {component.unit}</td>)}
        </tr>)}</tbody></table></div>
      {canEdit && ['draft', 'planned'].includes(job.status) && <div className="actions">
        {job.status === 'draft' && <button disabled={busy} onClick={() => change('plan')}>Запланировать</button>}
        {jobStatus(job) === 'planned' && <button disabled={busy} onClick={() => change('start')}>Запустить</button>}
        <button className="secondary" disabled={busy} onClick={() => change('cancel')}>Отменить задание</button>
      </div>}
      {canEdit && job.status === 'in_progress' && <form className="edit-form" onSubmit={event => {
        void change('complete', { actual: Object.fromEntries(formData(event)) })
      }}><fieldset><legend>Фактический расход</legend><div className="form-row">
        {job.components.map(component => <label key={component.code}>{component.name} · {component.unit}
          <input name={component.code} type="number" min="0" step="0.000001" defaultValue={component.required} required />
        </label>)}</div></fieldset><button disabled={busy}>Завершить задание</button></form>}
    </>}
    <ErrorNote error={error} />
  </Drawer>
}

export function Production({ user, materials, recipes, initialJob, reloadCatalog }:
  Catalog & { recipes: Recipe[]; initialJob: number | null }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [form, setForm] = useState<'job' | 'recipe' | null>(null)
  const [selected, setSelected] = useState<number | null>(initialJob)
  const [offset, setOffset] = useState(0)
  const canEdit = user.role !== 'storekeeper'
  const updated = () => setRevision(previous => previous + 1)
  useEffect(() => {
    let active = true
    api<Job[]>('/jobs?offset=' + offset).then(items => { if (active) setJobs(items) })
      .catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [revision, offset])
  return <>
    <header className="page-head"><div><h1>Производственные задания</h1><p>Планирование, резервы и фактический расход</p></div>
      {canEdit && <div className="actions"><button className="secondary" onClick={() => setForm('recipe')}>Создать рецепт</button>
        <button onClick={() => setForm('job')}>Новое задание</button></div>}</header>
    {form && <Drawer title={form === 'job' ? 'Новое задание' : 'Новый рецепт'} close={() => setForm(null)}>
      {form === 'job' ? <PlanForm today={user.today} recipes={recipes} done={id => {
        setForm(null); setSelected(id); setOffset(0); updated()
      }} /> : <RecipeForm materials={materials} done={() => { setForm(null); void reloadCatalog() }} />}
    </Drawer>}
    {selected && <JobCard id={selected} key={selected} updated={updated} canEdit={canEdit} close={() => setSelected(null)} />}
    <ErrorNote error={error} />
    <section className="panel"><div className="table-wrap"><table>
      <thead><tr><th>Задание</th><th>Рецепт</th><th>Количество</th><th>Дата плана</th><th>Статус</th></tr></thead>
      <tbody>{jobs.map(job => <tr key={job.id}>
        <td><button className="link" onClick={() => setSelected(job.id)}>#{job.id}</button></td>
        <td>{job.recipe}</td><td>{quantity(job.quantity)} шт</td><td>{day(job.planned_on)}</td><td><Status value={jobStatus(job)} /></td>
      </tr>)}</tbody>
    </table></div>{!jobs.length && <p className="empty">Задания не найдены</p>}
    {(offset > 0 || jobs.length >= 50) && <div className="pagination">
      <button className="secondary" disabled={offset === 0} onClick={() => setOffset(offset - 50)}>Назад</button>
      <button className="secondary" disabled={jobs.length < 50} onClick={() => setOffset(offset + 50)}>Далее</button>
    </div>}</section>
  </>
}
