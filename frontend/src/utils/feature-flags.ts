// Feature flags read from Vite environment variables.
//
// Why a dedicated module: ``import.meta.env`` can't be evaluated by
// ts-jest's CommonJS runtime. Centralising flag reads here lets tests
// mock the whole module (``jest.mock('@/utils/feature-flags', ...)``)
// without touching the production code path.
//
// Flags are read at module load — first import wins. That's fine for
// browser bundles (Vite inlines them at build time) and keeps the API
// trivial.

function readBoolEnv(key: string): boolean {
  const raw = (import.meta.env[key] as string | undefined) ?? ''
  return raw.toLowerCase() === 'true'
}

// When ``true``, the Library UI surfaces composite-callable authoring
// alongside the primary Code flow. Composite is a legacy advanced kind;
// existing rows keep working regardless of this flag — only the
// "+ New composite (advanced)" entry point is gated.
export const ADVANCED_KINDS_ENABLED = readBoolEnv('VITE_LIBRARY_ADVANCED_KINDS')
