/// <reference types="vite/client" />
/// <reference types="jest" />
/// <reference types="@testing-library/jest-dom" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
  readonly VITE_WS_URL?: string
  readonly VITE_LIVEKIT_URL?: string
  readonly VITE_SENTRY_DSN?: string
  readonly VITE_ENV?: string
  readonly MODE: string
  readonly DEV: boolean
  readonly PROD: boolean
  readonly SSR: boolean
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
