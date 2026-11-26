import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { label } from './api'

export function ErrorNote({ error }: { error: string }) {
  return error ? <p className="error" role="alert">{error}</p> : null
}

export function Status({ value }: { value: string }) {
  return <span className={'status status-' + value}><span />{label[value] ?? value}</span>
}

export function PageHead({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <header className="page-head">
    <div><h1>{title}</h1><p>{description}</p></div>
    {children && <div className="actions">{children}</div>}
  </header>
}

/** Side panel over the page: closes on Esc or backdrop click and returns focus to the opener. */
export function Drawer({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const panel = useRef<HTMLElement>(null)
  const onClose = useRef(close)
  useEffect(() => { onClose.current = close })
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    panel.current?.focus({ preventScroll: true })
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose.current() }
    document.addEventListener('keydown', onKey)
    document.body.classList.add('drawer-open')
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.classList.remove('drawer-open')
      opener?.focus({ preventScroll: true })
    }
  }, [])
  return <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) close() }}>
    <section className="drawer" ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}>
      <header className="drawer-head">
        <h2>{title}</h2>
        <button className="icon-button" onClick={close} aria-label="Закрыть">×</button>
      </header>
      <div className="drawer-body">{children}</div>
    </section>
  </div>
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить запрос'
