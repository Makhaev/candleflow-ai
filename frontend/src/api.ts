export type User = { id: number; name: string; role: 'storekeeper' | 'technologist' | 'manager'; today: string }
export type Material = { code: string; name: string; unit: string }
export type Recipe = { id: number; name: string }
export type Batch = {
  id: number; receipt_id: number; material_code: string; supplier: string; received_on: string;
  status: string; received: string; quantity: string; reserved: string;
  unit_price: string; currency: string; unit_cost: string; exchange_rate: string; exchange_rate_on: string; comment: string;
}
export type Audit = { id: number; actor: string; at: string; entity: string; entity_id: string; before: Record<string, unknown> | null; after: Record<string, unknown> }
export type Component = {
  code: string; name: string; unit: string; required: string; reserved: string;
  issued: string; consumed: string; missing: string; signal: 'green' | 'yellow' | 'red';
}
export type Job = { id: number; recipe: string; quantity: string; planned_on: string; status: string; components: Component[] }
export type Proposal = { job_id: number | null; expires: string; confirmed_at: string | null } & (
  { action: 'plan_job'; recipe: string; args: { recipe_id: number; quantity: string; planned_on: string };
    preview: PreviewComponent[] | null } |
  { action: 'remember_preference'; args: { key: 'currency' | 'workshop'; value: string } }
)
export type PreviewComponent = { code: string; name: string; unit: string; required: string; free: string; missing: string }
export type ToolCall = { ordinal: number; name: string; model: string | null; status: string; at: string }
export type Message = {
  id: number; role: 'user' | 'assistant'; body: string; status: string; model: string | null;
  tools: string[]; tool_calls: ToolCall[]; attempts: string[]; code: string[]; files: { id: number; filename: string; mime: string }[]; proposal: Proposal | null
}
export type Conversation = { id: number; title: string }
export type Fact = { id: number; key: string; value: string }
export type StreamEvent =
  | { type: 'start'; id: number }
  | { type: 'delta'; text: string }
  | { type: 'reset'; text: string; retry: boolean }
  | { type: 'tool'; name: string }
  | { type: 'done'; message: Message }

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

export async function responseError(response: Response) {
  const error = await response.json().catch(() => ({ detail: 'Сервис временно недоступен' }))
  return new ApiError(typeof error.detail === 'string' ? error.detail : 'Не удалось выполнить запрос', response.status)
}

export async function api<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
  const response = await fetch('/api' + path, {
    method,
    cache: 'no-store',
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
  storekeeper: 'Кладовщик', technologist: 'Технолог', manager: 'Руководитель',
  on_review: 'На проверке', accepted: 'Принята', rejected: 'Брак',
  draft: 'Черновик', planned: 'Запланировано', shortage: 'Дефицит', in_progress: 'В работе',
  completed: 'Завершено', cancelled: 'Отменено',
  stock: 'Остатки', shortages: 'Дефицит', batch_history: 'История партии',
  job_cost: 'Себестоимость', propose_job: 'Планирование', consumption_dataset: 'Расход по датам',
  job_cost_dataset: 'Данные себестоимости', remember_preference: 'Запись в память',
  get_material_stock: 'Остатки', get_production_shortages: 'Дефицит', get_batch_history: 'История партии',
  calculate_job_cost: 'Себестоимость', create_and_plan_job: 'Планирование',
  code_interpreter: 'Code Interpreter',
}

const workshopTime = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'short',
})

export function dateTime(value: string) {
  return workshopTime.format(new Date(value))
}

export function day(value: string) {
  return value.slice(0, 10).split('-').reverse().join('.')
}

export function quantity(value: string) {
  const [whole, fraction = ''] = value.split('.')
  const tail = fraction.replace(/0+$/, '')
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0') + (tail ? ',' + tail : '')
}

// Quantities arrive as decimal strings with up to six fractional digits; BigInt keeps the
// subtraction exact instead of producing 0.19999999999999998.
const scale = 6
const toMicro = (value: string) => {
  const [whole, fraction = ''] = value.split('.')
  return BigInt(whole + (fraction + '0'.repeat(scale)).slice(0, scale))
}

export function subtract(left: string, right: string) {
  const micro = toMicro(left) - toMicro(right)
  const sign = micro < 0n ? '-' : ''
  const digits = (micro < 0n ? -micro : micro).toString().padStart(scale + 1, '0')
  return sign + digits.slice(0, -scale) + '.' + digits.slice(-scale)
}

export type Page<T> = { total: number; items: T[] }
