# Ruhu — Claude Code Instructions

## Project

Ruhu is a production-grade conversational AI runtime. The backend is a deterministic kernel built on `AgentDocument` (scenarios → steps → transitions) that manages turn-based conversations across voice, chat, and telephony channels. The frontend is a web app for authoring agent documents, testing agents, and monitoring operations.

**Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.0+, Pydantic 2.8+, Psycopg3, Alembic, uvicorn. Optional: LiveKit Agents SDK 1.5.x for voice, Transformers/Torch for local inference.

**Frontend:** React 18.2+, TypeScript 5.9+, Vite 5.0+, React Router DOM 6.21+, TanStack React Query 5.17+, Zustand 4.4+, Tailwind CSS 3.4+, Radix UI.

**Entry points:**
- Backend: `src/ruhu/api.py` — `build_default_app()` / `create_app()`
- Frontend: `frontend/src/main.tsx` → `App.tsx`

---

## Commands

### Backend
```bash
make install             # create .venv, install editable package with [api,dev,browser-e2e]
make db-bootstrap        # create local ruhu_runtime_dev Postgres DB
make db-upgrade          # apply Alembic migrations
make db-revision MSG="…" # create new migration
make test                # run pytest with PYTHONPATH=src

# Dev server
uvicorn ruhu._dev_server:create_app --factory

# Standalone processes
python -m ruhu.simulator
python -m ruhu.livekit_worker
```

### Frontend
```bash
cd frontend
npm run dev              # Vite dev server (http://localhost:5173)
npm run build            # TypeScript check + Vite build
npm run build:widget     # widget-only build
npm run lint             # ESLint on .ts/.tsx
npm run test             # Jest
npm run test:coverage
npm run test:e2e         # Playwright
```

### Smoke / Integration
```bash
make realtime-widget-chat-smoke
make realtime-widget-voice-smoke
make realtime-livekit-smoke
make auth-ui-e2e
make auth-browser-e2e
make ticketing-verify
```

---

## Architecture

### Backend Modules (`src/ruhu/`)

| Module | Purpose |
|---|---|
| `kernel.py` | `ConversationKernel` — core state machine; accepts `RuntimeTurn`, evaluates guards/transitions, calls tools, records traces |
| `api.py` | FastAPI factory; composes kernel, stores, interpreters, auth, all routers |
| `templates/system/` | Shipped starter templates (JSON) seeded into `graph_templates` table on startup |
| `stores.py` | Conversation + Trace persistence; in-memory (dev) and SQLAlchemy (prod) |
| `interpreter.py / interpreters.py` | `SemanticInterpreter` Protocol + implementations: `KeywordInterpreter`, `GemmaLocalInterpreter` (delegates to `classifier/dispatcher`), `NullInterpreter` (kill-switch when `RUHU_CLASSIFIER_MODE=off`) |
| `classifier/` | **Prefill-first classifier subsystem** — single-tier intent classification post-cascade-collapse (see `docs/pre-fill-intent-classifier-design/architecture-final.md`). Modules: `prompt` (deterministic byte-identical prefix), `protocol` (`PrefillClassifier` Protocol + `ClassificationRequest/Result`), `dispatcher` (single seam: prompt + LoRA resolution → backend), `factory` (build via `RUHU_CLASSIFIER_BACKEND=transformers\|vllm\|vertex_gemini`), `vllm_backend`, `transformers_backend`, `vertex_gemini_backend` (failback — direct Vertex REST), `registry` (LoRA lifecycle + `resolve_lora`), `promotion`/`promotion_api` (eval-gate → status flip), `hot_load`/`hot_load_api` (vLLM `/v1/load_lora_adapter` admin), `training_scheduler`/`training_api` (auto + manual retrain triggers), `publish_gate` (tokenizer warning), `benchmark/` (Stage 2.5 harness + `probe_vllm_api`), `training/` (`trace_export` + `teacher_relabel` + `curate`; `train_lora` and per-LoRA eval harness live in `ruhu-ai-training/qwen`) |
| `response_generation.py` | `GeminiDialogueGenerator` — the **other** LLM in the two-LLM split: rendering, `select_move`, free-text generation. Renamed from `GeminiResponseGenerator` in WI-7.1; env vars are `RUHU_DIALOGUE_*` (formerly `RUHU_RESPONSE_GENERATOR_*`) |
| `livekit_adapter.py / livekit_worker.py` | LiveKit SDK isolation; token issuer, control plane, voice session lifecycle |
| `auth.py / auth_runtime.py` | JWT codec, `AuthService`, `AuthContextMiddleware`; magic-link, OAuth, SSO |
| `rules.py / rules_resolver.py` | Dynamic rule programs evaluated at pre/mid-turn decision stages |
| `tools/` | `ToolRuntime` + executors: `builtin`, `http`, `mcp`, `reference` |
| `knowledge/` | Document ingestion, chunking, embedding, retrieval (Weaviate or in-memory) |
| `journeys/` | Multi-step customer workflows, funnel analytics, replay, job scheduling |
| `ticketing_api.py` | Multi-provider ticketing (Zendesk, Jira, Freshdesk, Linear) with retry worker |
| `billing/` | Stripe integration, usage-based billing, subscription management |
| `notifications/` | Event-driven notification dispatch with durable retry |
| `runtime_config.py` | `RuntimeSettings` — all config loaded from environment |
| `phone_provider_*.py` | Multi-carrier phone abstraction (Telnyx, Africa's Talking) |
| `kpi/` | KPI goal tracking and metrics |

### Frontend Architecture

- Feature-based layout: each major domain lives in `frontend/src/features/<name>/`
- Zustand stores for client state (auth, UI, canvas)
- React Query for all server state
- Lazy-loaded route components via `React.lazy()` + `Suspense`
- API calls go through `frontend/src/api/client.ts` (bearer token + CSRF) → service classes in `frontend/src/api/services/`

---

## Directory Structure

```
ruhu/
├── src/ruhu/                 # Python backend package
│   ├── api.py                # FastAPI app factory + routes
│   ├── kernel.py             # ConversationKernel (core)
│   ├── templates/system/     # Shipped starter templates (JSON)
│   ├── stores.py             # Conversation/trace persistence
│   ├── schemas.py            # RuntimeTurn, StateGraph Pydantic models
│   ├── runtime_config.py     # RuntimeSettings (env config)
│   ├── auth.py               # AuthService, JWT codec
│   ├── livekit_adapter.py    # LiveKit SDK isolation layer
│   ├── livekit_worker.py     # Voice worker process
│   ├── tools/                # Tool runtime + executors
│   ├── knowledge/            # RAG: ingestion, embedding, retrieval
│   ├── journeys/             # Journey workflows + analytics
│   ├── kpi/                  # KPI goals tracking
│   ├── rules.py              # Rules engine
│   ├── billing/              # Stripe billing
│   ├── notifications/        # Notification dispatch
│   ├── realtime/             # WebSocket/realtime bridge models
│   ├── intent_tags/          # Intent classification (legacy hosted classifier)
│   └── classifier/           # Prefill-first classifier (prompt assembler, vLLM/transformers/vertex_gemini backends, LoRA registry, training data prep)
├── frontend/
│   └── src/
│       ├── main.tsx           # App entry point
│       ├── App.tsx            # React Router route definitions
│       ├── pages/             # Lazy-loaded page components
│       ├── features/          # Feature modules (self-contained)
│       │   ├── agent-canvas/  # State graph editor (canvas)
│       │   ├── voice-session/ # Voice testing UI
│       │   ├── analytics/     # Analytics dashboards
│       │   ├── journeys/      # Journey builder
│       │   ├── knowledge-base/
│       │   ├── kpi-goals/
│       │   ├── customer-widget/
│       │   └── settings/
│       ├── components/        # Atomic design: atoms/molecules/organisms/templates
│       ├── api/
│       │   ├── client.ts      # Base HTTP client (auth + CSRF)
│       │   └── services/      # Domain service classes
│       ├── store/             # Zustand stores
│       ├── hooks/             # Shared custom hooks
│       ├── types/             # TypeScript domain interfaces
│       └── utils/
├── tests/                    # Pytest test suite
├── scripts/                  # Shell + Python utility scripts
├── docs/                     # Architecture docs
├── Makefile
└── pyproject.toml
```

---

## Routes

### Backend API (FastAPI — `src/ruhu/api.py`)

**Core runtime:**
- `POST /conversations` — start conversation
- `POST /conversations/{id}/turns` — send a turn
- `GET  /conversations/{id}/traces`
- `GET  /conversations/{id}/realtime-events`

**Agents (the authored unit — `AgentDocument` with scenarios → steps):**
- `GET/POST /agents` — list / create
- `GET /agents/{agent_id}` — full target response (definition + version)
- `PUT /agents/{agent_id}` — replace whole document
- `DELETE /agents/{agent_id}`
- `GET /agents/{agent_id}/agent-document` — read just the document
- `PUT /agents/{agent_id}/agent-document` — replace just the document
- `POST /agents/{agent_id}/draft` / `/publish` / `/unpublish`
- `GET /agents/{agent_id}/diff` / `/publish-review` / `/audit`
- `GET /agents/{agent_id}/versions` — version list
- `GET /agents/{agent_id}/validation` — current validation report
- `GET/PATCH /agents/{agent_id}/settings`
- `GET/PATCH /agents/{agent_id}/evaluation-policy`
- `PATCH /agents/{agent_id}/metadata`
- `POST /agents/{agent_id}/test-session` — start a test session

Steps and transitions are NOT addressable via separate endpoints. The
agent document (`PUT /agents/{agent_id}/agent-document`) is the unit of
edit; sub-tree CRUD does not exist on the API.

**Public widget (unauthenticated):**
- `POST /public/widget/sessions`
- `POST /public/widget/sessions/{id}/messages` (+ `/stream`)
- `POST /public/widget/sessions/{id}/voice`
- `GET  /public/widget/sessions/{id}/conversation-events` (streaming)

**Auth:**
- `POST /auth/magic-link/request` / `/verify`
- `POST /auth/oauth/google/start` / `/sso/start` / `/callback`
- `POST /auth/refresh` / `/logout`
- `GET  /auth/me` / `PATCH /auth/me`

**Channels & providers:**
- `POST /channels/whatsapp/messages`
- `POST /channels/phone/calls/start`
- `GET/POST /providers/meta/whatsapp/webhook`
- `POST /providers/livekit/voice/sessions/{id}/transcripts|messages|signals|disconnect`

**Modular routers (installed conditionally):**
- `/knowledge/*` — document management
- `/kpis/*` — KPI goals
- `/rules/*` — rule authoring
- `/tickets/*` — ticketing CRUD
- `/notifications/*`
- `/browser-tasks/*`
- `/intent-tags/*`

**Health:** `GET /health`

---

### Frontend Routes (React Router — `frontend/src/App.tsx`)

**Public:**
- `/login`, `/signup`, `/accept-invitation`
- `/auth/callback`, `/auth/magic-link`
- `/terms`, `/privacy`

**Protected (require auth):**
- `/dashboard`
- `/agents` — agent list
- `/agents/:id/canvas` — agent canvas (Document / Graph / Test surfaces)
- `/agents/:id/widget` — widget config
- `/calls` — conversation history
- `/knowledge-base`
- `/kpi-goals`
- `/analytics`, `/insights`
- `/journeys`, `/rules`, `/tools`, `/testing`
- `/settings`, `/billing-settings`, `/audit`
- `/operations/phone-numbers`
- `/staff` — superuser only

---

## Component Pattern

Features are self-contained modules under `frontend/src/features/<name>/`:
```
features/agent-canvas/
  components/      # Feature-specific components
  hooks/           # Feature-specific hooks
  utils/
  index.ts         # Clean re-exports
```

Shared components follow atomic design under `frontend/src/components/`:
- `atoms/` — Button, Input, Badge, Card, Checkbox, etc. (Radix UI + Tailwind)
- `molecules/` — FormField, ButtonGroup, etc.
- `organisms/` — DataTable, Modals, Sidebars
- `templates/` — Page layout wrappers

**Key patterns:**
- `cn()` (clsx) for conditional className composition
- `useForm()` + Zod schemas for all forms (react-hook-form)
- `useQuery()` / `useMutation()` from React Query for all server state
- `React.lazy()` + `<Suspense>` for all page-level components
- Zustand stores: `useAuthStore`, `useUIStore`, `useCanvasStore`

---

## Styling

- **Tailwind CSS v3.4+** — utility-first, no CSS modules or styled-components
- **Dark mode:** class-based (`.dark` on `:root`)
- **Brand color:** `#E64E20` burnt orange ("African sunset" primary)
- **Light theme:** warm white background `#faf9f7`, dark text `#1a1917`
- **Dark theme:** warm charcoal `#0f0e0d`, off-white foreground `#f6f5f4`
- CSS variables (HSL) for theming: `--background`, `--foreground`, `--primary`, `--card`, etc. (defined in `frontend/src/index.css`)
- Icons: `lucide-react` SVG icons throughout
- Animations: Tailwind keyframes — `fade-in`, `slide-up`, `pulse-glow`, `accordion-down/up`
- Fonts: Inter (sans), JetBrains Mono / IBM Plex Mono (mono)

**Pattern:**
```tsx
className={cn(
  'flex items-center gap-2 rounded-lg p-4',
  isActive && 'bg-primary text-primary-foreground'
)}
```

---

## API Route Pattern (Frontend)

### Base client — `frontend/src/api/client.ts`

- Single `ApiClient` class with `.get<T>()`, `.post<T>()`, `.put<T>()`, `.patch<T>()`, `.delete<T>()`
- Base URL: env var or defaults to `http://localhost:8010` for dev
- **Auth:** `Authorization: Bearer ${token}` on every request (token from `useAuthStore`)
- **CSRF:** reads `csrf_token` cookie → `X-CSRF-Token` header on mutating requests (POST/PUT/PATCH/DELETE)
- 401 responses → auto-logout + redirect to `/login`
- `AbortController` per request for cancellation on unmount

### Service layer — `frontend/src/api/services/<domain>.service.ts`

```typescript
export class AgentService {
  static getAgent(agentId: string) {
    return apiClient.get<Agent>(`/agents/${agentId}`)
  }
  static createAgent(payload: CreateAgentRequest) {
    return apiClient.post<Agent>('/agents', payload)
  }
  static updateSettings(agentId: string, settings: AgentSettings) {
    return apiClient.patch<AgentSettingsResponse>(`/agents/${agentId}/settings`, settings)
  }
}
```

### React Query integration

```typescript
// Query (GET)
const { data } = useQuery(['agents'], () => AgentService.listAgents())

// Mutation (POST/PATCH/DELETE)
const mutation = useMutation((payload) => AgentService.createAgent(payload))
```

### Streaming endpoints

Some endpoints return streaming responses (SSE / NDJSON):
- `GET /public/widget/sessions/{id}/conversation-events`
- `POST /public/widget/sessions/{id}/messages/stream`

Use `Response.body` as `ReadableStream` — do not use standard JSON parsing for these.
