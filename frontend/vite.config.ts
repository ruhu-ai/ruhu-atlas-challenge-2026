import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function getNodeModulePackageName(id: string): string | null {
  const marker = 'node_modules/'
  const start = id.lastIndexOf(marker)
  if (start === -1) return null

  const modulePath = id.slice(start + marker.length)
  if (!modulePath) return null

  const parts = modulePath.split('/')
  if (parts[0].startsWith('@') && parts.length > 1) {
    return `${parts[0]}/${parts[1]}`
  }
  return parts[0]
}

const mainOutputConfig = {
  manualChunks(id: string) {
    if (!id.includes('node_modules')) return undefined

    const packageName = getNodeModulePackageName(id)
    if (!packageName) return undefined

    // Large, self-contained libraries with no circular deps to React core
    if (packageName === 'reactflow') return 'vendor-reactflow'
    if (packageName.includes('livekit')) return 'vendor-livekit'
    if (packageName === 'recharts') return 'vendor-recharts'
    if (
      packageName === 'victory-vendor'
      || packageName.startsWith('d3-')
    ) {
      return 'vendor-charts-core'
    }
    if (packageName === 'lodash') return 'vendor-lodash'
    if (packageName === '@monaco-editor/react' || packageName === 'monaco-editor') return 'vendor-monaco'

    // Everything else stays in Rollup's default chunk to avoid circular init issues
    return undefined
  },
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const parsedPort = Number.parseInt(
    env.VITE_DEV_PORT || env.FRONTEND_DEV_PORT || '3010',
    10,
  )
  const devPort = Number.isFinite(parsedPort) ? parsedPort : 3010
  const proxyTarget = env.VITE_API_PROXY_TARGET
    ? env.VITE_API_PROXY_TARGET
    : (() => {
      const apiBase = env.VITE_API_BASE_URL || '/api/v1'
      if (/^https?:\/\//i.test(apiBase)) {
        try {
          return new URL(apiBase).origin
        } catch {
          // Keep backwards-compatible fallback for malformed absolute URL values.
        }
      }
      return 'http://127.0.0.1:8010'
    })()

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: devPort,
      strictPort: true,
      proxy: {
        '/api/v1': {
          target: proxyTarget,
          changeOrigin: true,
          ws: true,
          rewrite: (path) => path.replace(/^\/api\/v1/, ''),
        },
      },
    },
    build: {
      rollupOptions: {
        // Multi-entry build configuration
        input: mode === 'widget'
          ? {
              // Widget bundle entry
              widget: path.resolve(__dirname, 'src/features/customer-widget/widget-loader.tsx'),
            }
          : {
              // Main app entry
              main: path.resolve(__dirname, 'index.html'),
            },
        output: mode === 'widget'
          ? {
              // ES format enables native dynamic import() for lazy-loaded
              // livekit-client (~100KB) — split into a separate chunk.
              // All modern browsers that support WebRTC also support ES modules.
              format: 'es' as const,
              entryFileNames: 'widget.js',
              chunkFileNames: 'widget-[name].[hash].js',
              assetFileNames: 'widget-[name].[hash][extname]',
            }
          : mainOutputConfig,
      },
      // For widget build, output to dist/widget directory
      outDir: mode === 'widget' ? 'dist/widget' : 'dist',
      minify: 'esbuild',
    },
  }
})
