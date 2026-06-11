const API_BASE_SUFFIXES = ['/api/v1', '/api/v1/']

function toAbsoluteUrl(apiUrl: string): URL {
  return new URL(apiUrl || '/api/v1', window.location.origin)
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/$/, '')
}

function stripWidgetSuffix(pathname: string): string {
  return pathname.replace(/\/public\/widget(\/|$)/, '/').replace(/\/\/+/, '/')
}

function ensureApiV1Path(pathname: string): string {
  if (API_BASE_SUFFIXES.includes(pathname)) {
    return pathname
  }

  if (pathname.endsWith('/api/v1/')) {
    return trimTrailingSlash(pathname)
  }

  const publicSuffixIndex = pathname.lastIndexOf('/public/widget')
  if (publicSuffixIndex >= 0) {
    pathname = pathname.slice(0, publicSuffixIndex)
  }

  pathname = trimTrailingSlash(pathname)
  if (pathname === '') {
    return '/api/v1'
  }
  if (pathname.endsWith('/api')) {
    return `${pathname}/v1`
  }
  return `${pathname}/api/v1`
}

export function normalizeWidgetApiUrl(apiUrl: string): string {
  const parsed = toAbsoluteUrl(apiUrl)
  const normalizedPath = ensureApiV1Path(stripWidgetSuffix(trimTrailingSlash(parsed.pathname) || '/'))
  parsed.pathname = normalizedPath
  return parsed.toString().replace(/\/$/, '')
}

export function buildWidgetPublicPath(apiUrl: string, path: string): string {
  const base = normalizeWidgetApiUrl(apiUrl)
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}
