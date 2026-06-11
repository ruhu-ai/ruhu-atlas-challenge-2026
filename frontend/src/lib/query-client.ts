/**
 * React Query Client Configuration
 *
 * Centralizes configuration for TanStack Query (React Query)
 */

import { QueryClient } from '@tanstack/react-query'

/**
 * Don't retry on auth errors (401/403) - these won't succeed on retry
 * and would cause unnecessary requests after token expiry.
 */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof Error) {
    const message = error.message
    // Don't retry auth errors
    if (message.includes('HTTP 401') || message.includes('HTTP 403') ||
        message.includes('Not authenticated') || message.includes('Invalid or expired')) {
      return false
    }
  }
  return failureCount < 2
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      gcTime: 1000 * 60 * 10, // 10 minutes (formerly cacheTime)
      retry: shouldRetry,
      refetchOnWindowFocus: false, // Don't refetch on window focus
    },
    mutations: {
      retry: (failureCount, error) => shouldRetry(failureCount, error),
    },
  },
})
