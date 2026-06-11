# Ruhu

Clean-slate state-native runtime for Ruhu agents.

This project is the clean implementation target for:

- agent document authoring
- turn-based conversation kernel
- typed semantic events
- bounded model services
- trace-first observability

## Structure

- `docs/`: product and runtime design docs
- `src/ruhu/`: implementation
- `tests/`: canonical schema and kernel tests

Rules system design guidance lives in `docs/rules-system/README.md`.

Theme guidance for the built-in playground lives in `docs/ui-theme.md`.

The project also now includes backend-rendered auth pages that mirror the old Ruhu
login, signup, invitation, and callback layouts while binding them to the new
passwordless auth surface.

## Initial scope

- canonical domain schemas
- `process_turn(...)` kernel contract
- deterministic-first fact updates
- semantic event normalization
- trace emission contract
- starter templates (`src/ruhu/templates/system/`) that seed the `agent_templates` table on startup

## Not included yet

- additional model provider integrations beyond the shipped Vertex + local Gemma paths

The project now includes:

- in-memory stores and Postgres-backed SQLAlchemy persistence
- file-backed agent registry
- FastAPI app factory over the kernel
- SQLAlchemy-backed auth and tenancy shell composition

## Local Python Environment

This repo should now run from its own local virtualenv, not the global shell
Python or Anaconda environment.

Bootstrap it once:

```bash
cd /Users/ijidailassa/projects/ruhu
make install
```

That creates `.venv/` in the repo root and installs the API, dev, and browser
test dependencies into that environment. After that, the `make` targets in this
repo use `.venv/bin/...` by default.

## Release Hygiene

For a repeatable release smoke, run:

```bash
cd /Users/ijidailassa/projects/ruhu
make release-hygiene
```

That target creates a fresh temporary virtualenv, installs the editable package
with `.[api,dev,browser-e2e]`, boots the FastAPI app, checks `/ready`, and
verifies `/knowledge/documents/upload` works with multipart upload support.

## Quickstart

The library-level entry point is `ConversationKernel`, which consumes an agent
document and an interpreter. Real agents are created through the API by cloning
one of the starter templates in `src/ruhu/templates/system/` (they seed into the
`agent_templates` table on app startup):

```bash
curl -X POST http://127.0.0.1:8010/agent-templates/gtpl_sales/clone \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "My Sales Agent"}'
```

For library-level kernel use with a hand-built agent document, see
`tests/test_agent_document.py` for canonical construction patterns.

The kernel is intentionally generic: business understanding lives in
interpreter implementations (`KeywordInterpreter`, `GemmaLocalInterpreter`,
etc.) registered on the kernel, not baked into the kernel itself.

## Channel Model

The runtime now separates delivery surface from modality:

- `channel`: `phone`, `whatsapp`, `web_chat`, `web_widget`, `browser`
- `modality`: `text`, `audio`, `image`, `file`, `mixed`, `event`

This keeps telephony, messaging, embedded web widgets, and browser-agent execution
as distinct runtime surfaces instead of collapsing them into coarse `chat` or `voice`
buckets.

## File-backed simulation

You can run an agent document and transcript from disk. Export an agent document
as JSON from the canvas or template store, save a transcript as JSON, then:

```bash
PYTHONPATH=src python -m ruhu.simulator \
  --agent-document-file path/to/agent-document.json \
  --transcript-file path/to/transcript.json \
  --interpreter keyword
```

To inspect the full structured trace for review or debugging, add `--json`.
To use the local Gemma model:

```bash
PYTHONPATH=src /tmp/ruhu-gemma-arm-venv/bin/python -m ruhu.simulator \
  --agent-document-file path/to/agent-document.json \
  --transcript-file path/to/transcript.json \
  --interpreter gemma_local \
  --model-path /tmp/gemma-4-E4B-it
```

The default global Python on this machine is not Gemma-4-capable. Use a runtime with
`transformers>=5.5`, such as `/tmp/ruhu-gemma-arm-venv/bin/python`, or install the
optional project extras:

```bash
.venv/bin/pip install -e ".[gemma-local]"
```

If Gemma fails with an unreadable `model.safetensors`, the downloaded weight file is
corrupt or incomplete. Re-download it into a fresh directory:

```bash
huggingface-cli download google/gemma-4-E4B-it --local-dir /tmp/gemma-4-E4B-it-clean
```

The runtime now also validates the expected SHA-256 for `gemma-4-E4B-it` and will
fail fast if the local file does not match the Hugging Face blob checksum. That avoids
the earlier failure mode where a readable-but-wrong safetensors file produced garbage
generation output.

## API

The project now includes a small FastAPI surface for agent discovery, conversation start,
turn processing, and trace inspection.

```python
from ruhu import build_default_app

app = build_default_app(
    database_url="postgresql+psycopg://ruhu:secret@localhost:5432/ruhu_runtime_dev",
    graph_interpreters={"sales_agent": "gemma_local"},
    model_path="/tmp/gemma-4-E4B-it",
    auth_database_url="postgresql+psycopg://ruhu:secret@localhost:5432/ruhu_runtime_dev",
    auth_jwt_secret="replace-with-32-byte-hs256-secret",
)
```

You can also drive interpreter selection from env instead of code:

```bash
export RUHU_GRAPH_INTERPRETERS='{"sales_agent":"gemma_local"}'
export RUHU_MODEL_PATH=/tmp/gemma-4-E4B-it
export RUHU_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev
export RUHU_AUTH_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev
export RUHU_AUTH_JWT_SECRET=replace-with-32-byte-hs256-secret
export RUHU_FRONTEND_URL=http://localhost:3001
export RUHU_PROVIDER_SHARED_SECRET=replace-me
export RUHU_LIVEKIT_SERVER_URL=wss://livekit.example.com
export RUHU_LIVEKIT_API_KEY=replace-me
export RUHU_LIVEKIT_API_SECRET=replace-me
export RUHU_LIVEKIT_AGENT_NAME=ruhu-voice
export RUHU_LIVEKIT_ROOM_PREFIX=ruhu
export RUHU_LIVEKIT_AGENTS_SDK_VERSION=1.5.1
export RUHU_LIVEKIT_VOICE_MODE=pipeline
export RUHU_LIVEKIT_DISPATCH_STRATEGY=hybrid
export RUHU_LIVEKIT_CONTROL_PLANE_BASE_URL=http://127.0.0.1:8010
export RUHU_SMTP_HOST=smtp.resend.com
export RUHU_SMTP_PORT=587
export RUHU_SMTP_USER=resend
export RUHU_SMTP_PASSWORD=replace-me
export RUHU_SMTP_FROM_EMAIL=hello@ruhu.ai
export RUHU_SMTP_FROM_NAME="Ruhu AI"
export RUHU_TICKETING_RETRY_WORKER_ENABLED=false
export RUHU_TICKETING_RETRY_INTERVAL_SECONDS=60
export RUHU_TICKETING_RETRY_BATCH_SIZE=25
```

## Ticketing Production Rollout

The ticket system is now production-shaped for conversation dashboarding, support
cases, external ticket links, webhook intake, and durable retry handling.

Apply the DB schema to the runtime database:

```bash
cd /Users/ijidailassa/projects/ruhu
make db-upgrade RUHU_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev
```

Run live provider verification against configured DB connections:

```bash
cd /Users/ijidailassa/projects/ruhu
make ticketing-verify
```

Process one retry batch manually:

```bash
cd /Users/ijidailassa/projects/ruhu
make ticketing-retry-once
```

Run the standalone retry worker loop:

```bash
cd /Users/ijidailassa/projects/ruhu
make ticketing-retry-worker
```

Per-connection webhook signature config lives in `provider_config` on each
ticketing connection:

- Zendesk:
  - `webhook_secret` or `webhook_secret_ref`
  - optional `webhook_tolerance_seconds`
- Jira:
  - `webhook_secret` or `webhook_secret_ref`
- Freshdesk:
  - no provider-native signature verification is currently wired
  - use the shared `RUHU_PROVIDER_SHARED_SECRET` fallback on the webhook route

If you want the API process itself to run retry loops, set:

```bash
export RUHU_TICKETING_RETRY_WORKER_ENABLED=true
export RUHU_TICKETING_RETRY_INTERVAL_SECONDS=60
export RUHU_TICKETING_RETRY_BATCH_SIZE=25
```

That in-process loop is optional. The cleaner production posture is a separate
worker process using `python -m ruhu.ticketing_worker worker`.

## LiveKit Adapter

The realtime voice provider shell is now isolated behind
[`src/ruhu/livekit_adapter.py`](/Users/ijidailassa/projects/ruhu/src/ruhu/livekit_adapter.py)
instead of keeping LiveKit-specific logic inline in the generic API layer.

Current rules:

- use `LiveKit Agents SDK v1.5.1`
- keep LiveKit as `provider/transport`, not as a business channel
- keep `conversation_id` and `realtime_session_id` as durable control-plane ids
- treat room identity only as `provider_session_id`
- only final transcript commits produce authoritative turns by default

To install the optional adapter dependency into the repo venv:

```bash
.venv/bin/pip install -e ".[livekit]"
```

The current implementation covers:

- LiveKit phone provider shell routes under `/providers/livekit/phone/calls/*`
- adapter config parsing from env
- explicit dispatch metadata in token issuance, with API-dispatch fallback support
- SDK isolation behind a dedicated adapter module
- public widget voice session start under `/public/widget/sessions/{conversation_id}/voice`
- worker callback routes under `/providers/livekit/voice/sessions/{realtime_session_id}/*`
- worker-side event replay route under `/providers/livekit/conversations/{conversation_id}/events`
- a worker bridge module in
  [`src/ruhu/livekit_worker.py`](/Users/ijidailassa/projects/ruhu/src/ruhu/livekit_worker.py)

The current session model now supports:

- room and token issuance for widget voice and phone starts
- `WorkerOptions(entrypoint_fnc=...)` worker runtime with `prewarm_fnc`
  and idle process controls
- `AgentServer` + `rtc_session(...)` worker registration behind the adapter
  boundary as an alternate SDK integration mode
- SDK-backed `AgentSession` wrapper creation behind the adapter boundary
- explicit `pipeline` vs `realtime_assisted` voice mode configuration
- transcript and lifecycle bridge calls from a LiveKit worker back into the
  durable control plane
- durable assistant-event replay for worker-side voice projection, including
  stale-output cutoff handling after interruptions/session replacement

Example worker bridge smoke check:

```bash
PYTHONPATH=src python -m ruhu.livekit_worker sdk-status --json
```

```bash
PYTHONPATH=src python -m ruhu.livekit_worker server-status --json
```

```bash
PYTHONPATH=src python -m ruhu.livekit_worker serve \
  --control-plane-base-url http://127.0.0.1:8010 \
  --provider-secret "$RUHU_PROVIDER_SHARED_SECRET" \
  --runtime-mode worker_options
```

Runtime selection:
- `worker_options`: old-Ruhu-compatible process runtime (`cli.run_app(WorkerOptions(...))`)
- `agent_server`: AgentServer wrapper runtime
- `auto` (default): prefer `worker_options`, fallback to `agent_server`

Old-Ruhu-style dev launcher is also available:

```bash
./scripts/run_agent_dev.sh \
  --control-plane-base-url http://127.0.0.1:8010 \
  --provider-secret "$RUHU_PROVIDER_SHARED_SECRET"
```

```bash
PYTHONPATH=src python -m ruhu.livekit_worker bridge-final-transcript \
  --control-plane-base-url http://127.0.0.1:8010 \
  --provider-secret "$RUHU_PROVIDER_SHARED_SECRET" \
  --realtime-session-id rs_example \
  --idempotency-key seg-1 \
  --text "Tell me about pricing." \
  --json
```

`bridge-final-transcript` now exits cleanly with structured JSON and status `1`
when the control plane rejects the transcript, for example after the target
voice session has already been disconnected or replaced.

For a real provider smoke against LiveKit itself, use:

```bash
PYTHONPATH=src python -m ruhu.realtime_smoke livekit \
  --conversation-id smoke-livekit-conv \
  --realtime-session-id smoke-livekit-rs \
  --dispatch-strategy api_dispatch \
  --json
```

That command issues a real transport grant and, by default, forces an
`api_dispatch` probe so the smoke verifies provider reachability instead of
only local token construction. Add `--skip-dispatch` if you only want the local
token issuance check.

For a real widget chat smoke against a running app, use:

```bash
PYTHONPATH=src python -m ruhu.realtime_smoke widget-chat \
  --base-url http://127.0.0.1:8010 \
  --agent-id sales_agent \
  --text "Tell me about pricing." \
  --json
```

That command creates a real public widget session, sends a real chat turn
through `/public/widget/sessions/.../messages`, and reports the assistant
messages and trace ids without printing the widget session token.

For a real widget voice smoke against a running app, use:

```bash
PYTHONPATH=src python -m ruhu.realtime_smoke widget-voice \
  --base-url http://127.0.0.1:8010 \
  --agent-id sales_agent \
  --participant-name "Smoke User" \
  --bridge-transcript \
  --provider-secret "$RUHU_PROVIDER_SHARED_SECRET" \
  --json
```

That command creates a real public widget session, starts a real widget voice
transport, optionally bridges a final transcript through the LiveKit provider
bridge, and then disconnects the widget voice session so the test leaves the
control plane clean.

If you want the repo dev server to inherit provider config from another local
env file without overriding this repo's own `.env.development`, set
`RUHU_DEV_ENV_FILE` before starting `uvicorn ruhu._dev_server:create_app
--factory`. The dev server will load `.env.development.local`, `.env.local`,
`.env.development`, `.env`, and then the extra `RUHU_DEV_ENV_FILE`, preserving
explicit shell exports the whole time. It also auto-runs Alembic migrations for
the unique runtime and auth database URLs it sees unless
`RUHU_DEV_AUTO_MIGRATE=0`.

For a real Meta WhatsApp outbound smoke:

```bash
PYTHONPATH=src python -m ruhu.realtime_smoke whatsapp \
  --phone-number-id "$PHONE_NUMBER_ID" \
  --recipient-id "$WHATSAPP_RECIPIENT_ID" \
  --text "Ruhu WhatsApp smoke check" \
  --json
```

All smoke commands read the normal `RUHU_*` runtime environment. Pass
`--env-file path/to/file` if you want to preload a specific env file before the
smoke runs.

Committed defaults also live in:

- [.env.example](/Users/ijidailassa/projects/ruhu/.env.example)
- [.env.development.example](/Users/ijidailassa/projects/ruhu/.env.development.example)

For a live invite + magic-link smoke send against the real SMTP transport:

```bash
cd /Users/ijidailassa/projects/ruhu
PYTHONPATH=src .venv/bin/python scripts/send_auth_email_smoke.py \
  --admin-email admin@example.com \
  --invite-email invitee@example.com \
  --magic-link-email member@example.com
```

The smoke sender loads `.env.development` by default, seeds a minimal admin/org
context in the auth database, calls the real `/organization/invitations` and
`/auth/magic-link/request` routes, and waits for retry-backed SMTP delivery to
settle. Use distinct invite and magic-link recipients.

## Database

The project now uses Postgres as the default local and development database.

Local default:

```text
postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev
```

Run the baseline migration with:

```bash
cd /Users/ijidailassa/projects/ruhu
.venv/bin/python -m alembic -c alembic.ini upgrade head
```

To create a new migration from updated models:

```bash
cd /Users/ijidailassa/projects/ruhu
.venv/bin/python -m alembic -c alembic.ini revision --autogenerate -m "describe change"
```

Alembic will use `RUHU_DATABASE_URL` when it is set; otherwise it falls back to
the `ruhu_runtime_dev` URL defined in [alembic.ini](/Users/ijidailassa/projects/ruhu/alembic.ini).

This repo intentionally uses its own local database name. Do not point it at
another repo's development database, because the Alembic histories are not
compatible.

The project now also ships a small local workflow:

```bash
cd /Users/ijidailassa/projects/ruhu
make install
make db-bootstrap
make db-upgrade
make db-revision MSG="describe change"
make auth-email-smoke ADMIN_EMAIL=admin@example.com INVITE_EMAIL=invitee@example.com MAGIC_LINK_EMAIL=member@example.com
make auth-ui-e2e
```

`make db-bootstrap` creates the local `ruhu_runtime_dev` database if it does not already
exist, then applies Alembic migrations using the canonical local Postgres URL.

Main routes:

- `GET /health`
- `GET /live`
- `GET /ready`
- `GET /login`
- `GET /signup`
- `GET /accept-invitation`
- `GET /app`
- `GET /account`
- `GET /internal/admin`
- `GET /auth/magic-link`
- `GET /auth/callback`
- `GET /playground`
- `GET /auth/invite/validate`
- `POST /auth/magic-link/request`
- `POST /auth/magic-link/verify`
- `POST /auth/oauth/google/start`
- `POST /auth/oauth/sso/start`
- `POST /auth/oauth/callback`
- `POST /auth/refresh`
- `POST /auth/logout`
- `POST /auth/invitations/accept`
- `PATCH /auth/me`
- `GET /auth/external-identities`
- `GET /auth/sso/config`
- `PUT /auth/sso/config`
- `DELETE /auth/sso/config`
- `GET /auth/me`
- `GET /auth/sessions`
- `DELETE /auth/sessions/current`
- `DELETE /auth/sessions/{session_id}`
- `GET /organization`
- `PATCH /organization`
- `POST /organization/auth/revoke-sessions`
- `GET /organization/invitations`
- `POST /organization/invitations`
- `DELETE /organization/invitations/{invitation_id}`
- `GET /organization/members`
- `GET /organization/members/{user_id}/sessions`
- `DELETE /organization/members/{user_id}/sessions`
- `POST /organization/members`
- `PATCH /organization/members/{user_id}`
- `DELETE /organization/members/{user_id}`
- `POST /api-keys` (client-generated secret material: send `name`, `key_hash`, `key_prefix`; plaintext is not returned by the API)
- `GET /internal/platform/health`
- `GET /internal/organizations`
- `GET /internal/users`
- `GET /internal/users/{user_id}/external-identities`
- `POST /internal/users/{user_id}/promote-superuser`
- `POST /internal/users/{user_id}/revoke-superuser`
- `GET /agents`
- `GET /agents/{agent_id}`
- `POST /conversations`
- `GET /conversations`
- `GET /conversations/{conversation_id}`
- `POST /conversations/{conversation_id}/turns`
- `GET /conversations/{conversation_id}/traces`

When auth is configured, the app factory also composes a persistent
SQLAlchemy identity store, an `AuthService`, an `AuthContextResolver`, and a
tenant-scoped repository factory on `app.state`. The project now treats a real
Postgres database URL as the canonical persistence target. Conversation and trace
endpoints are scoped to the authenticated organization when auth is enabled.
Organization-wide session invalidation is exposed through
`POST /organization/auth/revoke-sessions`; the reserved auth cutoff keys are not
accepted through the generic `PATCH /organization` settings path. The auth shell
also exposes self-session inventory and targeted revocation, plus invitation-
driven Google sign-in, enterprise SSO, and magic-link flows. Invitation and
magic-link issuance now render email templates separately from delivery and send
through a configured SMTP sender when `RUHU_SMTP_HOST` is set. Without SMTP configuration, the app falls back to
an in-memory dev outbox with redacted logging instead of returning raw auth
tokens over the API. SMTP delivery now uses a retry-aware transport wrapper, so
transient send failures are queued for retry instead of immediately failing the
request path. The auth shell also exposes current-user profile updates,
external-identity visibility, and a minimal superuser-only internal platform
surface for health and tenant visibility. The built-in auth UI now includes an
authenticated workspace shell at `/app` for profile, session, organization,
member, and invitation management, plus a superuser-only internal admin shell
at `/internal/admin`. The canonical auth journey regression suite is
`tests/test_auth_ui.py` plus `tests/test_auth_ui_flows.py`, which you can run
with `make auth-ui-e2e`.

For real browser coverage, the repo also has a separate Playwright tier in
`tests_playwright/`. Install the optional dependencies and browser runtime once:

```bash
make install
make playwright-install
```

Then run the browser suite with:

```bash
make auth-browser-e2e
```

Those tests use a local fake OAuth authorize route, so the Google and
enterprise SSO browser journeys stay entirely on localhost while still
exercising the real `/auth/callback` page and backend callback handler.
The Playwright tier now also includes widget voice lifecycle coverage in
[`tests_playwright/test_widget_voice_playwright.py`](/Users/ijidailassa/projects/ruhu/tests_playwright/test_widget_voice_playwright.py),
which runs the real widget routes against a local app while serving a fake
`/widget-livekit-client.js` module for deterministic connect/interruption
checks. Worker-side control-plane bridge coverage also exists in
[`tests/test_livekit_worker_cli.py`](/Users/ijidailassa/projects/ruhu/tests/test_livekit_worker_cli.py),
which validates `python -m ruhu.livekit_worker bridge-final-transcript`
against a live local app instead of only through mocks.

## Dev Playground

The API now serves a zero-build developer playground at `/playground`. It lets you:

- pick a loaded agent
- start a conversation
- send turns manually
- inspect current state and conversation JSON
- inspect full trace records after each turn

Once your app is running locally, open:

```text
http://127.0.0.1:8010/playground
```

## Eval Harness

The eval harness replays transcript fixtures against file-backed graphs and
interpreters.  CI suites live under `tests/_fixtures/data/evals/`; to run your
own eval suite, pass its JSON file and a root directory that its relative
``graph_file`` / ``transcript_file`` paths resolve against:

```bash
PYTHONPATH=src python -m ruhu.evals \
  --suite-file tests/_fixtures/data/evals/ci_suite.json \
  --root tests/_fixtures/data \
  --json
```

Add ``--model-path /path/to/gemma-4-E4B-it`` to use the local Gemma classifier.
