SHELL := /bin/bash

VENV ?= .venv
BOOTSTRAP_PYTHON ?= python3
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUHU_DATABASE_URL ?= postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev
RUHU_AUTH_DATABASE_URL ?= $(RUHU_DATABASE_URL)
ALEMBIC := $(PYTHON) -m alembic -c alembic.ini
RELEASE_HYGIENE_VENV ?= /tmp/ruhu-release-hygiene-venv

.PHONY: venv install release-hygiene db-bootstrap db-upgrade db-revision auth-email-smoke atlas-readiness-smoke atlas-readiness-google-smoke realtime-livekit-smoke realtime-widget-chat-smoke realtime-widget-voice-smoke realtime-whatsapp-smoke auth-ui-e2e playwright-install auth-browser-e2e ticketing-verify ticketing-retry-once ticketing-retry-worker journey-runtime-worker test ratchet openapi-types

ratchet:
	python3 scripts/ratchets/check_line_budgets.py

openapi-types: $(PYTHON)
	PYTHONPATH=src $(PYTHON) scripts/export_openapi.py
	cd frontend && npm run generate:api-types

$(PYTHON):
	$(BOOTSTRAP_PYTHON) -m venv $(VENV)

venv: $(PYTHON)

install: $(PYTHON)
	$(PIP) install -U pip
	$(PIP) install -e '.[api,dev,browser-e2e]'

release-hygiene:
	bash scripts/release_hygiene_smoke.sh "$(RELEASE_HYGIENE_VENV)"

db-bootstrap:
	bash scripts/bootstrap_ruhu_dev.sh

db-upgrade: $(PYTHON)
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" RUHU_AUTH_DATABASE_URL="$(RUHU_AUTH_DATABASE_URL)" $(ALEMBIC) upgrade head

db-revision: $(PYTHON)
	@if [ -z "$(MSG)" ]; then echo 'MSG is required, for example: make db-revision MSG="add auth_magic_links"'; exit 1; fi
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" RUHU_AUTH_DATABASE_URL="$(RUHU_AUTH_DATABASE_URL)" $(ALEMBIC) revision --autogenerate -m "$(MSG)"

auth-email-smoke: $(PYTHON)
	@if [ -z "$(ADMIN_EMAIL)" ]; then echo 'ADMIN_EMAIL is required'; exit 1; fi
	@if [ -z "$(INVITE_EMAIL)" ] && [ -z "$(MAGIC_LINK_EMAIL)" ]; then echo 'INVITE_EMAIL or MAGIC_LINK_EMAIL is required'; exit 1; fi
	PYTHONPATH=src $(PYTHON) scripts/send_auth_email_smoke.py --admin-email "$(ADMIN_EMAIL)" $(if $(INVITE_EMAIL),--invite-email "$(INVITE_EMAIL)") $(if $(MAGIC_LINK_EMAIL),--magic-link-email "$(MAGIC_LINK_EMAIL)")

atlas-readiness-smoke: db-upgrade
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" RUHU_AUTH_DATABASE_URL="$(RUHU_AUTH_DATABASE_URL)" $(PYTHON) scripts/atlas_readiness_smoke.py $(if $(ORG_ID),--organization-id "$(ORG_ID)") $(if $(AGENT_ID),--agent-id "$(AGENT_ID)")

atlas-readiness-google-smoke: db-upgrade
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" RUHU_AUTH_DATABASE_URL="$(RUHU_AUTH_DATABASE_URL)" $(PYTHON) scripts/atlas_readiness_smoke.py --require-google $(if $(ORG_ID),--organization-id "$(ORG_ID)") $(if $(AGENT_ID),--agent-id "$(AGENT_ID)") $(if $(VOICE_AUDIO_URI),--voice-audio-uri "$(VOICE_AUDIO_URI)") $(if $(VOICE_LANGUAGE),--voice-language "$(VOICE_LANGUAGE)") $(if $(REQUIRE_REAL_VOICE),--require-real-voice)

realtime-livekit-smoke: $(PYTHON)
	PYTHONPATH=src $(PYTHON) -m ruhu.realtime_smoke livekit $(if $(ENV_FILE),--env-file "$(ENV_FILE)") $(if $(CONVERSATION_ID),--conversation-id "$(CONVERSATION_ID)") $(if $(REALTIME_SESSION_ID),--realtime-session-id "$(REALTIME_SESSION_ID)") $(if $(CHANNEL),--channel "$(CHANNEL)") $(if $(PARTICIPANT_IDENTITY),--participant-identity "$(PARTICIPANT_IDENTITY)") $(if $(PARTICIPANT_NAME),--participant-name "$(PARTICIPANT_NAME)") $(if $(METADATA_JSON),--metadata-json '$(METADATA_JSON)') $(if $(SKIP_DISPATCH),--skip-dispatch) --dispatch-strategy "$(or $(DISPATCH_STRATEGY),api_dispatch)" --json

realtime-widget-chat-smoke: $(PYTHON)
	PYTHONPATH=src $(PYTHON) -m ruhu.realtime_smoke widget-chat $(if $(ENV_FILE),--env-file "$(ENV_FILE)") --base-url "$(or $(BASE_URL),http://127.0.0.1:8010)" --agent-id "$(or $(AGENT_ID),sales_agent)" $(if $(CONVERSATION_ID),--conversation-id "$(CONVERSATION_ID)") $(if $(SESSION_TOKEN),--session-token "$(SESSION_TOKEN)") $(if $(TEXT),--text "$(TEXT)") --json

realtime-widget-voice-smoke: $(PYTHON)
	PYTHONPATH=src $(PYTHON) -m ruhu.realtime_smoke widget-voice $(if $(ENV_FILE),--env-file "$(ENV_FILE)") --base-url "$(or $(BASE_URL),http://127.0.0.1:8010)" --agent-id "$(or $(AGENT_ID),sales_agent)" $(if $(CONVERSATION_ID),--conversation-id "$(CONVERSATION_ID)") $(if $(SESSION_TOKEN),--session-token "$(SESSION_TOKEN)") $(if $(PARTICIPANT_IDENTITY),--participant-identity "$(PARTICIPANT_IDENTITY)") $(if $(PARTICIPANT_NAME),--participant-name "$(PARTICIPANT_NAME)") $(if $(METADATA_JSON),--metadata-json '$(METADATA_JSON)') $(if $(PROVIDER_SECRET),--provider-secret "$(PROVIDER_SECRET)") $(if $(TEXT),--text "$(TEXT)") $(if $(BRIDGE_TRANSCRIPT),--bridge-transcript) $(if $(SKIP_DISCONNECT),--skip-disconnect) --json

realtime-whatsapp-smoke: $(PYTHON)
	@if [ -z "$(PHONE_NUMBER_ID)" ]; then echo 'PHONE_NUMBER_ID is required'; exit 1; fi
	@if [ -z "$(RECIPIENT_ID)" ]; then echo 'RECIPIENT_ID is required'; exit 1; fi
	PYTHONPATH=src $(PYTHON) -m ruhu.realtime_smoke whatsapp $(if $(ENV_FILE),--env-file "$(ENV_FILE)") --phone-number-id "$(PHONE_NUMBER_ID)" --recipient-id "$(RECIPIENT_ID)" $(if $(TEXT),--text "$(TEXT)") --json

auth-ui-e2e: $(PYTHON)
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTEST) -q -p no:cacheprovider tests/test_auth_ui.py tests/test_auth_ui_flows.py

playwright-install: $(PYTHON)
	PYTHONPATH=src $(PYTHON) -m playwright install chromium

auth-browser-e2e: $(PYTHON)
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src $(PYTEST) -q -p no:cacheprovider tests_playwright --browser chromium

ticketing-verify: $(PYTHON)
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" $(PYTHON) -m ruhu.ticketing_worker verify-connections $(if $(ORG_ID),--organization-id "$(ORG_ID)") $(if $(CONNECTION_ID),--connection-id "$(CONNECTION_ID)") $(if $(INCLUDE_DISABLED),--include-disabled) --json

ticketing-retry-once: $(PYTHON)
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" $(PYTHON) -m ruhu.ticketing_worker retry-once $(if $(ORG_ID),--organization-id "$(ORG_ID)") --batch-size "$(or $(BATCH_SIZE),25)" $(if $(FORCE),--force) --json

ticketing-retry-worker: $(PYTHON)
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" $(PYTHON) -m ruhu.ticketing_worker worker $(if $(ORG_ID),--organization-id "$(ORG_ID)") --batch-size "$(or $(BATCH_SIZE),25)" --interval-seconds "$(or $(INTERVAL_SECONDS),60)" $(if $(MAX_RUNS),--max-runs "$(MAX_RUNS)") --json

journey-runtime-worker: $(PYTHON)
	PYTHONPATH=src RUHU_DATABASE_URL="$(RUHU_DATABASE_URL)" $(PYTHON) -m ruhu.journey_worker worker $(if $(ORG_ID),--organization-id "$(ORG_ID)") --max-jobs "$(or $(MAX_JOBS),10)" --interval-seconds "$(or $(INTERVAL_SECONDS),2)" $(if $(MAX_RUNS),--max-runs "$(MAX_RUNS)") $(if $(RUN_SCHEDULER),--run-scheduler) --json

test: $(PYTHON)
	PYTHONPATH=src $(PYTEST) tests -q
