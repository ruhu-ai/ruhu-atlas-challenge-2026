#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -n "${AGENT_PYTHON:-}" ] && [ -x "$AGENT_PYTHON" ]; then
  PYTHON="$AGENT_PYTHON"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
  PYTHON="${VIRTUAL_ENV}/bin/python"
else
  echo "Python not found. Expected .venv/bin/python, AGENT_PYTHON, or \$VIRTUAL_ENV/bin/python." >&2
  exit 1
fi

if [ -x "$PYTHON" ]; then
  echo "run_agent_dev using python: $("$PYTHON" -c 'import sys; print(sys.executable)' )"
  echo "run_agent_dev python version: $("$PYTHON" -V 2>&1)"
fi

if [ "$PYTHON" != ".venv/bin/python" ] && [ ! -x ".venv/bin/python" ]; then
  echo "run_agent_dev warning: running LiveKit worker outside .venv python (selected: ${PYTHON})" >&2
fi

if [ "$PYTHON" = ".venv/bin/python" ]; then
  export VIRTUAL_ENV="$ROOT_DIR/.venv"
  export PATH="$VIRTUAL_ENV/bin:$PATH"
fi

export PYTHONPATH="${PYTHONPATH:-src}"
export AGENT_NUM_IDLE_PROCESSES="${AGENT_NUM_IDLE_PROCESSES:-1}"
export RUHU_LIVEKIT_RUNTIME_MODE="${RUHU_LIVEKIT_RUNTIME_MODE:-worker_options}"
RESTART_DELAY="${AGENT_RESTART_DELAY_SECONDS:-3}"

child_pid=""
stop_requested=0

cleanup() {
  stop_requested=1
  if [ -n "$child_pid" ]; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM

while true; do
  "$PYTHON" -m ruhu.livekit_worker serve "$@" &
  child_pid=$!

  if wait "$child_pid"; then
    exit_code=0
  else
    exit_code=$?
  fi
  child_pid=""

  if [ "$stop_requested" -eq 1 ]; then
    exit 0
  fi

  echo "LiveKit worker exited with status ${exit_code}; restarting in ${RESTART_DELAY}s..." >&2
  sleep "$RESTART_DELAY"
done
