/**
 * Frontend Logger Utility
 *
 * Provides centralized logging with environment-aware behavior.
 * - Development: All logs output to console
 * - Production: Only errors are logged (ready for Sentry/LogRocket integration)
 */

const isDev = import.meta.env.DEV

type LogLevel = 'debug' | 'info' | 'warn' | 'error'

interface LoggerOptions {
  /** Optional prefix for all log messages */
  prefix?: string
  /** Force logging even in production (use sparingly) */
  force?: boolean
}

/**
 * Create a logger instance with optional prefix.
 *
 * @example
 * const apiLogger = createLogger({ prefix: '[API]' })
 * apiLogger.log('Request sent', { url })
 */
export function createLogger(options: LoggerOptions = {}) {
  const { prefix = '', force = false } = options

  const shouldLog = (level: LogLevel): boolean => {
    if (force) return true
    if (level === 'error') return true // Always log errors
    return isDev
  }

  const formatMessage = (message: string): string => {
    return prefix ? `${prefix} ${message}` : message
  }

  return {
    debug: (...args: unknown[]) => {
      if (shouldLog('debug')) {
        const [first, ...rest] = args
        console.debug(formatMessage(String(first)), ...rest)
      }
    },

    log: (...args: unknown[]) => {
      if (shouldLog('info')) {
        const [first, ...rest] = args
        console.log(formatMessage(String(first)), ...rest)
      }
    },

    info: (...args: unknown[]) => {
      if (shouldLog('info')) {
        const [first, ...rest] = args
        console.info(formatMessage(String(first)), ...rest)
      }
    },

    warn: (...args: unknown[]) => {
      if (shouldLog('warn')) {
        const [first, ...rest] = args
        console.warn(formatMessage(String(first)), ...rest)
      }
    },

    error: (...args: unknown[]) => {
      if (shouldLog('error')) {
        const [first, ...rest] = args
        console.error(formatMessage(String(first)), ...rest)
      }
    },
  }
}

/**
 * Report an error to the production error tracking service.
 * In development, logs to console. In production, sends to configured endpoint.
 *
 * Integration point for Sentry, LogRocket, or custom error API.
 * To enable Sentry: npm install @sentry/react, then initialize in main.tsx
 * and replace the fetch call below with Sentry.captureException(error).
 */
export function reportError(error: Error, context?: Record<string, unknown>): void {
  // Always log to console
  console.error('[ErrorReport]', error.message, context)

  if (!isDev) {
    // Production: send to error reporting endpoint
    // Replace with Sentry.captureException(error, { extra: context }) when configured
    const errorApiUrl = import.meta.env.VITE_ERROR_REPORTING_URL
    if (errorApiUrl) {
      // Sanitize URL to prevent leaking sensitive data (tokens, PII in paths)
      const sanitizeUrl = (url: string): string => {
        try {
          const urlObj = new URL(url)
          // Only send origin and pathname (no query params, no hash)
          let sanitized = urlObj.origin + urlObj.pathname
          // Redact numeric IDs that might be PII (keep last path segment if it looks like an ID)
          const pathParts = urlObj.pathname.split('/')
          const lastPart = pathParts[pathParts.length - 1]
          if (lastPart && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(lastPart)) {
            // UUID - redact it
            pathParts[pathParts.length - 1] = '[REDACTED-ID]'
            sanitized = urlObj.origin + pathParts.join('/')
          } else if (lastPart && /^\d+$/.test(lastPart)) {
            // Numeric ID - redact it
            pathParts[pathParts.length - 1] = '[REDACTED-ID]'
            sanitized = urlObj.origin + pathParts.join('/')
          }
          return sanitized
        } catch {
          return '[INVALID-URL]'
        }
      }

      fetch(errorApiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: error.message,
          stack: error.stack,
          context,
          url: sanitizeUrl(window.location.href),
          timestamp: new Date().toISOString(),
          userAgent: navigator.userAgent,
        }),
      }).catch(() => {
        // Silently fail — don't cause cascading errors from error reporting
      })
    }
  }
}

// Default logger instance
export const logger = createLogger()

// Pre-configured loggers for common use cases
export const apiLogger = createLogger({ prefix: '[API]' })
export const socketLogger = createLogger({ prefix: '[Socket]' })
export const authLogger = createLogger({ prefix: '[Auth]' })
