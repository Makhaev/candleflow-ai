export type User = { id: number; name: string; role: 'warehouse' | 'technologist' | 'manager'; today: string }
export type Material = { code: string; name: string; unit: string }
export type Recipe = { id: number; name: string }
export type Batch = {
  id: number; material_code: string; supplier: string; received_on: string;
  status: string; received: string; quantity: string; reserved: string;
  unit_price: string; currency: string; unit_cost: string; exchange_rate: string; comment: string;
}
export type Audit = { id: number; actor: string; at: string; before: Record<string, unknown> | null; after: Record<string, unknown> }
export type Component = {
  code: string; name: string; unit: string; required: string; reserved: string;
  consumed: string; missing: string; signal: 'green' | 'yellow' | 'red';
}
export type Job = { id: number; recipe: string; quantity: string; planned_on: string; status: string; components: Component[] }
export type Proposal = {
  recipe: string; args: { recipe_id: number; quantity: string; planned_on: string }; job_id: number | null; expires: string
}
export type Message = {
  id: number; role: 'user' | 'assistant'; body: string; status: string; model: string | null;
  tools: string[]; code: string[]; files: { id: number; filename: string; mime: string }[]; proposal: Proposal | null
}
export type Conversation = { id: number; title: string }
export type Fact = { id: number; key: string; value: string }
export type StreamEvent =
  | { type: 'start'; id: number }
  | { type: 'delta'; text: string }
  | { type: 'reset'; text: string }
  | { type: 'tool'; name: string }
  | { type: 'done'; message: Message }

export async function responseError(response: Response) {
  const error = await response.json().catch(() => ({ detail: 'Сервис временно недоступен' }))
  return new Error(error.detail || 'Не удалось выполнить запрос')
}

export async function api<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
  const response = await fetch('/api' + path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw await responseError(response)
  return response.status === 204 ? undefined as T : response.json()
}

export async function stream(
  key: number, question: string, file: File | null, signal: AbortSignal, onEvent: (event: StreamEvent) => void,
) {
  const body = new FormData()
  body.set('question', question)
  if (file) body.set('upload', file)
  const response = await fetch('/api/conversations/' + key + '/stream', { method: 'POST', body, signal })
  if (!response.ok) throw await responseError(response)
  if (!response.body) throw new Error('Поток ответа недоступен')
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) return
      buffer += value
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        if (frame.startsWith('data: ')) onEvent(JSON.parse(frame.slice(6)) as StreamEvent)
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export const label: Record<string, string> = {
  warehouse: 'Кладовщик', technologist: 'Технолог', manager: 'Руководитель',
  inspection: 'На проверке', accepted: 'Принята', rejected: 'Брак',
  planned: 'Запланировано', shortage: 'Дефицит', running: 'В работе',
  completed: 'Завершено', cancelled: 'Отменено',
  stock: 'Остатки', shortages: 'Дефицит', batch_history: 'История партии',
  job_cost: 'Себестоимость', propose_job: 'Планирование', consumption_dataset: 'Расход по датам',
  job_cost_dataset: 'Данные себестоимости', remember_preference: 'Запись в память',
  code_interpreter: 'Code Interpreter',
}

export function day(value: string) {
  return value.slice(0, 10).split('-').reverse().join('.')
}

export function quantity(value: string) {
  const [whole, fraction = ''] = value.split('.')
  const tail = fraction.replace(/0+$/, '')
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0') + (tail ? ',' + tail : '')
}
