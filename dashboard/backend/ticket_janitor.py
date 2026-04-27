"""Ticket janitor — auto-releases timed-out ticket locks every 5 minutes (Feature 1.3)."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

JANITOR_INTERVAL_SECONDS = int(os.getenv("TICKET_JANITOR_INTERVAL", "300"))  # 5 min default

_janitor_started = False
_janitor_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def release_expired_locks(app=None) -> int:
    """Find and release all expired ticket locks.

    Returns the number of tickets released.
    Must run inside a Flask app context (pass app for context push, or call
    from an already-active context).
    """
    released = 0
    try:
        from models import db, Ticket, TicketActivity

        # Find all tickets whose lock has expired
        expired = db.session.execute(
            db.text("""
                SELECT id, locked_by, locked_at, COALESCE(lock_timeout_seconds, 1800) as timeout_secs
                FROM tickets
                WHERE locked_at IS NOT NULL
            """)
        ).fetchall()

        now_dt = datetime.now(timezone.utc)
        for row in expired:
            ticket_id = row[0]
            locked_by = row[1]
            locked_at = _parse_dt(row[2])
            timeout_secs = int(row[3] or 1800)
            if locked_at is None:
                continue
            if (locked_at.timestamp() + timeout_secs) > now_dt.timestamp():
                continue

            # Update via raw SQL to avoid SQLAlchemy CHECK constraint issues
            db.session.execute(
                db.text(
                    "UPDATE tickets SET locked_at = NULL, locked_by = NULL, updated_at = :now "
                    "WHERE id = :id AND locked_at IS NOT NULL"
                ),
                {"id": ticket_id, "now": now_dt},
            )

            activity = TicketActivity(
                id=str(uuid.uuid4()),
                ticket_id=ticket_id,
                actor="system:janitor",
                action="auto_release",
                payload=json.dumps({"previously_locked_by": locked_by}),
                created_at=now_dt,
            )
            db.session.add(activity)
            released += 1

        if released > 0:
            db.session.commit()
            print(f"[ticket_janitor] auto-released {released} expired lock(s)", flush=True)

    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        print(f"[ticket_janitor] ERROR in release_expired_locks: {exc}", flush=True)

    return released


def _janitor_loop(app):
    """Background loop — runs release_expired_locks every JANITOR_INTERVAL_SECONDS."""
    while True:
        time.sleep(JANITOR_INTERVAL_SECONDS)
        try:
            with app.app_context():
                release_expired_locks()
        except Exception as exc:
            print(f"[ticket_janitor] loop error: {exc}", flush=True)


def start_janitor_thread():
    """Start the janitor background thread (idempotent — safe to call multiple times)."""
    global _janitor_started

    with _janitor_lock:
        if _janitor_started:
            return
        _janitor_started = True

    # Import here to avoid circular import at module load
    from flask import current_app
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    t = threading.Thread(
        target=_janitor_loop,
        args=(app,),
        daemon=True,
        name="ticket-janitor",
    )
    t.start()
    print(f"[ticket_janitor] started (interval={JANITOR_INTERVAL_SECONDS}s)", flush=True)
