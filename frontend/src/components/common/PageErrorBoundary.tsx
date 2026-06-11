/**
 * Per-route error boundary that catches errors within a single page.
 *
 * Prevents a crash in one route from breaking the entire app.
 * Renders an inline error message with a retry button.
 */

import React from 'react'
import { reportError } from '@/utils/logger'

interface Props {
  children: React.ReactNode
  fallback?: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class PageErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    reportError(error, {
      boundary: 'PageErrorBoundary',
      componentStack: errorInfo.componentStack ?? undefined,
    })
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback
      }

      return (
        <div className="flex items-center justify-center p-12">
          <div className="max-w-sm text-center space-y-3">
            <h2 className="text-lg font-semibold text-foreground">Page Error</h2>
            <p className="text-sm text-muted-foreground">
              This page encountered an error. Other parts of the app still work.
            </p>
            {process.env.NODE_ENV === 'development' && this.state.error && (
              <pre className="mt-2 rounded bg-muted p-3 text-left text-xs text-muted-foreground overflow-auto max-h-32">
                {this.state.error.message}
              </pre>
            )}
            <button
              onClick={this.handleReset}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Try Again
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
