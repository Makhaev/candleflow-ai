import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, day, label, quantity, stream } from './api'
import type { Conversation, Fact, Job, Message, StreamEvent } from './api'
import { ErrorNote, errorText } from './common'

const examples = [
  'Какой общий и свободный остаток соевого воска?',
  'Чего не хватает на задания этой недели?',
  'Запланируй 20 свечей Лаванда на пятницу',
  'Построй график расхода соевого воска по неделям',
]

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
          ? <p>{proposal.recipe} · {quantity(proposal.args.quantity)} шт · {day(proposal.args.planned_on)}</p>
          : <p>{proposal.args.key === 'currency' ? 'Валюта' : 'Цех'} · {proposal.args.value}</p>}
        {proposal.job_id ? <button className="secondary" onClick={() => goToJob(proposal.job_id!)}>
          Открыть задание #{proposal.job_id}
        </button> : proposal.confirmed_at ? <p>Предпочтение сохранено</p>
          : <button disabled={busy || Boolean(expired)} onClick={() => confirm(answer.id)}>
          {expired ? 'Срок подтверждения истёк' : busy ? 'Подтверждаем…' : 'Подтвердить'}
        </button>}
      </section>}
      {answer.files.map(file => <div className="artifact" key={file.id}>
        {file.mime === 'image/png' && <img src={'/api/artifacts/' + file.id} alt={file.filename} />}
        <a href={'/api/artifacts/' + file.id} download>{file.filename}</a>
      </div>)}
      {(answer.model || answer.tools.length > 0) && <div className="answer-meta">
        {answer.model && <span>{answer.model === 'demo' ? 'Демо-модель' : answer.model}</span>}
        {answer.tools.map((name, index) => <span key={name + index}>{label[name] ?? name}</span>)}
      </div>}
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
      tools: [], attempts: [], code: [], files: [], proposal: null }
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
    <header className="page-head"><div><h1>Ассистент мастерской</h1>
      <p>Остатки, дефициты, себестоимость и планирование</p></div>
      <button className="secondary" onClick={() => { showMemory(!memory); if (!memory) refreshFacts().catch(report) }}>
        {memory ? 'Скрыть память' : 'Память'}
      </button>
    </header>
    {memory && <section className="drawer"><h2>Мои предпочтения</h2>
      <p className="muted">Скажите ассистенту, что запомнить, например «Запомни, считать в евро».</p>
      {facts.length ? <ul className="memory-list">{facts.map(fact => <li key={fact.id}>
        <span>{fact.key === 'currency' ? 'Валюта' : 'Цех'} · {fact.value}</span>
        <button className="secondary compact" onClick={() => api('/memory/' + fact.id, undefined, 'DELETE')
          .then(refreshFacts).catch(report)}>Удалить</button>
      </li>)}</ul> : <p>Нет сохранённых предпочтений.</p>}
    </section>}
    <ErrorNote error={error} />
    <section className="chat-panel panel">
      <aside className="chat-history">
        <button className="secondary" disabled={busy} onClick={() => {
          setActive(null); setMessages([]); setDraft(''); clearFile(); setError(''); draftInput.current?.focus()
        }}>Новый диалог</button>
        <div className="chat-history-list">
          {conversations.map(item => <button key={item.id} className={active === item.id ? 'selected' : ''}
            disabled={busy} onClick={() => open(item.id)} title={item.title}>{item.title}</button>)}
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
            {busy && controller.current ? <button type="button" className="secondary send" onClick={() => {
              const id = activeAnswer.current
              if (id) api('/messages/' + id + '/stop', {}).catch(report)
              else controller.current?.abort()
            }}>
              Остановить
            </button> : <button className="send" disabled={busy || !draft.trim()}>Отправить</button>}
          </div>
        </form>
      </div>
    </section>
  </>
}
