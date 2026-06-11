# Ruhu AI Voice Agent Platform - Frontend

Production-ready frontend application for the Ruhu AI Voice Agent Platform, built with React, TypeScript, and modern web technologies.

## 🏗️ Architecture

This frontend follows a feature-based architecture with strict separation of concerns:

- **Component-Driven**: Built using Atomic Design principles (atoms → molecules → organisms → templates → pages)
- **Type-Safe**: Full TypeScript coverage for maintainability and developer experience
- **Provider-Agnostic**: Abstraction layer for AI providers (STT, TTS, LLM) to prevent vendor lock-in
- **State Management**: Zustand for global state, React Query for server state
- **Dark Theme First**: Optimized for low-light environments with professional aesthetic

## 📦 Tech Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| React | 18.2+ | UI library |
| TypeScript | 5.3+ | Type safety |
| Vite | 5.0+ | Build tool & dev server |
| Tailwind CSS | 3.4+ | Utility-first CSS |
| Radix UI | Latest | Accessible component primitives |
| Zustand | 4.4+ | Global state management |
| TanStack Query | 5.17+ | Server state & caching |
| React Router | 6.21+ | Client-side routing |
| React Flow | 11.10+ | Node-based Agent Canvas |
| LiveKit | 2.0+ | Real-time voice communication |

## 🚀 Getting Started

### Prerequisites

- Node.js 18+ and npm/yarn/pnpm
- Backend API running on `http://localhost:8000` (or configure `VITE_API_BASE_URL`)

### Installation

```bash
# Install dependencies
npm install

# Copy environment variables
cp .env.example .env

# Start development server
npm run dev
```

The app will be available at `http://localhost:3001`

### Available Scripts

```bash
npm run dev         # Start development server with HMR
npm run build       # Build for production
npm run preview     # Preview production build locally
npm run lint        # Lint code with ESLint
npm run test        # Run unit tests (Jest + RTL)
npm run test:e2e    # Run end-to-end tests (Playwright)
```

## 📁 Project Structure

```
frontend/
├── public/                 # Static assets
├── src/
│   ├── api/                # API client & services
│   │   ├── client.ts       # Core fetch client with auth
│   │   ├── services/       # Domain-specific API services
│   │   │   ├── agent.service.ts
│   │   │   └── auth.service.ts
│   │   └── providers/      # Provider abstraction (STT/TTS/LLM)
│   ├── assets/             # Images, fonts
│   ├── components/         # Reusable UI components
│   │   ├── atoms/          # Button, Input, Label, Card
│   │   ├── molecules/      # SearchForm, MetricCard
│   │   ├── organisms/      # Header, Sidebar, AgentList
│   │   └── templates/      # Page layouts
│   ├── config/             # App configuration
│   ├── features/           # Feature modules (future)
│   │   ├── agent-canvas/   # Agent Canvas feature
│   │   ├── analytics/      # Analytics dashboards
│   │   └── insights/       # Insights & recommendations
│   ├── hooks/              # Custom React hooks
│   ├── layouts/            # Application layouts
│   ├── lib/                # Third-party library configs
│   │   ├── query-client.ts # React Query setup
│   │   └── utils.ts        # Utility functions (cn, formatters)
│   ├── pages/              # Route pages
│   │   └── login.tsx       # Login page
│   ├── store/              # Zustand global stores
│   │   ├── auth.store.ts   # Authentication state
│   │   └── ui.store.ts     # UI state (sidebar, theme)
│   ├── types/              # TypeScript type definitions
│   │   └── index.ts        # Core types
│   ├── utils/              # Utility functions
│   ├── App.tsx             # Main app component
│   ├── main.tsx            # Entry point
│   └── index.css           # Global styles & design tokens
├── .env.example            # Environment variables template
├── tailwind.config.js      # Tailwind CSS configuration
├── tsconfig.json           # TypeScript configuration
├── vite.config.ts          # Vite configuration
└── package.json            # Dependencies & scripts
```

## 🎨 Design System

### Colors (Dark Theme)

- **Background**: `#0a0a0a` (Primary), `#121212` (Secondary), `#1a1a1a` (Surface)
- **Primary**: Indigo-400 `#818cf8`
- **Accent**: Orange-500 `#f97316`
- **Text**: `#fafafa` (Primary), `#a3a3a3` (Secondary), `#737373` (Muted)

### Typography

- **Font**: Inter (UI), JetBrains Mono (Code)
- **Scale**: 12px → 72px (xs → 7xl)
- **Weights**: 300 → 800

### Spacing

- Base unit: 4px
- Scale: 0, 2px, 4px, 8px, 12px, 16px, 24px, 32px, 48px, 64px...

### Components

All components are built on **Radix UI** primitives for accessibility:
- Button (6 variants, 6 sizes, loading state)
- Input (with error handling)
- Card (with header, content, footer)
- Label (accessible form labels)

## 🔐 Authentication Flow

1. User visits `/login`
2. Submits credentials → `authService.login()`
3. On success: Store `{ user, token }` in Zustand + localStorage
4. Redirect to `/dashboard`
5. Protected routes check `isAuthenticated` state

### Auth Store API

```typescript
import { useAuthStore } from '@/store/auth.store'

const { login, logout, user, isAuthenticated } = useAuthStore()

// Login
await login('user@example.com', 'password')

// Logout
logout()

// Check auth status
if (isAuthenticated) {
  console.log('User:', user)
}
```

## 📡 API Client

### Provider-Agnostic Design

The API client abstracts all third-party providers, making it easy to swap STT/TTS/LLM providers without changing UI code.

```typescript
import { apiClient } from '@/api/client'

// GET request
const agents = await apiClient.get<Agent[]>('/agents')

// POST request
const newAgent = await apiClient.post<Agent>('/agents', {
  name: 'Sales Bot',
  description: 'Handles sales inquiries'
})

// Automatic auth token injection
// Automatic error handling (401 → logout)
// Request/response logging
```

### Service Layer

```typescript
import { agentService } from '@/api/services/agent.service'

// Get all agents
const agents = await agentService.getAllAgents()

// Get agent by ID
const agent = await agentService.getAgentById('123')

// Create agent
const newAgent = await agentService.createAgent({ name: 'Support Bot' })

// Update agent
await agentService.updateAgent('123', { status: 'published' })
```

## 🧪 State Management

### Zustand (Global State)

Used for UI state and simple client state:

```typescript
import { useUIStore } from '@/store/ui.store'

const { isSidebarOpen, toggleSidebar, theme, setTheme } = useUIStore()

toggleSidebar() // Toggle sidebar visibility
setTheme('dark') // Set theme
```

### React Query (Server State)

Used for all server data fetching with automatic caching:

```typescript
import { useQuery } from '@tanstack/react-query'
import { agentService } from '@/api/services/agent.service'

function AgentList() {
  const { data: agents, isLoading, error } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentService.getAllAgents()
  })

  // React Query handles caching, refetching, and background updates
}
```

## 🛣️ Routing

Routes are defined in `src/App.tsx`:

```
/                    → /dashboard (redirect)
/login               → Login page
/register            → Register page
/dashboard           → Main dashboard (protected)
/agents              → Agent list (protected)
/agents/:id/canvas   → Agent Canvas editor (protected)
/analytics           → Analytics dashboard (protected)
/insights            → Insights dashboard (protected)
/settings            → Settings (protected)
```

Protected routes automatically redirect to `/login` if user is not authenticated.

## 🎯 Next Steps

### Implemented ✅

- [x] Project structure and configuration
- [x] Tailwind CSS + design tokens
- [x] API client with provider abstraction
- [x] Zustand stores (auth, UI)
- [x] React Query setup
- [x] Core atomic components (Button, Input, Card, Label)
- [x] Login page
- [x] Routing with protected routes
- [x] TypeScript types

### Pending Implementation 🚧

- [ ] Register page
- [ ] MFA verification page
- [ ] Dashboard (Home)
- [ ] Agent List page
- [ ] Agent Canvas (React Flow integration)
- [ ] Analytics Dashboard
- [ ] Insights Dashboard
- [ ] Testing Simulator
- [ ] Settings pages
- [ ] Sidebar & Header components
- [ ] Additional atomic components (Select, Checkbox, Switch, etc.)
- [ ] Unit tests (Jest + React Testing Library)
- [ ] E2E tests (Playwright)

## 📚 Documentation References

- [Visual Wireframes](/doc/VISUAL-WIREFRAMES-&-ASCII-DIAGRAMS-Ruhu.md)
- [Frontend Architecture](/doc/Frontend-Architecture-Document-Ruhu-AI-Voice-Agent-Platform.md)
- [Design System](/doc/Ruhu-AI-Design-System-&-Component-Librar.md)
- [UI/UX Specifications](/doc/Ruhu-AI-Voice-Agent-Platform-UI-UX-Specifications.md)

## 🤝 Contributing

1. Follow the established folder structure
2. Use TypeScript for all new files
3. Follow Atomic Design principles for components
4. Add JSDoc comments for complex functions
5. Use the design system tokens (no hardcoded colors/spacing)
6. Test components with React Testing Library
7. Ensure accessibility (WCAG 2.1 AA compliance)

## 📄 License

Proprietary - Ruhu AI Platform

---

**Last Updated**: 2025-12-27
**Maintained By**: Frontend Team
