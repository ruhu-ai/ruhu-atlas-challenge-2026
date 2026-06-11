export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return 'Never'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function formatRelativeNumber(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return '0'
  }
  return new Intl.NumberFormat().format(value)
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return '0%'
  }
  return `${Math.round(value * 100)}%`
}

export function titleCaseFromSnake(value: string | null | undefined): string {
  if (!value) {
    return 'Unknown'
  }
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const trimmed = value.trim()
  if (!trimmed) {
    return {}
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    throw new Error(`${label} must be valid JSON`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`)
  }
  return parsed as Record<string, unknown>
}

export function parseJsonStringMap(value: string, label: string): Record<string, string> {
  const parsed = parseJsonObject(value, label)
  const normalized: Record<string, string> = {}
  for (const [key, item] of Object.entries(parsed)) {
    normalized[key] = String(item)
  }
  return normalized
}

export function parseJsonArrayOfObjects(value: string, label: string): Array<Record<string, unknown>> {
  const trimmed = value.trim()
  if (!trimmed) {
    return []
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    throw new Error(`${label} must be valid JSON`)
  }
  if (!Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON array`)
  }
  return parsed.filter((item) => item && typeof item === 'object') as Array<Record<string, unknown>>
}

export function parseCommaList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function statusVariant(
  status: string | null | undefined
): 'default' | 'secondary' | 'destructive' | 'success' | 'warning' | 'info' | 'outline' {
  switch (status) {
    case 'published':
    case 'resolved':
    case 'final':
    case 'corrected':
    case 'success':
    case 'active':
      return 'success'
    case 'draft':
    case 'in_review':
      return 'warning'
    case 'pending':
    case 'cached_live':
      return 'info'
    case 'failed':
    case 'dismissed':
    case 'deprecated':
    case 'superseded':
      return 'destructive'
    default:
      return 'secondary'
  }
}
