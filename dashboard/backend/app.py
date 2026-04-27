"""Flask backend for the workspace dashboard â€” EvoNexus."""

import os
import sys
import secrets
import sqlite3
from pathlib import Path
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
from flask_login import LoginManager, current_user, login_user
from blueprint_registry import register_blueprints
from db_migrations import run_database_migrations
from runtime_config import load_dashboard_runtime_config, sqlite_path_from_uri
from structured_logging import emit_json_log, install_request_logging
from request_security import require_xhr
from session_security import attach_session_token

# Workspace root: two levels up from backend/
WORKSPACE = Path(__file__).resolve().parent.parent.parent

# Load .env from workspace root
load_dotenv(WORKSPACE / ".env")

# Add social-auth to path
sys.path.insert(0, str(WORKSPACE / "social-auth"))


_PRE_MIGRATION_BACKUP_DONE = False


def _schema_backup_required(db_path: str) -> bool:
    """Return True when the on-disk SQLite schema is behind the current code."""

    if not Path(db_path).exists():
        return False

    manual_tables = {
        "heartbeats",
        "heartbeat_runs",
        "heartbeat_triggers",
        "missions",
        "projects",
        "goals",
        "goal_tasks",
        "tickets",
        "ticket_comments",
        "ticket_activity",
        "knowledge_connections",
        "knowledge_connection_events",
        "knowledge_api_keys",
    }

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        existing_tables = {
            row[0]
            for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        # Any table tracked by SQLAlchemy metadata that is missing on disk means
        # db.create_all() will need to touch the schema on this boot.
        if set(db.metadata.tables.keys()).difference(existing_tables):
            return True
        if manual_tables.difference(existing_tables):
            return True

        roles_cols = {row[1] for row in cur.execute("PRAGMA table_info(roles)").fetchall()}
        if {"agent_access_json", "workspace_folders_json"}.difference(roles_cols):
            return True

        ticket_cols = set()
        if "tickets" in existing_tables:
            ticket_cols = {row[1] for row in cur.execute("PRAGMA table_info(tickets)").fetchall()}
        if {
            "source_agent",
            "source_session_id",
            "workspace_path",
            "memory_md_path",
            "thread_session_id",
            "message_count",
            "last_summary_at_message",
        }.difference(ticket_cols):
            return True

        return False
    except Exception as exc:
        emit_json_log("warning", "pre_migration_schema_inspection_failed", service="dashboard", error=str(exc))
        return False
    finally:
        if conn is not None:
            conn.close()


def _backup_before_migrations(db_path: str) -> None:
    """Create one workspace backup before the first detected schema migration."""

    global _PRE_MIGRATION_BACKUP_DONE
    if _PRE_MIGRATION_BACKUP_DONE:
        return
    _PRE_MIGRATION_BACKUP_DONE = True

    try:
        import backup as backup_module

        backup_module.backup_local()
        emit_json_log("info", "pre_migration_backup_created", service="dashboard", database_path=db_path)
    except SystemExit as exc:
        # backup.py exits(0) when there is nothing to back up.
        if exc.code not in (0, None):
            raise
    except Exception as exc:
        emit_json_log("warning", "pre_migration_backup_failed", service="dashboard", database_path=db_path, error=str(exc))

app = Flask(__name__, static_folder=None)
runtime_config = load_dashboard_runtime_config(WORKSPACE)
app.secret_key = runtime_config.secret_key
app.config["SQLALCHEMY_DATABASE_URI"] = runtime_config.database_uri
app.config["EVONEXUS_DATABASE_BACKEND"] = runtime_config.database_backend
app.config["EVONEXUS_DASHBOARD_PORT"] = runtime_config.dashboard_port
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=30)
# SameSite=Strict prevents cross-origin cookie riding (CSRF defense layer 1).
app.config["SESSION_COOKIE_SAMESITE"] = "Strict"

# --- JSON encoding for API responses ---
# With ensure_ascii=True (Flask default) jsonify escapes every non-ASCII
# character as \uXXXX. JSON parsers in the browser decode this correctly,
# but it clutters network logs and occasionally breaks naive consumers that
# look at raw bytes (e.g. grep over nginx access logs). Emit real UTF-8 so
# accented content ("JoÃ£o", "Mirandas LeilÃµes") stays readable end-to-end.
try:
    app.json.ensure_ascii = False          # type: ignore[attr-defined]
    app.json.mimetype = "application/json; charset=utf-8"  # type: ignore[attr-defined]
except AttributeError:
    # Flask <2.2 exposed this through app.config; keep compatibility.
    app.config["JSON_AS_ASCII"] = False

CORS(
    app,
    origins=runtime_config.cors_allowed_origins,
    supports_credentials=True,
    allow_headers=["Content-Type", "X-Requested-With", "X-CSRF-Token", "Authorization"],
    expose_headers=["X-CSRF-Token"],
)
install_request_logging(app, service="dashboard")

# --------------- Database ---------------
from models import db, User, needs_setup, seed_roles, seed_systems
db.init_app(app)

# Create tables on first run + enable WAL mode for concurrent reads
with app.app_context():
    _db_path = sqlite_path_from_uri(app.config["SQLALCHEMY_DATABASE_URI"])
    if _db_path is not None:
        _db_path_str = str(_db_path)
        if _schema_backup_required(_db_path_str):
            _backup_before_migrations(_db_path_str)

    run_database_migrations(app.config["SQLALCHEMY_DATABASE_URI"])

    if _db_path is not None:
        db.session.execute(db.text("PRAGMA journal_mode=WAL"))
        db.session.commit()

    seed_roles()
    seed_systems()
    # Sync trigger definitions from YAML config
    from routes.triggers import sync_triggers_from_yaml
    sync_triggers_from_yaml()

    # Sync heartbeats from YAML + start dispatcher thread
    try:
        from heartbeat_dispatcher import _sync_heartbeats_to_db, start_dispatcher_thread
        _sync_heartbeats_to_db()
        start_dispatcher_thread()
    except Exception as _hb_exc:
        emit_json_log("warning", "heartbeat_dispatcher_init_failed", service="dashboard", error=str(_hb_exc))

    # Start ticket janitor (auto-release timed-out locks)
    try:
        from ticket_janitor import start_janitor_thread
        start_janitor_thread()
    except Exception as _tj_exc:
        emit_json_log("warning", "ticket_janitor_init_failed", service="dashboard", error=str(_tj_exc))

    # Start Knowledge pool GC + health check threads
    try:
        from knowledge.connection_pool import start_gc_thread
        from knowledge.health_check import start_health_check_thread
        start_gc_thread()
        start_health_check_thread(lambda: app)
    except Exception as _kn_exc:
        emit_json_log("warning", "knowledge_background_threads_init_failed", service="dashboard", error=str(_kn_exc))

    # Start knowledge usage janitor (delete usage rows > 7 days)
    try:
        from knowledge.usage_janitor import start_janitor_thread as start_usage_janitor
        start_usage_janitor()
    except Exception as _uj_exc:
        emit_json_log("warning", "knowledge_usage_janitor_init_failed", service="dashboard", error=str(_uj_exc))

    # Start knowledge classify worker (async document classification â€” ADR-008)
    try:
        from knowledge.classify_worker import start_classify_worker
        _sqlite_db_path = sqlite_path_from_uri(app.config["SQLALCHEMY_DATABASE_URI"])
        start_classify_worker(str(_sqlite_db_path) if _sqlite_db_path is not None else app.config["SQLALCHEMY_DATABASE_URI"])
    except Exception as _cw_exc:
        emit_json_log("warning", "knowledge_classify_worker_init_failed", service="dashboard", error=str(_cw_exc))

    # Cleanup: remove old disabled share records (expired + disabled + older than 30 days)
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from models import FileShare as _FileShare
    _cutoff = _dt.now(_tz.utc) - _td(days=30)
    _FileShare.query.filter(
        _FileShare.enabled == False,  # noqa: E712
        _FileShare.created_at < _cutoff,
    ).delete()
    db.session.commit()

# --------------- Licensing (register-only, no heartbeat) â”€â”€â”€
from licensing import auto_register_if_needed

with app.app_context():
    auto_register_if_needed()

# --------------- Login Manager ---------------
login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "Authentication required"}), 401

# --------------- Auth Middleware ---------------
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/csrf",
    "/api/auth/needs-setup",
    "/api/auth/setup",
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/api/health/deep",
    "/api/config/workspace-status",
    "/api/version",
    "/api/version/check",
    "/api/agents/active",
}

def _try_api_token_auth():
    """Resolve an Authorization: Bearer <token> header against DASHBOARD_API_TOKEN.
    On match, log in the configured service user for the duration of this request.
    Returns True if a valid token was found and applied, False otherwise.
    """
    expected = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not expected:
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    provided = header[len("Bearer "):].strip()
    if not provided or not secrets.compare_digest(provided, expected):
        return False
    # Load service user: DASHBOARD_API_USER env var, defaults to first admin
    service_username = os.environ.get("DASHBOARD_API_USER", "").strip()
    user = None
    if service_username:
        user = User.query.filter_by(username=service_username, is_active=True).first()
    if user is None:
        user = User.query.filter_by(role="admin", is_active=True).order_by(User.id.asc()).first()
    if user is None:
        return False
    # Log in for this request only (no remember cookie)
    login_user(user, remember=False, fresh=False)
    return True


@app.before_request
def auth_middleware():
    path = request.path

    # Static assets and frontend
    if not path.startswith("/api/") and not path.startswith("/ws/"):
        return None

    # WebSocket â€” auth checked inside the handler
    if path.startswith("/ws/"):
        return None

    try:
        require_xhr(request)
    except Exception as exc:
        return jsonify({"error": getattr(exc, "description", "CSRF check failed.")}), getattr(exc, "code", 403)

    # Public API paths (exact match or prefix match for docs/webhooks/shares)
    if (
        path in PUBLIC_PATHS
        or path.startswith("/api/docs")
        or path.startswith("/api/triggers/webhook/")
        or (path.startswith("/api/shares/") and "/view" in path)
        or path.startswith("/api/knowledge/v1/")
    ):
        return None

    # Setup redirect â€” if no users, only allow setup endpoints
    if needs_setup():
        if path not in PUBLIC_PATHS:
            return jsonify({"error": "Setup required", "needs_setup": True}), 403

    # Try API token auth first (Bearer header) for headless agents / CLI tools
    if not current_user.is_authenticated:
        if _try_api_token_auth():
            return None

    # Require auth for all other API paths
    if not current_user.is_authenticated:
        return jsonify({"error": "Authentication required"}), 401


@app.after_request
def attach_security_headers(response):
    if request.path.startswith("/api/"):
        try:
            attach_session_token(response)
        except Exception:
            pass
    return response

# --------------- Register blueprints ---------------
register_blueprints(app)

def _get_local_version():
    """Read current version from pyproject.toml."""
    try:
        pyproject = WORKSPACE / "pyproject.toml"
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                return line.split('"')[1]
    except Exception:
        pass
    return "unknown"


@app.route("/api/version")
def api_version():
    """Return current version from pyproject.toml."""
    return {"version": _get_local_version()}


@app.route("/api/agents/active")
def api_agents_active():
    """Return currently active agents from hook-generated status file."""
    import json
    status_file = WORKSPACE / ".claude" / "agent-status.json"
    try:
        if status_file.is_file():
            data = json.loads(status_file.read_text())
            # Filter entries older than 10 minutes (stale)
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
            active = []
            for entry in data.get("active_agents", []):
                try:
                    started = datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00"))
                    if started > cutoff:
                        active.append(entry)
                except (KeyError, ValueError):
                    pass
            return {"active_agents": active, "last_updated": data.get("last_updated")}
    except Exception:
        pass
    return {"active_agents": [], "last_updated": None}


# --- Version check with 1h cache ---
_version_cache = {"data": None, "expires": 0}

@app.route("/api/version/check")
def api_version_check():
    """Compare local version against latest GitHub release (cached 1h)."""
    import time
    import requests as http_requests

    now = time.time()
    if _version_cache["data"] and now < _version_cache["expires"]:
        return _version_cache["data"]

    current = _get_local_version()
    result = {
        "current": current,
        "latest": None,
        "update_available": False,
        "release_url": None,
        "release_notes": None,
    }

    try:
        resp = http_requests.get(
            "https://api.github.com/repos/EvolutionAPI/evo-nexus/releases/latest",
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            latest = data.get("tag_name", "").lstrip("v")
            result["latest"] = latest
            result["release_url"] = data.get("html_url")
            result["release_notes"] = data.get("body", "")[:500]

            # Compare versions (semver-like: major.minor.patch)
            def parse_ver(v):
                try:
                    return tuple(int(x) for x in v.split("."))
                except (ValueError, AttributeError):
                    return (0, 0, 0)

            if parse_ver(latest) > parse_ver(current):
                result["update_available"] = True
    except Exception:
        pass

    _version_cache["data"] = result
    _version_cache["expires"] = now + 3600  # 1 hour
    return result

@app.route("/api/social-accounts")
def social_accounts():
    from env_manager import all_platforms_with_accounts
    return {"platforms": all_platforms_with_accounts()}

@app.route("/api/social-accounts/<platform>/<int:index>", methods=["DELETE"])
def delete_social_account(platform, index):
    from env_manager import delete_account, all_platforms_with_accounts
    delete_account(platform, index)
    return {"ok": True, "platforms": all_platforms_with_accounts()}

# --------------- Serve React build ---------------
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    full = FRONTEND_DIST / path
    if full.is_file():
        return send_from_directory(str(FRONTEND_DIST), path)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return send_from_directory(str(FRONTEND_DIST), "index.html")
    return {"error": "Frontend not built. Run npm build in frontend/"}, 404


if __name__ == "__main__":
    # Read the dashboard port from the normalized runtime config.
    port = runtime_config.dashboard_port
    # Scheduler runs as a standalone process (scheduler.py) started by start-services.sh.
    # A thread here would create a duplicate instance â€” all routines would fire 2-3x.
    # One-off scheduled tasks (ScheduledTask model) are checked by the standalone scheduler
    # via _run_pending_tasks, which is called from its own loop.
    import threading

    def _run_pending_tasks():
        """Check for pending scheduled tasks and execute them."""
        from datetime import datetime as _dt, timezone as _tz
        from models import ScheduledTask

        try:
            now = _dt.now(_tz.utc)
            pending = ScheduledTask.query.filter(
                ScheduledTask.status == "pending",
                ScheduledTask.scheduled_at <= now,
            ).all()

            for task in pending:
                log_path = WORKSPACE / "ADWs" / "logs" / "scheduler.log"
                with open(log_path, "a") as log:
                    log.write(f"  [{_dt.now().strftime('%H:%M')}] Running scheduled task #{task.id}: {task.name}\n")

                t = threading.Thread(target=_execute_task_with_context, args=(task.id,), daemon=True)
                t.start()
        except Exception:
            pass

    def _execute_task_with_context(task_id):
        with app.app_context():
            from routes.tasks import _execute_task
            _execute_task(task_id)

    def _poll_scheduled_tasks():
        """Lightweight thread that only polls ScheduledTask â€” no routine scheduling."""
        import time as _time
        while True:
            with app.app_context():
                _run_pending_tasks()
            _time.sleep(30)

    task_thread = threading.Thread(target=_poll_scheduled_tasks, daemon=True, name="task-poller")
    task_thread.start()

    app.run(host="0.0.0.0", port=port, debug=False)
