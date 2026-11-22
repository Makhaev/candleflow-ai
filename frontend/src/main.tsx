import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { api, day, label } from './api'
import type { Material, Recipe, User } from './api'
import { Warehouse, Production } from './inventory'
import { Chat } from './chat'
import { ErrorNote, errorText } from './common'
import './styles.css'

type View = 'batches' | 'jobs' | 'chat'
const nav: { id: View; icon: string; title: string }[] = [
  { id: 'batches', icon: '▤', title: 'Партии сырья' },
  { id: 'jobs', icon: '▦', title: 'Задания' },
  { id: 'chat', icon: '✦', title: 'Ассистент' },
]

function Brand() {
  return <div className="brand"><span className="brand-icon" aria-hidden="true">◈</span>
    <div>CandleFlow<small>Мастерская свечей</small></div></div>
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [login, setLogin] = useState('manager')
  const [password, setPassword] = useState('demo-candleflow')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api('/login', { login, password })
      onLogin(await api<User>('/me'))
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <main className="login"><form className="login-panel" onSubmit={submit}>
    <Brand />
    <h1>Вход в мастерскую</h1>
    <label>Демо-роль<select value={login} onChange={event => setLogin(event.target.value)}>
      {Object.entries(label).slice(0, 3).map(([id, name]) => <option key={id} value={id === 'storekeeper' ? 'warehouse' : id}>{name}</option>)}
    </select></label>
    <label>Пароль<input type="password" autoComplete="current-password" value={password}
      onChange={event => setPassword(event.target.value)} required /></label>
    <ErrorNote error={error} />
    <button disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button>
  </form></main>
}

function Workspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [view, setView] = useState<View>('batches')
  const [materials, setMaterials] = useState<Material[]>([])
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [error, setError] = useState('')
  const [selectedJob, setSelectedJob] = useState<number | null>(null)
  const reloadCatalog = () => Promise.all([api<Material[]>('/materials'), api<Recipe[]>('/recipes')])
    .then(([m, r]) => { setMaterials(m); setRecipes(r) }).catch(err => setError(errorText(err)))
  useEffect(() => { void reloadCatalog() }, [])
  const goToJob = (id: number) => { setSelectedJob(id); setView('jobs') }
  return <div className="workspace">
    <aside className="sidebar">
      <Brand />
      <nav aria-label="Рабочие разделы">
        {nav.map(item => <button key={item.id} className={view === item.id ? 'active' : ''}
          aria-current={view === item.id ? 'page' : undefined} onClick={() => { setSelectedJob(null); setView(item.id) }}>
          <span aria-hidden="true">{item.icon}</span>{item.title}
        </button>)}
      </nav>
      <div className="account"><div className="avatar">{user.name.split(' ').map(s => s[0]).join('').slice(0, 2)}</div>
        <div><strong>{user.name}</strong><small>{label[user.role]}</small></div>
      </div>
      <button className="logout" onClick={() => api('/logout', {}).then(onLogout).catch(err => setError(errorText(err)))}>
        Выйти
      </button>
    </aside>
    <main className="workspace-main">
      <div className="topline"><span>{nav.find(item => item.id === view)?.title}</span><time>{day(user.today)}</time></div>
      <ErrorNote error={error} />
      {view === 'batches' && <Warehouse user={user} materials={materials} reloadCatalog={reloadCatalog} />}
      {view === 'jobs' && <Production user={user} initialJob={selectedJob} materials={materials} recipes={recipes}
        reloadCatalog={reloadCatalog} />}
      {view === 'chat' && <Chat goToJob={goToJob} />}
    </main>
  </div>
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api<User>('/me').then(setUser).catch(() => setUser(null)).finally(() => setLoading(false))
  }, [])
  if (loading) return <main className="loading">Загружаем рабочее пространство…</main>
  return user ? <Workspace user={user} onLogout={() => setUser(null)} /> : <Login onLogin={setUser} />
}

createRoot(document.getElementById('root')!).render(<App />)
