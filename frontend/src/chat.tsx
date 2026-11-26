import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, day, label, quantity, stream } from './api'
import type { Conversation, Fact, Job, Message, PreviewComponent, StreamEvent } from './api'
import { ErrorNote, errorText, PageHead } from './common'

const executionStatus: Record<string, string> = {
  complete: 'Выполнен', completed: 'Выполнен', interrupted: 'Прерван',
  failed: 'Ошибка', incomplete: 'Прерван', error: 'Ошибка', unknown: 'Статус недоступен',
}

const examples = [
  'Какой общий и свободный остаток соевого воска?',
  'Чего не хватает на задания этой недели?',
  'Запланируй 20 свечей Лаванда на пятницу',
  'Построй график расхода соевого воска по неделям',
]

function PlanPreview({ rows }: { rows: PreviewComponent[] }) {
  const short = rows.filter(row => Number(row.missing) > 0)
  return <>
    <div className="table-wrap"><table className="preview-table">
      <caption className="sr-only">Что будет зарезервировано</caption>
      <thead><tr><th>Материал</th><th>Нужно</th><th>Свободно</th><th>Не хватит</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.code} className={Number(row.missing) > 0 ? 'short' : undefined}>
        <td>{row.name}</td>
        {[row.required, row.free, row.missing].map((value, index) =>
          <td key={index}>{Number(value) || index < 2 ? quantity(value) + ' ' + row.unit : '—'}</td>)}
      </tr>)}</tbody>
    </table></div>
    <p className={short.length ? 'preview-note warn' : 'preview-note'}>
      {short.length ? 'Резерв будет частичным: запустить задание можно будет только после поступления недостающего сырья.'
        : 'Всё сырьё есть в свободном остатке, задание получит полный резерв.'}
    </p>
  </>
}

function Answer({ answer, confirm, goToJob, busy }: { answer: Message; confirm: (id: number) => void;
  goToJob: (id: number) => void; busy: boolean }) {
  if (answer.role === 'user') return <article className="bubble user-message">{answer.body}</article>
  const proposal = answer.proposal
  const expired = proposal && new Date(proposal.expires).getTime() <= Date.now()
  return <article className="assistant-message">
    <span className="assistant-avatar" aria-hidden="true">✦</span>
    <div className="answer-content">
      <div className="bubble">{answer.body ? answer.body : answer.status === 'streaming' ? 'Готовлю ответ…' : 'Ответ прерван'}</div>
      {answer.status === 'interrupted' && <span className="message-status">Ответ прерван</span>}
      {answer.status === 'error' && <span className="message-status">Ошибка генерации</span>}
      {answer.attempts.length > 1 && <span className="message-status">
        {answer.status === 'streaming' ? 'Новая попытка с запасной моделью…'
          : 'Попытки · ' + answer.attempts.join(' → ')}
      </span>}
      {proposal && <section className="proposal">
        <h3>{proposal.action === 'plan_job' ? 'Планирование задания' : 'Сохранение предпочтения'}</h3>
        {proposal.action === 'plan_job'
          ? <><p>{proposal.recipe} · {quantity(proposal.args.quantity)} шт · {day(proposal.args.planned_on)}</p>
            {!proposal.job_id && proposal.preview && <PlanPreview rows={proposal.preview} />}</>
          : <p>{proposal.args.key === 'currency' ? 'Валюта' : 'Цех'} · {proposal.args.value}</p>}
        {proposal.job_id ? <button className="secondary" onClick={() => goToJob(proposal.job_id!)}>
          Открыть задание #{proposal.job_id}
        </button> : proposal.confirmed_at ? <p>Предпочтение сохранено</p>
          : <button disabled={busy || Boolean(expired)} onClick={() => confirm(answer.id)}>
          {expired ? 'Срок подтверждения истёк' : busy ? 'Подтверждаем…' : 'Подтвердить'}
        </button>}
      </section>}
      {answer.files.map(file => <div className="artifact" key={file.id}>
        {file.mime === 'image/png' && <img src={'/api/files/' + file.id} alt={file.filename} />}
        <a href={'/api/files/' + file.id} download>{file.filename}</a>
      </div>)}
      {(answer.model || answer.tools.length > 0) && <div className="answer-meta">
        {answer.model && <span title={answer.model}>{answer.model === 'demo' ? 'Демо-модель' : answer.model}</span>}
        {[...new Set(answer.tools)].map(name => <span key={name}>{label[name] ?? name}</span>)}
      </div>}
      {answer.tool_calls.length > 0 && <details className="tool-log">
        <summary>Вызовы инструментов · {answer.tool_calls.length}</summary>
        <ol>{answer.tool_calls.map(call => <li key={call.ordinal}>
          <strong>{label[call.name] ?? call.name}</strong>
          <span>{executionStatus[call.status] ?? call.status}</span>
          {call.model && <span>{call.model}</span>}
        </li>)}</ol>
      </details>}
      {answer.code.length > 0 && <details className="code">
        <summary>Выполненный код</summary>{answer.code.map((code, index) => <pre key={index}>{code}</pre>)}
      </details>}
    </div>
  </article>
}

export function Chat({ goToJob }: { goToJob: (id: number) => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [active, setActive] = useState<number | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [facts, setFacts] = useState<Fact[]>([])
  const [loadingFacts, setLoadingFacts] = useState(false)
  const [deletingFact, setDeletingFact] = useState<number | null>(null)
  const [stopping, setStopping] = useState(false)
  const [memory, showMemory] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState<number | null>(null)
  const controller = useRef<AbortController | null>(null)
  const activeAnswer = useRef<number | null>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const draftInput = useRef<HTMLTextAreaElement>(null)
  const report = (err: unknown) => setError(errorText(err))
  const refreshList = () => api<Conversation[]>('/conversations').then(setConversations)
  const refreshFacts = () => api<Fact[]>('/memory').then(setFacts)
  async function toggleMemory() {
    showMemory(!memory)
    if (memory) return
    setError('')
    setLoadingFacts(true)
    try { await refreshFacts() } catch (err) { report(err) } finally { setLoadingFacts(false) }
  }

  async function deleteFact(id: number) {
    setDeletingFact(id)
    setError('')
    try {
      await api('/memory/' + id, undefined, 'DELETE')
      await refreshFacts()
    } catch (err) { report(err) } finally { setDeletingFact(null) }
  }

  async function stop() {
    setStopping(true)
    try {
      const id = activeAnswer.current
      if (id) await api('/messages/' + id + '/stop', {})
      else controller.current?.abort()
    } catch (err) { report(err); setStopping(false) }
  }
  const load = (key: number) => api<{ messages: Message[] }>('/conversations/' + key)
  useEffect(() => {
    let mounted = true
    api<Conversation[]>('/conversations').then(items => {
      if (mounted) setConversations(items)
    }).catch(err => { if (mounted) report(err) })
    return () => { mounted = false; controller.current?.abort() }
  }, [])
  useEffect(() => {
    const node = transcript.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages])

  async function open(key: number) {
    setError('')
    setBusy(true)
    try {
      setMessages((await load(key)).messages)
      setActive(key)
    } catch (err) { report(err) } finally { setBusy(false) }
  }

  function clearFile() {
    setFile(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const question = draft.trim()
    if (!question || busy) return
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setError('')
    let key = active
    let answerId = -2
    const placeholder: Message = { id: answerId, body: '', role: 'assistant', status: 'streaming', model: null,
      tools: [], tool_calls: [], attempts: [], code: [], files: [], proposal: null }
    try {
      if (!key) {
        const created = await api<Conversation>('/conversations', {})
        key = created.id
        setActive(key)
      }
      setMessages(previous => [...previous, { ...placeholder, id: -1, role: 'user', body: question }, placeholder])
      setDraft('')
      clearFile()
      function receive(update: StreamEvent) {
        if (update.type === 'start') {
          setMessages(previous => previous.map(row => row.id === -2 ? { ...row, id: update.id } : row))
          answerId = update.id
          activeAnswer.current = update.id
        } else if (update.type === 'delta') {
          setMessages(previous => previous.map(row => row.id === answerId ? { ...row, body: row.body + update.text } : row))
        } else if (update.type === 'reset') {
          setMessages(previous => previous.map(row => row.id === answerId ? { ...row, body: update.text,
            attempts: update.retry ? ['primary', 'fallback'] : row.attempts } : row))
        } else if (update.type === 'tool') {
          setMessages(previous => previous.map(row => row.id === answerId ? { ...row, tools: [...row.tools, update.name] } : row))
        } else {
          setMessages(previous => previous.map(row => row.id === answerId ? update.message : row))
        }
      }
      await stream(key, question, file, abort.signal, receive)
    } catch (err) {
      if (!abort.signal.aborted) report(err)
    } finally {
      if (key) {
        try {
          let result = await load(key)
          for (let attempt = 0; abort.signal.aborted && attempt < 8
               && result.messages.at(-1)?.status === 'streaming'; attempt++) {
            await new Promise(resolve => setTimeout(resolve, 125))
            result = await load(key)
          }
          setMessages(result.messages)
          await refreshList()
          if (memory) await refreshFacts()
        } catch (err) { if (!abort.signal.aborted) report(err) }
      }
      activeAnswer.current = null
      controller.current = null
      setStopping(false)
      setBusy(false)
    }
  }

  async function confirm(id: number) {
    setError('')
    setConfirming(id)
    try {
      const result = await api<Job | { confirmed: true }>('/messages/' + id + '/confirm', {})
      setMessages(previous => previous.map(row => row.id === id && row.proposal
        ? { ...row, proposal: { ...row.proposal, job_id: 'id' in result ? result.id : null,
            confirmed_at: new Date().toISOString() } } : row))
      if (memory) await refreshFacts()
    } catch (err) { report(err) } finally { setConfirming(null) }
  }

  return <>
    <PageHead title="Ассистент" description="Спросите про остатки, дефициты и себестоимость или попросите запланировать задание. Изменения выполняются только после вашего подтверждения.">
      <button className="secondary" onClick={toggleMemory} aria-expanded={memory}>
        {memory ? 'Скрыть память' : 'Память ассистента'}
      </button>
    </PageHead>
    {memory && <section className="panel memory-panel"><h2>Что ассистент помнит о вас</h2>
      <p className="muted">Скажите ассистенту, что запомнить, например «Запомни, считать в евро».</p>
      {loadingFacts ? <p className="muted" role="status">Загружаем предпочтения…</p>
        : facts.length ? <ul className="memory-list">{facts.map(fact => <li key={fact.id}>
        <span>{fact.key === 'currency' ? 'Валюта' : 'Цех'} · {fact.value}</span>
        <button className="secondary compact" disabled={deletingFact !== null}
          onClick={() => deleteFact(fact.id)}>{deletingFact === fact.id ? 'Удаляем…' : 'Удалить'}</button>
      </li>)}</ul> : <p>Нет сохранённых предпочтений.</p>}
    </section>}
    <ErrorNote error={error} />
    <section className="chat-panel panel">
      <aside className="chat-history">
        <button className="secondary" disabled={busy} onClick={() => {
          setActive(null); setMessages([]); setDraft(''); clearFile(); setError(''); draftInput.current?.focus()
        }}>Новый диалог</button>
        <div className="chat-history-list" aria-label="История диалогов">
          {conversations.map(item => <button key={item.id} className={active === item.id ? 'selected' : ''}
            disabled={busy} aria-current={active === item.id ? 'true' : undefined}
            onClick={() => open(item.id)} title={item.title}>{item.title}</button>)}
        </div>
      </aside>
      <div className="chat-content">
        <div className="transcript" ref={transcript} aria-label="Сообщения" aria-live="polite">
          {messages.length ? messages.map(answer => <Answer answer={answer} key={answer.id}
            busy={confirming === answer.id} confirm={confirm} goToJob={goToJob} />) : <div className="chat-empty">
            <span className="assistant-avatar" aria-hidden="true">✦</span><h2>Что проверим сегодня?</h2>
            <div className="examples">{examples.map(question => <button className="secondary" key={question}
              disabled={busy} onClick={() => { setDraft(question); draftInput.current?.focus() }}>{question}</button>)}</div>
          </div>}
        </div>
        <form className="composer" onSubmit={submit}>
          <label className="sr-only" htmlFor="message">Сообщение</label>
          <textarea ref={draftInput} value={draft} id="message" maxLength={5000} rows={3}
            placeholder="Спросите о производстве или остатках…" onChange={event => setDraft(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault(); event.currentTarget.form?.requestSubmit()
              }
            }} />
          <div className="composer-actions">
            <input ref={fileInput} type="file" id="upload" className="sr-only" accept=".xlsx,.csv" disabled={busy}
              onChange={event => setFile(event.target.files?.[0] ?? null)} />
            <button type="button" className="secondary compact" disabled={busy} onClick={() => fileInput.current?.click()}>
              Прикрепить CSV или XLSX
            </button>
            {file && <button className="file-chip" type="button" onClick={clearFile} title="Удалить вложение">{file.name} ×</button>}
            {busy && controller.current ? <button type="button" className="secondary send" disabled={stopping} onClick={stop}>
              {stopping ? 'Останавливаем…' : 'Остановить'}
            </button> : <button className="send" disabled={busy || !draft.trim()}>Отправить</button>}
          </div>
        </form>
      </div>
    </section>
  </>
}
