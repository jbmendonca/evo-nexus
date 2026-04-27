#!/usr/bin/env bash
# ============================================================================
# start-dashboard.sh — multi-process entrypoint for the dashboard container.
#
# The dashboard needs TWO processes running simultaneously:
#   * Flask backend        → :8080   (/api/*, static SPA, OAuth, Providers...)
#   * Node terminal-server → :32352  (/terminal/*, embedded CLI sessions)
#
# The React frontend calls /terminal/* on the same origin and expects the
# reverse proxy (Traefik) to route it to :32352. If the terminal-server is
# not running inside the container, every "open agent chat" click fails
# with "Could not reach terminal-server".
#
# This wrapper starts both processes, but supervises the terminal-server
# separately. If the terminal dies, it is restarted in-place. If Flask dies,
# the container exits so Docker/Swarm can restart the whole stack.
# ============================================================================
set -euo pipefail

TERMINAL_PORT="${TERMINAL_SERVER_PORT:-32352}"
FLASK_PORT="${EVONEXUS_PORT:-8080}"

echo "[start-dashboard] terminal-server on :${TERMINAL_PORT}, Flask on :${FLASK_PORT}"

# ----------------------------------------------------------------------------
# Pre-seed Claude Code global settings so the first-run theme/onboarding
# prompts are skipped on every new agent terminal. Each agent runs in its
# own working directory, which Claude Code treats as a separate project —
# without this, the user has to pick a theme on every single agent.
# Only writes the file if it doesn't already exist (preserves user choices).
# ----------------------------------------------------------------------------
mkdir -p /root/.claude
if [ ! -f /root/.claude/settings.json ]; then
    echo "[start-dashboard] seeding /root/.claude/settings.json with default theme"
    cat > /root/.claude/settings.json <<'EOF'
{
  "theme": "dark",
  "hasCompletedOnboarding": true,
  "hasSeenWelcome": true,
  "telemetry": false
}
EOF
fi

# ----------------------------------------------------------------------------
# Restore /root/.claude.json from the most recent backup when missing.
#
# Claude Code's main config (theme, OAuth tokens, per-project state) lives
# at /root/.claude.json — a SIBLING of the /root/.claude/ directory, NOT
# inside it. The Swarm volume mounts /root/.claude/, so /root/.claude.json
# sits in the container's writable layer and is wiped on every redeploy.
# Result: theme picker and onboarding reappear on every release.
#
# Claude Code itself writes timestamped backups into /root/.claude/backups/
# (which IS in the volume). We just need to restore the latest on startup
# if the main file is missing. If no backup exists either, seed a minimal
# config so the first-run prompts are skipped.
# ----------------------------------------------------------------------------
if [ ! -f /root/.claude.json ]; then
    latest_backup=$(ls -t /root/.claude/backups/.claude.json.backup.* 2>/dev/null | head -n1 || true)
    if [ -n "${latest_backup:-}" ] && [ -f "${latest_backup}" ]; then
        echo "[start-dashboard] restoring /root/.claude.json from ${latest_backup}"
        cp "${latest_backup}" /root/.claude.json
    else
        echo "[start-dashboard] seeding minimal /root/.claude.json (no backup found)"
        cat > /root/.claude.json <<'EOF'
{
  "theme": "dark",
  "hasCompletedOnboarding": true,
  "hasSeenWelcome": true,
  "bypassPermissionsModeAccepted": true,
  "telemetry": false
}
EOF
    fi
fi

SHUTTING_DOWN=0
TERMINAL_PID=""
TERMINAL_SUPERVISOR_PID=""
FLASK_PID=""

terminal_supervisor() {
    trap 'SHUTTING_DOWN=1; if [ -n "${TERMINAL_PID}" ]; then kill "${TERMINAL_PID}" 2>/dev/null || true; fi' EXIT INT TERM
    while [ "${SHUTTING_DOWN}" -eq 0 ]; do
        node /workspace/dashboard/terminal-server/bin/server.js --port "${TERMINAL_PORT}" &
        TERMINAL_PID=$!
        echo "[start-dashboard] terminal-server spawned (pid=${TERMINAL_PID})"

        if wait "${TERMINAL_PID}"; then
            TERMINAL_EXIT=0
        else
            TERMINAL_EXIT=$?
        fi

        if [ "${SHUTTING_DOWN}" -ne 0 ]; then
            break
        fi

        echo "[start-dashboard] terminal-server exited with code ${TERMINAL_EXIT}; restarting in 2s"
        sleep 2
    done
}

# Start terminal-server supervisor in the background
terminal_supervisor &
TERMINAL_SUPERVISOR_PID=$!

# Start Flask in the background
uv run python /workspace/dashboard/backend/app.py &
FLASK_PID=$!

# When this script exits for any reason, kill both supervisors/processes.
# shellcheck disable=SC2317  # invoked by trap below
cleanup() {
    SHUTTING_DOWN=1
    echo "[start-dashboard] shutting down (supervisor=${TERMINAL_SUPERVISOR_PID}, flask=${FLASK_PID})"
    kill "${TERMINAL_SUPERVISOR_PID}" "${FLASK_PID}" 2>/dev/null || true
    wait "${TERMINAL_SUPERVISOR_PID}" 2>/dev/null || true
    wait "${FLASK_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for either Flask or the supervisor to exit. If Flask exits, cleanup
# stops the terminal supervisor; if the supervisor dies, cleanup takes Flask
# down too so the container does not stay half-alive.
wait -n
EXIT_CODE=$?
echo "[start-dashboard] a child process exited with code ${EXIT_CODE}"
exit "${EXIT_CODE}"
