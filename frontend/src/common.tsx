import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { label } from './api'

export function ErrorNote({ error }: { error: string }) {
  return error ? <p className="error" role="alert">{error}</p> : null
}

export function Status({ value }: { value: string }) {
  return <span className={'status status-' + value}><span />{label[value] ?? value}</span>
}

export function Drawer({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const section = useRef<HTMLElement>(null)
  useEffect(() => {
    section.current?.focus({ preventScroll: true })
    section.current?.scrollIntoView({ block: 'nearest' })
  }, [])
  return <section className="drawer" ref={section} tabIndex={-1} aria-label={title}>
    <div className="section-head"><h2>{title}</h2><button className="secondary" onClick={close}>Закрыть</button></div>
    {children}
  </section>
}

export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить запрос'
