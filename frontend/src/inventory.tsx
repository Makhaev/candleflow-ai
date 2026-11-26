import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { api, dateTime, day, label, quantity, subtract } from './api'
import type { Audit, Batch, Job, Material, Page, Recipe, User } from './api'
import { Drawer, ErrorNote, errorText, PageHead, Status } from './common'
import { changes } from './format'

type Catalog = { user: User; materials: Material[]; reloadCatalog: () => Promise<void> }
type Column = { id: keyof Batch; title: string; type?: 'number' | 'date'; core?: boolean }

const pageSize = 12
const columns: Column[] = [
  { id: 'id', title: 'Партия', type: 'number', core: true },
  { id: 'material_code', title: 'Материал', core: true },
  { id: 'supplier', title: 'Поставщик', core: true },
  { id: 'received_on', title: 'Поступление', type: 'date', core: true },
  { id: 'status', title: 'Статус', core: true },
  { id: 'quantity', title: 'Остаток', type: 'number', core: true },
  { id: 'reserved', title: 'В резерве', type: 'number', core: true },
  { id: 'unit_cost', title: 'За ед., ₽', type: 'number', core: true },
  { id: 'currency', title: 'Валюта', core: true },
  { id: 'received', title: 'Поступило', type: 'number' },
  { id: 'unit_price', title: 'Цена за ед.', type: 'number' },
  { id: 'exchange_rate', title: 'Курс', type: 'number' },
  { id: 'exchange_rate_on', title: 'Дата курса', type: 'date' },
  { id: 'comment', title: 'Комментарий' },
]
const statusOptions = ['on_review', 'accepted', 'rejected']
const currencies = ['RUB', 'USD', 'EUR']
const unitOf = (materials: Material[], code: string) => materials.find(item => item.code === code)?.unit ?? ''
const nameOf = (materials: Material[], code: string) => materials.find(item => item.code === code)?.name ?? code

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

function formData(event: FormEvent<HTMLFormElement>) {
  event.preventDefault()
  return new FormData(event.currentTarget)
}

function Field({ label: title, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{title}</span>{children}</label>
}

function SelectMaterial({ materials }: { materials: Material[] }) {
  return <select name="material_code" required defaultValue="">
    <option value="" disabled>Выберите материал</option>
    {materials.map(material => <option value={material.code} key={material.code}>
      {material.name} · {material.unit}
    </option>)}
  </select>
}

/* Warehouse ---------------------------------------------------------------- */

function ReceiptForm({ user, materials, done }: { user: User; materials: Material[]; done: () => void }) {
  const [lines, setLines] = useState([1])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = formData(event)
    setError('')
    setBusy(true)
    try {
      await api('/receipts', {
        supplier: data.get('supplier'), received_on: data.get('received_on'),
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
    <p className="hint">Каждая строка станет отдельной партией со статусом «На проверке». Себестоимость в рублях
      посчитается по курсу на дату поступления.</p>
    <div className="form-row">
      <Field label="Поставщик"><input name="supplier" required maxLength={160} placeholder="ООО Восковой двор" /></Field>
      <Field label="Дата поступления"><input name="received_on" type="date" required defaultValue={user.today} /></Field>
    </div>
    {lines.map((line, index) => <fieldset key={line}>
      <legend>Строка {index + 1}</legend>
      <div className="form-row">
        <Field label="Материал"><SelectMaterial materials={materials} /></Field>
        <Field label="Количество"><input name="quantity" type="number" step="0.000001" min="0.000001" required /></Field>
        <Field label="Цена за ед."><input name="unit_price" type="number" step="0.000001" min="0" required /></Field>
        <Field label="Валюта"><select name="currency">{currencies.map(item => <option key={item}>{item}</option>)}</select></Field>
      </div>
      <div className="form-row">
        <Field label="Комментарий"><input name="comment" maxLength={500} placeholder="Необязательно" /></Field>
        {lines.length > 1 && <button type="button" className="secondary"
          onClick={() => setLines(previous => previous.filter(item => item !== line))}>Убрать строку</button>}
      </div>
    </fieldset>)}
    <ErrorNote error={error} />
    <div className="form-actions">
      <button type="button" className="secondary" disabled={lines.length >= 100}
        onClick={() => setLines(previous => [...previous, previous.at(-1)! + 1])}>Добавить строку</button>
      <button disabled={busy}>{busy ? 'Сохраняем…' : 'Оформить поступление'}</button>
    </div>
  </form>
}

function MaterialForm({ done }: { done: () => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = Object.fromEntries(formData(event))
    setBusy(true)
    try { await api('/materials', data); done() } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <form className="edit-form" onSubmit={submit}>
    <div className="form-row">
      <Field label="Код"><input name="code" pattern="[A-Z][A-Z0-9_\-]{0,31}" required placeholder="SOY"
        title="Латинские заглавные буквы, цифры, дефис и подчёркивание" /></Field>
      <Field label="Название"><input name="name" maxLength={160} required placeholder="Соевый воск" /></Field>
      <Field label="Единица"><input name="unit" maxLength={160} required placeholder="кг" /></Field>
    </div>
    <ErrorNote error={error} />
    <div className="form-actions"><button disabled={busy}>Создать материал</button></div>
  </form>
}

function Fact({ title, children }: { title: string; children: ReactNode }) {
  return <div><dt>{title}</dt><dd>{children}</dd></div>
}

function History({ rows }: { rows: Audit[] }) {
  if (!rows.length) return <p className="muted">Изменений пока нет.</p>
  return <ol className="timeline">{[...rows].reverse().map(row => {
    const list = changes(row)
    return <li key={row.id}>
      <div className="timeline-head"><strong>{row.actor}</strong><time dateTime={row.at}>{dateTime(row.at)}</time></div>
      {row.before
        ? <ul>{list.map(item => <li key={item.key}>{item.field}: {item.from} → <b>{item.to}</b></li>)}</ul>
        : <p className="muted">Партия создана при поступлении</p>}
    </li>
  })}</ol>
}

function BatchCard({ id, canEdit, materials, updated, close }:
  { id: number; canEdit: boolean; materials: Material[]; updated: () => void; close: () => void }) {
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
  const batch = record?.batch
  const unit = batch ? unitOf(materials, batch.material_code) : ''
  const amount = (value: string) => quantity(value) + ' ' + unit
  return <Drawer title={batch ? `Партия #${id} · ${nameOf(materials, batch.material_code)}` : `Партия #${id}`} close={close}>
    <ErrorNote error={error} />
    {!batch ? !error && <p className="muted" role="status">Загружаем партию…</p> : <>
      <div className="card-status">
        <Status value={batch.status} />
        {batch.status === 'on_review' && <span className="muted">Партию нельзя резервировать, пока она не принята.</span>}
      </div>
      {canEdit && batch.status === 'on_review' && <div className="actions">
        <button disabled={busy} onClick={() => inspect('accepted')}>Принять партию</button>
        <button className="secondary" disabled={busy} onClick={() => inspect('rejected')}>Отметить брак</button>
      </div>}
      <section className="card-section"><h3>Остатки</h3><dl className="facts">
        <Fact title="Поступило">{amount(batch.received)}</Fact>
        <Fact title="На складе">{amount(batch.quantity)}</Fact>
        <Fact title="В резерве">{amount(batch.reserved)}</Fact>
        <Fact title="Свободно">{batch.status === 'accepted' ? amount(subtract(batch.quantity, batch.reserved)) : '—'}</Fact>
      </dl></section>
      <section className="card-section"><h3>Стоимость</h3><dl className="facts">
        <Fact title="Цена за ед.">{quantity(batch.unit_price)} {batch.currency}</Fact>
        <Fact title={batch.currency === 'RUB' ? 'Курс' : `Курс ${batch.currency} на ${day(batch.exchange_rate_on)}`}>
          {batch.currency === 'RUB' ? 'не нужен' : quantity(batch.exchange_rate) + ' ₽'}</Fact>
        <Fact title="Себестоимость за ед.">{quantity(batch.unit_cost)} ₽</Fact>
      </dl>
        {batch.currency !== 'RUB' && batch.exchange_rate_on !== batch.received_on &&
          <p className="hint">Поступление пришлось на выходной, поэтому взят курс предыдущего рабочего дня.</p>}
      </section>
      <section className="card-section"><h3>Поступление</h3><dl className="facts">
        <Fact title="Поставщик">{batch.supplier}</Fact>
        <Fact title="Дата">{day(batch.received_on)}</Fact>
        <Fact title="Документ">№ {batch.receipt_id}</Fact>
        {batch.comment && <Fact title="Комментарий">{batch.comment}</Fact>}
      </dl></section>
      <section className="card-section"><h3>История изменений</h3><History rows={record.history} /></section>
    </>}
  </Drawer>
}

function batchValue(batch: Batch, column: Column, materials: Material[]) {
  const key = column.id
  if (key === 'status') return <Status value={batch.status} />
  if (column.type === 'date') return day(String(batch[key]))
  if (key === 'material_code') return nameOf(materials, batch.material_code)
  if (column.type === 'number') {
    const withUnit = ['quantity', 'received', 'reserved'].includes(key)
    return quantity(String(batch[key])) + (withUnit ? ' ' + unitOf(materials, batch.material_code) : '')
  }
  return batch[key] || '—'
}

function ColumnFilter({ column, value, materials, onChange }:
  { column: Column; value: string; materials: Material[]; onChange: (value: string) => void }) {
  const options = column.id === 'status' ? statusOptions : column.id === 'currency' ? currencies
    : column.id === 'material_code' ? materials.map(item => item.code) : null
  if (options) {
    return <select aria-label={'Фильтр: ' + column.title} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">Все</option>
      {options.map(option => <option value={option} key={option}>{label[option] ?? nameOf(materials, option)}</option>)}
    </select>
  }
  return <input aria-label={'Фильтр: ' + column.title} placeholder="Все" value={value} type={column.type ?? 'text'}
    step="any" onChange={event => onChange(event.target.value)} />
}

export function Warehouse({ user, materials, reloadCatalog }: Catalog) {
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [applied, setApplied] = useState(filters)
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ id: keyof Batch; descending: boolean }>({ id: 'id', descending: true })
  const [result, setResult] = useState<Page<Batch>>({ total: 0, items: [] })
  const [allColumns, setAllColumns] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const [form, setForm] = useState<'receipt' | 'material' | null>(null)
  const [revision, setRevision] = useState(0)
  const [selected, select] = useHash(/^#batch-(\d+)$/)
  const canEdit = user.role === 'storekeeper' || user.role === 'manager'
  const visible = columns.filter(column => allColumns || column.core)

  useEffect(() => {
    const timer = setTimeout(() => { setApplied(filters); setPage(0) }, 250)
    return () => clearTimeout(timer)
  }, [filters])
  useEffect(() => {
    let active = true
    setError('')
    setBusy(true)
    const options = new URLSearchParams({
      sort: sort.id, descending: String(sort.descending), offset: String(page * pageSize), limit: String(pageSize),
    })
    for (const [key, value] of Object.entries(applied)) if (value) options.set(key, value)
    api<Page<Batch>>('/batches?' + options).then(value => { if (active) setResult(value) })
      .catch(err => { if (active) setError(errorText(err)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [applied, page, sort, revision])

  const updated = () => setRevision(previous => previous + 1)
  const toggleColumns = () => {
    // Filters on columns that are about to disappear would keep narrowing the list invisibly.
    if (allColumns) setFilters(previous => Object.fromEntries(Object.entries(previous)
      .filter(([key]) => columns.find(column => column.id === key)?.core)))
    setAllColumns(!allColumns)
  }
  const { total, items } = result
  return <>
    <PageHead title="Партии сырья" description="Поступления от поставщиков, проверка качества и остатки по каждой партии">
      {canEdit && <>
        <button className="secondary" onClick={() => setForm('material')}>Новый материал</button>
        <button onClick={() => setForm('receipt')}>Оформить поступление</button>
      </>}
    </PageHead>
    {form && <Drawer title={form === 'receipt' ? 'Новое поступление' : 'Новый материал'} close={() => setForm(null)}>
      {form === 'receipt'
        ? <ReceiptForm user={user} materials={materials} done={() => { setForm(null); setPage(0); updated() }} />
        : <MaterialForm done={() => { setForm(null); void reloadCatalog() }} />}
    </Drawer>}
    {selected && <BatchCard key={selected} id={selected} canEdit={canEdit} materials={materials} updated={updated}
      close={() => select(null)} />}
    <ErrorNote error={error} />
    <section className="panel" aria-label="Реестр партий" aria-busy={busy}>
      <div className="panel-toolbar">
        <strong>Реестр партий <span className="count">{total}</span></strong>
        <div className="actions">
          <button className="secondary compact" onClick={toggleColumns} aria-pressed={allColumns}>
            {allColumns ? 'Основные столбцы' : 'Все столбцы'}</button>
          <button className="secondary compact" disabled={!Object.values(filters).some(Boolean)}
            onClick={() => setFilters({})}>Сбросить фильтры</button>
        </div>
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Таблица партий">
        <table className="batch-table">
          <thead>
            <tr>{visible.map(column => <th key={column.id}
              aria-sort={sort.id === column.id ? (sort.descending ? 'descending' : 'ascending') : 'none'}>
              <button onClick={() => { setSort({ id: column.id, descending: sort.id === column.id && !sort.descending }); setPage(0) }}>
                {column.title}
                <span className="sort" aria-hidden="true">{sort.id === column.id ? (sort.descending ? '↓' : '↑') : ''}</span>
              </button>
            </th>)}</tr>
            <tr>{visible.map(column => <th key={column.id} className="filter">
              <ColumnFilter column={column} value={filters[column.id] ?? ''} materials={materials}
                onChange={value => setFilters(previous => ({ ...previous, [column.id]: value }))} />
            </th>)}</tr>
          </thead>
          <tbody>{items.map(batch => <tr key={batch.id}>{visible.map(column => <td key={column.id}>
            {column.id === 'id'
              ? <a href={'#batch-' + batch.id} onClick={() => { setForm(null); select(batch.id) }}>#{batch.id}</a>
              : batchValue(batch, column, materials)}
          </td>)}</tr>)}</tbody>
        </table>
      </div>
      {!items.length && <p className="empty">{busy ? 'Загружаем партии…' : 'По этим фильтрам партий нет'}</p>}
      <Pagination page={page} size={pageSize} total={total} onChange={setPage} />
    </section>
  </>
}

function Pagination({ page, size, total, onChange }: { page: number; size: number; total: number; onChange: (page: number) => void }) {
  if (total <= size) return null
  return <div className="pagination">
    <span>{page * size + 1}–{Math.min((page + 1) * size, total)} из {total}</span>
    <div className="actions">
      <button className="secondary compact" disabled={page === 0} onClick={() => onChange(page - 1)}>Назад</button>
      <button className="secondary compact" disabled={(page + 1) * size >= total} onClick={() => onChange(page + 1)}>Далее</button>
    </div>
  </div>
}

/* Production --------------------------------------------------------------- */

function RecipeForm({ materials, done }: { materials: Material[]; done: () => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = formData(event)
    const ingredients = Object.fromEntries(materials.flatMap(material => {
      const value = data.get(material.code)
      return value ? [[material.code, value]] : []
    }))
    setBusy(true)
    try { await api('/recipes', { name: data.get('recipe_name'), ingredients }); done() }
    catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <form className="edit-form" onSubmit={submit}>
    <Field label="Название рецепта"><input name="recipe_name" required maxLength={160} placeholder="Бергамот" /></Field>
    <fieldset><legend>Нормы на одну свечу</legend>
      <p className="hint">Заполните только те материалы, которые входят в рецепт.</p>
      <div className="form-row">
        {materials.map(material => <Field key={material.code} label={`${material.name}, ${material.unit}`}>
          <input type="number" name={material.code} min="0.000001" step="0.000001" />
        </Field>)}
      </div>
    </fieldset>
    <ErrorNote error={error} />
    <div className="form-actions"><button disabled={busy}>Сохранить рецепт</button></div>
  </form>
}

function PlanForm({ recipes, today, done }: { recipes: Recipe[]; today: string; done: (id: number) => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    const data = Object.fromEntries(formData(event))
    const intent = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null
    setBusy(true)
    try {
      const job = await api<Job>(intent?.value === 'plan' ? '/jobs' : '/jobs/drafts', { ...data, recipe_id: Number(data.recipe_id) })
      done(job.id)
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <form className="edit-form" onSubmit={submit}>
    <div className="form-row">
      <Field label="Рецепт"><select name="recipe_id" required defaultValue="">
        <option value="" disabled>Выберите рецепт</option>
        {recipes.map(recipe => <option value={recipe.id} key={recipe.id}>{recipe.name}</option>)}
      </select></Field>
      <Field label="Количество свечей"><input name="quantity" type="number" min="1" step="1" required /></Field>
      <Field label="Дата производства"><input name="planned_on" type="date" defaultValue={today} required /></Field>
    </div>
    <p className="hint">Черновик ничего не резервирует. При планировании сырьё резервируется по FIFO; если его не хватает,
      задание сохранится с дефицитом, но запустить его будет нельзя.</p>
    <ErrorNote error={error} />
    <div className="form-actions">
      <button className="secondary" disabled={busy} value="draft">Сохранить черновик</button>
      <button disabled={busy} value="plan">Запланировать и зарезервировать</button>
    </div>
  </form>
}

function jobStatus(job: Job) {
  return job.status === 'planned' && job.components.some(row => row.signal !== 'green') ? 'shortage' : job.status
}

const reserveLabels = { green: 'Полный резерв', yellow: 'Частичный резерв', red: 'Нет резерва' }
const costLabels: Record<string, string> = {
  actual: 'Фактическая себестоимость', issued: 'Стоимость выданного сырья', reserved: 'Стоимость резерва',
}

function Coverage({ job }: { job: Job }) {
  if (!['draft', 'planned'].includes(job.status)) return <span className="muted">—</span>
  if (job.status === 'draft') return <span className="muted">не резервируется</span>
  const ready = job.components.filter(row => row.signal === 'green').length
  return <span className="coverage" title={`${ready} из ${job.components.length} материалов в полном резерве`}>
    {job.components.map(row => <span key={row.code} className={'signal ' + row.signal} />)}
    <span>{ready}/{job.components.length}</span>
  </span>
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
      setJob(await api<Job>('/jobs/' + id + '/' + action, body))
      updated()
      setCost(await api('/jobs/' + id + '/cost'))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  const shortage = job && jobStatus(job) === 'shortage'
  const lastColumn = job?.status === 'completed' ? 'Израсходовано' : job?.status === 'in_progress' ? 'Выдано в цех' : 'Не хватает'
  return <Drawer title={job ? `Задание #${id} · ${job.recipe}` : `Задание #${id}`} close={close}>
    <ErrorNote error={error} />
    {!job ? !error && <p className="muted" role="status">Загружаем задание…</p> : <>
      <div className="card-status">
        <Status value={jobStatus(job)} />
        <span>{quantity(job.quantity)} свечей · {day(job.planned_on)}</span>
      </div>
      {cost && <p className="cost">{costLabels[cost.kind] ?? 'Себестоимость'}: <strong>{quantity(cost.total_rub)} ₽</strong></p>}
      <div className="table-wrap"><table className="requirements-table">
        <thead><tr><th>Материал</th><th>Нужно</th><th>В резерве</th><th>{lastColumn}</th></tr></thead>
        <tbody>{job.components.map(component => {
          const last = job.status === 'completed' ? component.consumed : job.status === 'in_progress' ? component.issued : component.missing
          return <tr key={component.code} className={job.status === 'planned' && component.signal !== 'green' ? 'short' : undefined}>
            <td><strong>{component.name}</strong>
              {job.status === 'planned' && <small className={'reserve-label reserve-' + component.signal}>
                <span className={'signal ' + component.signal} aria-hidden="true" />{reserveLabels[component.signal]}
              </small>}</td>
            <td>{quantity(component.required)} {component.unit}</td>
            <td>{quantity(component.reserved)} {component.unit}</td>
            <td>{Number(last) || job.status !== 'planned' ? `${quantity(last)} ${component.unit}` : '—'}</td>
          </tr>
        })}</tbody>
      </table></div>
      {shortage && <p className="hint warn">Запуск возможен только при полном резерве. Дождитесь поступления недостающего
        сырья или отмените задание и запланируйте заново.</p>}
      {canEdit && ['draft', 'planned'].includes(job.status) && <div className="form-actions">
        <button className="secondary danger" disabled={busy} onClick={() => change('cancel')}>Отменить задание</button>
        {job.status === 'draft' && <button disabled={busy} onClick={() => change('plan')}>Запланировать и зарезервировать</button>}
        {job.status === 'planned' && <button disabled={busy || Boolean(shortage)} onClick={() => change('start')}>
          Запустить в производство</button>}
      </div>}
      {canEdit && job.status === 'in_progress' && <form className="edit-form" onSubmit={event => {
        void change('complete', { actual: Object.fromEntries(formData(event)) })
      }}>
        <fieldset><legend>Фактический расход</legend>
          <p className="hint">Экономия вернётся в исходные партии, перерасход спишется со свободного остатка по FIFO.</p>
          <div className="form-row">
            {job.components.map(component => <Field key={component.code} label={`${component.name}, ${component.unit}`}>
              <input name={component.code} type="number" min="0" step="0.000001" defaultValue={component.required} required />
            </Field>)}
          </div>
        </fieldset>
        <div className="form-actions"><button disabled={busy}>Завершить задание</button></div>
      </form>}
    </>}
  </Drawer>
}

const statusTabs = [
  { id: '', title: 'Все' }, { id: 'draft', title: 'Черновики' }, { id: 'planned', title: 'Запланированы' },
  { id: 'in_progress', title: 'В работе' }, { id: 'completed', title: 'Завершены' }, { id: 'cancelled', title: 'Отменены' },
]
const jobPageSize = 20

export function Production({ user, materials, recipes, initialJob, reloadCatalog }:
  Catalog & { recipes: Recipe[]; initialJob: number | null }) {
  const [result, setResult] = useState<Page<Job>>({ total: 0, items: [] })
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(0)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [form, setForm] = useState<'job' | 'recipe' | null>(null)
  const [selected, setSelected] = useState<number | null>(initialJob)
  const canEdit = user.role !== 'storekeeper'
  const updated = () => setRevision(previous => previous + 1)
  useEffect(() => {
    let active = true
    setBusy(true)
    const options = new URLSearchParams({ offset: String(page * jobPageSize), limit: String(jobPageSize) })
    if (status) options.set('status', status)
    api<Page<Job>>('/jobs?' + options).then(value => { if (active) setResult(value) })
      .catch(err => { if (active) setError(errorText(err)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [revision, page, status])
  return <>
    <PageHead title="Производственные задания" description="Планирование выпуска, резерв сырья, запуск в цех и фактический расход">
      {canEdit && <>
        <button className="secondary" onClick={() => setForm('recipe')}>Новый рецепт</button>
        <button onClick={() => setForm('job')}>Новое задание</button>
      </>}
    </PageHead>
    {form && <Drawer title={form === 'job' ? 'Новое задание' : 'Новый рецепт'} close={() => setForm(null)}>
      {form === 'job'
        ? <PlanForm today={user.today} recipes={recipes} done={id => { setForm(null); setSelected(id); setPage(0); updated() }} />
        : <RecipeForm materials={materials} done={() => { setForm(null); void reloadCatalog() }} />}
    </Drawer>}
    {selected && <JobCard id={selected} key={selected} updated={updated} canEdit={canEdit} close={() => setSelected(null)} />}
    <ErrorNote error={error} />
    <section className="panel" aria-busy={busy}>
      <div className="tabs" role="tablist" aria-label="Статус задания">
        {statusTabs.map(tab => <button key={tab.id} role="tab" aria-selected={status === tab.id}
          className={status === tab.id ? 'active' : ''} onClick={() => { setStatus(tab.id); setPage(0) }}>{tab.title}</button>)}
      </div>
      <div className="table-wrap"><table className="jobs-table">
        <thead><tr><th>Задание</th><th>Рецепт</th><th>Количество</th><th>Дата</th><th>Резерв сырья</th><th>Статус</th></tr></thead>
        <tbody>{result.items.map(job => <tr key={job.id}>
          <td><button className="link" onClick={() => { setForm(null); setSelected(job.id) }}>#{job.id}</button></td>
          <td>{job.recipe}</td>
          <td>{quantity(job.quantity)} шт</td>
          <td>{day(job.planned_on)}</td>
          <td><Coverage job={job} /></td>
          <td><Status value={jobStatus(job)} /></td>
        </tr>)}</tbody>
      </table></div>
      {!result.items.length && <p className="empty">{busy ? 'Загружаем задания…' : 'Заданий с таким статусом нет'}</p>}
      <Pagination page={page} size={jobPageSize} total={result.total} onChange={setPage} />
    </section>
  </>
}
