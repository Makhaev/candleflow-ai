import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { api, ApiError, day, label } from './api'
import type { Material, Recipe, User } from './api'
import { AuditLog } from './audit'
import { Warehouse, Production } from './inventory'
import { Chat } from './chat'
import { ErrorNote, errorText } from './common'
import './styles.css'

type View = 'batches' | 'jobs' | 'chat' | 'audit'
const nav: { id: View; icon: string; title: string }[] = [
  { id: 'batches', icon: '▤', title: 'Партии сырья' },
  { id: 'jobs', icon: '▦', title: 'Задания' },
  { id: 'chat', icon: '✦', title: 'Ассистент' },
  { id: 'audit', icon: '◷', title: 'Журнал аудита' },
]
const demoAccounts: Record<User['role'], string> = {
  manager: 'manager', storekeeper: 'warehouse', technologist: 'technologist',
}
const demoPassword = 'demo-candleflow'

function Brand() {
  return <div className="brand"><span className="brand-icon" aria-hidden="true">◈</span>
    <div>CandleFlow<small>Мастерская свечей</small></div></div>
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [preset, setPreset] = useState<User['role'] | 'manual'>('manager')
  const [login, setLogin] = useState(demoAccounts.manager)
  const [password, setPassword] = useState(demoPassword)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      onLogin(await api<User>('/login', { login, password }))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }

  return <main className="login"><form className="login-panel" onSubmit={submit}>
    <Brand />
    <h1>Вход в мастерскую</h1>
    <label>Учётная запись<select value={preset} disabled={busy} onChange={event => {
      const role = event.target.value as User['role'] | 'manual'
      setPreset(role)
      setLogin(role === 'manual' ? '' : demoAccounts[role])
      setPassword(role === 'manual' ? '' : demoPassword)
    }}>
      {Object.keys(demoAccounts).map(role => <option key={role} value={role}>{label[role]} · демо</option>)}
      <option value="manual">Ввести логин</option>
    </select></label>
    {preset === 'manual' && <label>Логин<input value={login} onChange={event => setLogin(event.target.value)}
      autoComplete="username" required /></label>}
    <label>Пароль<input type="password" autoComplete="current-password" value={password}
      onChange={event => setPassword(event.target.value)} required /></label>
    <ErrorNote error={error} />
    <button disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button>
  </form></main>
}

function Workspace({ user, onLogout, onSwitch }: { user: User; onLogout: () => void; onSwitch: (user: User) => void }) {
  const [view, setView] = useState<View>('batches')
  const [materials, setMaterials] = useState<Material[]>([])
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [error, setError] = useState('')
  const [accountError, setAccountError] = useState('')
  const [accountBusy, setAccountBusy] = useState(false)
  const [selectedJob, setSelectedJob] = useState<number | null>(null)
  const reloadCatalog = () => Promise.all([api<Material[]>('/materials'), api<Recipe[]>('/recipes')])
    .then(([m, r]) => { setMaterials(m); setRecipes(r) }).catch(err => setError(errorText(err)))
  useEffect(() => { void reloadCatalog() }, [])

  function navigate(next: View, jobId: number | null = null, batchId?: number) {
    history.replaceState(null, '', location.pathname + location.search + (batchId ? '#batch-' + batchId : ''))
    setSelectedJob(jobId)
    setView(next)
    window.scrollTo({ top: 0 })
  }

  async function switchAccount(role: User['role']) {
    if (role === user.role) return
    setAccountBusy(true)
    setAccountError('')
    try {
      const next = await api<User>('/login', { login: demoAccounts[role], password: demoPassword })
      history.replaceState(null, '', location.pathname + location.search)
      window.scrollTo({ top: 0 })
      onSwitch(next)
    } catch (err) { setAccountError(errorText(err)) } finally { setAccountBusy(false) }
  }

  async function logout() {
    setAccountBusy(true)
    setAccountError('')
    try {
      await api('/logout', {})
      history.replaceState(null, '', location.pathname + location.search)
      onLogout()
    } catch (err) { setAccountError(errorText(err)) } finally { setAccountBusy(false) }
  }

  return <div className="workspace">
    <a className="skip-link" href="#workspace-content">К основному содержанию</a>
    <aside className="sidebar">
      <Brand />
      <nav aria-label="Рабочие разделы">
        {nav.filter(item => item.id !== 'audit' || user.role === 'manager').map(item =>
          <button key={item.id} className={view === item.id ? 'active' : ''}
            aria-current={view === item.id ? 'page' : undefined}
            onClick={() => { if (view !== item.id) navigate(item.id) }}>
            <span aria-hidden="true">{item.icon}</span>{item.title}
          </button>)}
      </nav>
      <div className="account-tools">
        <div className="account"><div className="avatar" aria-hidden="true">
          {user.name.split(' ').map(part => part[0]).join('').slice(0, 2)}
        </div><div><strong>{user.name}</strong><small>{label[user.role]}</small></div></div>
        <div className="account-actions">
          <label><span className="sr-only">Переключить демо-роль</span>
            <select value={user.role} onChange={event => void switchAccount(event.target.value as User['role'])} disabled={accountBusy}>
              {Object.keys(demoAccounts).map(role => <option key={role} value={role}>{label[role]}</option>)}
            </select>
          </label>
          <button className="logout" disabled={accountBusy} onClick={logout}>Выйти</button>
        </div>
        <ErrorNote error={accountError} />
      </div>
    </aside>
    <main className="workspace-main" id="workspace-content" tabIndex={-1}>
      <div className="topline"><time dateTime={user.today}>{day(user.today)}</time></div>
      <ErrorNote error={error} />
      {view === 'batches' && <Warehouse user={user} materials={materials} reloadCatalog={reloadCatalog} />}
      {view === 'jobs' && <Production user={user} initialJob={selectedJob} materials={materials} recipes={recipes}
        reloadCatalog={reloadCatalog} />}
      {view === 'chat' && <Chat goToJob={id => navigate('jobs', id)} />}
      {view === 'audit' && user.role === 'manager' && <AuditLog goToBatch={id => navigate('batches', null, id)}
        goToJob={id => navigate('jobs', id)} />}
    </main>
  </div>
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  function loadSession() {
    setLoading(true)
    setError('')
    api<User>('/me').then(setUser).catch((err: unknown) => {
      if (!(err instanceof ApiError) || err.status !== 401) setError(errorText(err))
      setUser(null)
    }).finally(() => setLoading(false))
  }
  useEffect(loadSession, [])
  if (loading) return <main className="loading" role="status">Загружаем рабочее пространство…</main>
  if (error) return <main className="login"><section className="login-panel">
    <Brand /><ErrorNote error={error} /><button onClick={loadSession}>Повторить загрузку</button>
  </section></main>
  return user ? <Workspace key={user.id} user={user} onSwitch={setUser} onLogout={() => setUser(null)} />
    : <Login onLogin={setUser} />
}

createRoot(document.getElementById('root')!).render(<App />)
