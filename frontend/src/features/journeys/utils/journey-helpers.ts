import type { JourneyRuntimeAlert, JourneyVersionStatus } from '@/types/journeys';

export function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

export function formatShortDate(value?: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function summarizeMap(values: Record<string, number>): string {
  const entries = Object.entries(values);
  if (entries.length === 0) return '—';
  return entries
    .slice(0, 3)
    .map(([key, count]) => `${key}: ${count}`)
    .join(' · ');
}

export function summarizePayload(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (entries.length === 0) return '—';
  return entries
    .slice(0, 3)
    .map(([key, value]) => {
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        return `${key}: ${String(value)}`;
      }
      return `${key}: ${JSON.stringify(value)}`;
    })
    .join(' · ');
}

export function downloadJsonFile(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(href);
}

export function prettyJson(payload: unknown): string {
  return JSON.stringify(payload, null, 2);
}

export function parseJsonField<T>(label: string, rawValue: string, fallback?: T): T {
  const normalized = rawValue.trim();
  if (!normalized) {
    if (fallback !== undefined) {
      return fallback;
    }
    throw new Error(`${label} is required`);
  }
  try {
    return JSON.parse(normalized) as T;
  } catch (error) {
    const reason = error instanceof Error ? error.message : 'invalid JSON';
    throw new Error(`${label} must be valid JSON: ${reason}`);
  }
}

export function parseCommaSeparatedList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function commaSeparatedList(values?: string[] | null): string {
  return (values || []).join(', ');
}

export function definitionStatusVariant(status: string) {
  return status === 'active' ? 'success' : 'secondary';
}

export function versionStatusVariant(status: JourneyVersionStatus) {
  return status === 'published' ? 'success' : 'outline';
}

export function journeyStatusVariant(status: string) {
  if (status === 'completed') return 'success';
  if (status === 'open') return 'info';
  if (status === 'abandoned' || status === 'failed') return 'destructive';
  return 'warning';
}

export function alertVariant(alert: JourneyRuntimeAlert) {
  return alert.severity === 'error' ? 'destructive' : 'warning';
}
