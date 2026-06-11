/**
 * Core API Client with Provider Abstraction
 *
 * This client provides a lightweight, provider-agnostic layer for all API calls.
 * It handles authentication, error handling, CSRF protection, and request/response interceptors.
 *
 * Security:
 * - Automatically includes CSRF token in state-changing requests (POST, PUT, PATCH, DELETE)
 * - CSRF token is read from cookie and sent in X-CSRF-Token header
 * - Handles token rotation when server sends new token in response header
 */

import { apiLogger } from '@/utils/logger'

type RequestParams = Record<string, string | number | boolean | undefined | null>

type RequestOptions = RequestInit & {
  params?: RequestParams
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/**
 * Get a cookie value by name.
 * Used to retrieve CSRF token from cookie set by server.
 */
function getCookie(name: string): string | null {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) {
    return parts.pop()?.split(';').shift() || null
  }
  return null
}

/**
 * HTTP methods that require CSRF protection (state-changing operations)
 */
const CSRF_PROTECTED_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE']

/**
 * CSRF token header name (must match server configuration)
 */
const CSRF_HEADER_NAME = 'X-CSRF-Token'
const CSRF_COOKIE_NAME = 'csrf_token'
let authTokenGetter: () => string | null = () => null

export function setApiTokenGetter(getter: () => string | null): void {
  authTokenGetter = getter
}

// Token refresh coordination — prevents concurrent refresh calls when multiple
// requests 401 simultaneously. All waiters resolve/reject together.
let _refreshPromise: Promise<boolean> | null = null

export async function attemptTokenRefresh(): Promise<boolean> {
  if (_refreshPromise) return _refreshPromise
  _refreshPromise = (async () => {
    try {
      // Dynamic import to avoid circular dep (auth.service → client → auth.service)
      const { authService } = await import('@/api/services/auth.service')
      await authService.refresh()
      return true
    } catch {
      return false
    } finally {
      _refreshPromise = null
    }
  })()
  return _refreshPromise
}

class ApiClient {
  private baseURL: string
  private getToken: () => string | null
  private activeRequests: Map<string, AbortController> = new Map()

  constructor(baseURL: string, getToken: () => string | null) {
    this.baseURL = baseURL
    this.getToken = getToken
  }

  /** Return the base URL — used by streaming fetch calls. */
  getBaseUrl(): string {
    return this.baseURL
  }

  /** Return the Authorization header value if a token is available. */
  getAuthHeader(): Record<string, string> {
    const token = this.getToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  /**
   * Cancel a specific request by ID
   */
  cancelRequest(requestId: string): void {
    const controller = this.activeRequests.get(requestId)
    if (controller) {
      controller.abort()
      this.activeRequests.delete(requestId)
    }
  }

  /**
   * Cancel all active requests (useful on logout or navigation)
   */
  cancelAllRequests(): void {
    this.activeRequests.forEach((controller) => controller.abort())
    this.activeRequests.clear()
  }

  private async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const url = new URL(`${this.baseURL}${endpoint}`, window.location.origin)

    // Add query parameters if provided (filter out undefined/null values)
    if (options.params) {
      Object.keys(options.params).forEach((key) => {
        const value = options.params![key]
        if (value !== undefined && value !== null && value !== '') {
          url.searchParams.append(key, String(value))
        }
      })
    }

    // Get auth token
    const token = this.getToken()

    // Check if body is FormData (for file uploads)
    const isFormData = options.body instanceof FormData

    // Get CSRF token for state-changing requests
    const method = (options.method || 'GET').toUpperCase()
    const csrfToken = CSRF_PROTECTED_METHODS.includes(method)
      ? getCookie(CSRF_COOKIE_NAME)
      : null

    // Create AbortController for request cancellation
    const requestId = `${method}-${endpoint}-${Date.now()}-${Math.random()}`
    const externalSignal = options.signal
    const abortController = new AbortController()
    const abortFromExternalSignal = () => abortController.abort()
    if (externalSignal?.aborted) {
      abortController.abort()
    } else {
      externalSignal?.addEventListener('abort', abortFromExternalSignal, { once: true })
    }
    this.activeRequests.set(requestId, abortController)

    // Build request config
    const config: RequestInit = {
      ...options,
      credentials: 'include', // Include cookies for CSRF
      signal: abortController.signal,
      headers: {
        // Only set Content-Type for JSON, let browser set it for FormData
        ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
        ...options.headers,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        // Include CSRF token for state-changing requests
        ...(csrfToken ? { [CSRF_HEADER_NAME]: csrfToken } : {}),
      },
    }

    // Request interceptor (logging - dev only)
    apiLogger.log(`Request: ${config.method || 'GET'} ${url}`)

    try {
      const response = await fetch(url.toString(), config)

      // Clean up completed request
      this.activeRequests.delete(requestId)
      externalSignal?.removeEventListener('abort', abortFromExternalSignal)

      // Handle CSRF token rotation - server may send new token in response header
      const newCsrfToken = response.headers.get(CSRF_HEADER_NAME)
      if (newCsrfToken) {
        // Token will be automatically updated via Set-Cookie header
        apiLogger.log('CSRF token rotated')
      }

      // Response interceptor - handle errors
      if (!response.ok) {
        if (response.status === 401) {
          // /auth/me returning 401 means "not logged in" — not a mid-session expiry.
          // For all other endpoints, try refreshing the access token once before
          // giving up and firing the unauthorized event.
          if (endpoint !== '/auth/me' && endpoint !== '/auth/refresh') {
            const refreshed = await attemptTokenRefresh()
            if (refreshed) {
              // Retry the original request with the new cookies
              this.activeRequests.delete(requestId)
              return this.request<T>(endpoint, options)
            }
            window.dispatchEvent(new Event('unauthorized'))
          }
        }

        const responseText = await response.text().catch(() => '')
        let errorData: any = { message: 'An unexpected error occurred' }
        if (responseText) {
          try {
            errorData = JSON.parse(responseText)
          } catch {
            errorData = { message: responseText }
          }
        }

        // Handle CSRF errors specifically
        if (response.status === 403 && errorData.error_code?.startsWith('csrf_')) {
          apiLogger.warn('CSRF validation failed, refreshing page to get new token')
          window.dispatchEvent(new Event('csrf_error'))
        }

        // FastAPI returns errors in 'detail' field
        // For 422 validation errors, detail is an array of validation errors
        let errorMessage: string
        if (Array.isArray(errorData.detail)) {
          // Format validation errors
          errorMessage = errorData.detail
            .map((err: any) => `${err.loc?.join?.('.') || 'field'}: ${err.msg}`)
            .join(', ')
        } else {
          errorMessage = errorData.detail || errorData.message || `HTTP ${response.status}`
        }

        throw new ApiError(errorMessage, response.status, errorData.detail ?? errorData)
      }

      // Handle empty responses (e.g., 204 No Content from DELETE)
      if (response.status === 204 || response.headers.get('content-length') === '0') {
        return {} as T
      }

      const contentType = response.headers.get('content-type')
      if (contentType && contentType.includes('application/json')) {
        // Check if response has content before parsing
        const text = await response.text()
        if (text) {
          return JSON.parse(text) as T
        }
        return {} as T
      }

      return {} as T
    } catch (error) {
      // Clean up aborted or failed request
      this.activeRequests.delete(requestId)
      externalSignal?.removeEventListener('abort', abortFromExternalSignal)

      // Don't log AbortError as an error (it's intentional cancellation)
      if (error instanceof Error && error.name === 'AbortError') {
        apiLogger.log(`Request cancelled: ${method} ${endpoint}`)
        throw error
      }

      // /auth/me 401 means "not logged in" — expected on page load, not an error
      if (endpoint === '/auth/me' && error instanceof Error &&
          (error.message === 'authentication required' || error.message.includes('401'))) {
        apiLogger.log(`Session check: unauthenticated`)
        throw error
      }

      apiLogger.error('Error:', error)
      throw error
    }
  }

  get<T>(endpoint: string, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: 'GET' })
  }

  post<T>(endpoint: string, body?: any, options?: RequestOptions): Promise<T> {
    const requestInit: RequestInit = {
      ...options,
      method: 'POST',
    }
    // Only add body if provided
    if (body !== undefined) {
      // Don't stringify FormData, only JSON objects
      requestInit.body = body instanceof FormData ? body : JSON.stringify(body)
    }
    return this.request<T>(endpoint, requestInit)
  }

  put<T>(endpoint: string, body: any, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'PUT',
      body: JSON.stringify(body),
    })
  }

  patch<T>(endpoint: string, body: any, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'PATCH',
      body: JSON.stringify(body),
    })
  }

  delete<T>(endpoint: string, body?: any, options?: RequestOptions): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'DELETE',
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
  }
}

// Create and export the singleton instance
// Auth is now primarily via httpOnly cookies (sent automatically with credentials: 'include').
// The token getter is kept as fallback during the dual-support transition period.
export const apiClient = new ApiClient(
  import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  () => authTokenGetter()
)

/**
 * Cancel all active API requests
 * Call this on logout or navigation to prevent memory leaks
 */
export function cancelAllRequests(): void {
  apiClient.cancelAllRequests()
}
