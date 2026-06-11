/**
 * Production-ready Lazy Load Wrapper Component
 *
 * Features:
 * - Lazy loading with React.lazy and Suspense
 * - Error boundary for graceful error handling
 * - Loading skeletons/spinners
 * - Retry logic on failure
 * - Prefetching support
 *
 * Usage:
 *   <LazyLoadWrapper
 *     factory={() => import('./HeavyComponent')}
 *     fallback={<LoadingSkeleton />}
 *   />
 */

import React, { Suspense, Component, ReactNode, ComponentType } from 'react';

// ============================================
// Error Boundary
// ============================================

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
  onReset?: () => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('LazyLoadWrapper Error:', error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: undefined });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[400px] p-6">
          <div className="text-center max-w-md">
            <div className="text-red-500 mb-4">
              <svg
                className="w-16 h-16 mx-auto"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
            </div>
            <h3 className="text-lg font-semibold mb-2">
              Failed to load component
            </h3>
            <p className="text-gray-600 dark:text-gray-400 mb-4">
              {this.state.error?.message || 'An error occurred while loading this content'}
            </p>
            <button
              onClick={this.handleReset}
              className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
            >
              Try Again
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

// ============================================
// Loading Fallbacks
// ============================================

export const DefaultLoadingFallback: React.FC = () => (
  <div className="flex items-center justify-center min-h-[400px]">
    <div className="text-center">
      <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
      <p className="text-gray-600 dark:text-gray-400">Loading...</p>
    </div>
  </div>
);

export const PageLoadingSkeleton: React.FC = () => (
  <div className="animate-pulse p-6 space-y-4">
    <div className="h-8 bg-gray-200 dark:bg-gray-700 rounded w-1/4"></div>
    <div className="space-y-3">
      <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded"></div>
      <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-5/6"></div>
      <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-4/6"></div>
    </div>
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-8">
      <div className="h-32 bg-gray-200 dark:bg-gray-700 rounded"></div>
      <div className="h-32 bg-gray-200 dark:bg-gray-700 rounded"></div>
    </div>
  </div>
);

export const TableLoadingSkeleton: React.FC = () => (
  <div className="animate-pulse space-y-3">
    {[1, 2, 3, 4, 5].map((i) => (
      <div key={i} className="h-12 bg-gray-200 dark:bg-gray-700 rounded"></div>
    ))}
  </div>
);

// ============================================
// Lazy Load Wrapper Component
// ============================================

interface LazyLoadWrapperProps<T extends ComponentType<any>> {
  factory: () => Promise<{ default: T }>;
  fallback?: ReactNode;
  errorFallback?: ReactNode;
  prefetch?: boolean;
  delay?: number;
}

export function LazyLoadWrapper<T extends ComponentType<any>>({
  factory,
  fallback = <DefaultLoadingFallback />,
  errorFallback,
  prefetch = false,
  delay = 0,
}: LazyLoadWrapperProps<T>): React.FC<React.ComponentProps<T>> {
  // Lazy load the component
  const LazyComponent = React.lazy(() => {
    if (delay > 0) {
      // Add artificial delay for minimum loading state visibility
      return Promise.all([
        factory(),
        new Promise(resolve => setTimeout(resolve, delay))
      ]).then(([module]) => module);
    }
    return factory();
  });

  // Prefetch component if requested
  if (prefetch && typeof window !== 'undefined') {
    // Use requestIdleCallback for prefetching
    if ('requestIdleCallback' in window) {
      window.requestIdleCallback(() => factory());
    } else {
      setTimeout(() => factory(), 1);
    }
  }

  // Return wrapped component
  const WrappedLazyComponent: React.FC<React.ComponentProps<T>> = (props) => {
    const [retryCount, setRetryCount] = React.useState(0);

    const handleReset = () => {
      setRetryCount(prev => prev + 1);
    };

    return (
      <ErrorBoundary
        fallback={errorFallback || <DefaultLoadingFallback />}
        onReset={handleReset}
      >
        <Suspense fallback={fallback}>
          <LazyComponent key={retryCount} {...(props as any)} />
        </Suspense>
      </ErrorBoundary>
    );
  };

  return WrappedLazyComponent;
}

// ============================================
// Prefetch Utility
// ============================================

/**
 * Prefetch a lazy component module
 *
 * Usage:
 *   prefetchComponent(() => import('./HeavyComponent'))
 */
export function prefetchComponent<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>
): void {
  if (typeof window === 'undefined') return;

  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(() => factory().catch(() => {}));
  } else {
    setTimeout(() => factory().catch(() => {}), 1);
  }
}

// ============================================
// Lazy Route Wrapper
// ============================================

/**
 * Wrapper for lazy-loaded routes
 *
 * Usage:
 *   const AgentCanvas = lazyRoute(() => import('./pages/agent-canvas'))
 */
export function lazyRoute<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  options?: {
    fallback?: ReactNode;
    prefetch?: boolean;
  }
): React.FC<React.ComponentProps<T>> {
  return LazyLoadWrapper({
    factory,
    fallback: options?.fallback || <PageLoadingSkeleton />,
    prefetch: options?.prefetch,
  });
}

// ============================================
// Type Exports
// ============================================

export type { LazyLoadWrapperProps };
