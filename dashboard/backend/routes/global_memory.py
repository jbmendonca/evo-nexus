"""Global Memory — persists extracted procedures and learnings across sessions.

Blueprint: /api/global-memory
Table:     global_memories (auto-migrated in app.py)
"""

import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_login import current_user, login_required
from models import db

bp = Blueprint("global_memory", __name__)


# ── Helpers ──────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "title": row[1],
        "content": row[2],
        "category": row[3],
        "source_agent": row[4],
        "source_user": row[5],
        "source_session_id": row[6],
        "tags": json.loads(row[7]) if row[7] else [],
        "created_at": row[8],
        "updated_at": row[9],
    }


# ── LIST ─────────────────────────────────────────────────────────

@bp.route("/api/global-memory", methods=["GET"])
@login_required
def list_memories():
    """List global memories with optional filters."""
    category = request.args.get("category")
    agent = request.args.get("agent")
    search = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))

    sql = "SELECT * FROM global_memories WHERE 1=1"
    params: list = []

    if category:
        sql += " AND category = ?"
        params.append(category)
    if agent:
        sql += " AND source_agent = ?"
        params.append(agent)
    if search:
        sql += " AND (title LIKE ? OR content LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])

    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.session.execute(db.text(sql), params).fetchall()
    memories = [_row_to_dict(r) for r in rows]

    # Total count
    count_sql = "SELECT COUNT(*) FROM global_memories WHERE 1=1"
    count_params: list = []
    if category:
        count_sql += " AND category = ?"
        count_params.append(category)
    if agent:
        count_sql += " AND source_agent = ?"
        count_params.append(agent)
    if search:
        count_sql += " AND (title LIKE ? OR content LIKE ?)"
        count_params.extend([f"%{search}%", f"%{search}%"])

    total = db.session.execute(db.text(count_sql), count_params).scalar() or 0

    return jsonify({"memories": memories, "total": total})


# ── CREATE ───────────────────────────────────────────────────────

@bp.route("/api/global-memory", methods=["POST"])
@login_required
def create_memory():
    """Manually create a global memory."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    category = data.get("category", "learning")

    if not title or not content:
        return jsonify({"error": "title and content are required"}), 400

    valid_categories = ("procedure", "learning", "solution", "config")
    if category not in valid_categories:
        return jsonify({"error": f"category must be one of: {', '.join(valid_categories)}"}), 400

    mem_id = str(uuid.uuid4())
    now = _utcnow()
    tags = json.dumps(data.get("tags", []))

    db.session.execute(
        db.text(
            "INSERT INTO global_memories "
            "(id, title, content, category, source_agent, source_user, source_session_id, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ),
        [
            mem_id,
            title,
            content,
            category,
            data.get("source_agent"),
            current_user.username,
            data.get("source_session_id"),
            tags,
            now,
            now,
        ],
    )
    db.session.commit()

    return jsonify({"id": mem_id, "status": "created"}), 201


# ── DELETE ───────────────────────────────────────────────────────

@bp.route("/api/global-memory/<mem_id>", methods=["DELETE"])
@login_required
def delete_memory(mem_id):
    """Delete a global memory by ID."""
    result = db.session.execute(
        db.text("DELETE FROM global_memories WHERE id = ?"), [mem_id]
    )
    db.session.commit()
    if result.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "deleted"})


# ── AUTO-EXTRACT ─────────────────────────────────────────────────

@bp.route("/api/global-memory/extract", methods=["POST"])
@login_required
def extract_memories():
    """Extract learnings and procedures from a chat session's messages.

    Expects JSON body:
      {
        "agent": "agent-name",
        "session_id": "...",
        "messages": [
            {"role": "user"|"assistant"|"system", "text": "...", "blocks": [...]}
        ]
      }

    Extraction heuristics:
    1. Tool-use blocks that completed successfully → category=procedure
    2. Assistant text blocks containing key learning patterns → category=learning
    3. System messages about configuration changes → category=config
    """
    data = request.get_json(silent=True) or {}
    agent = data.get("agent", "")
    session_id = data.get("session_id", "")
    messages = data.get("messages", [])

    if not messages:
        return jsonify({"extracted": 0, "memories": []})

    extracted = []
    now = _utcnow()
    user = current_user.username if current_user.is_authenticated else "system"

    for msg in messages:
        role = msg.get("role", "")

        # 1. Extract from tool-use blocks (procedures)
        if role == "assistant":
            blocks = msg.get("blocks", [])
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("done"):
                    tool_name = block.get("toolName", "")
                    tool_input = block.get("input", "")
                    result = block.get("result", "")

                    # Skip trivial tools
                    if tool_name in ("ping", "pong", ""):
                        continue

                    title = f"Procedimento: {tool_name}"
                    content = f"O agente @{agent} executou a ferramenta `{tool_name}`"
                    if tool_input:
                        # Truncate very long inputs
                        inp_preview = tool_input[:500] if isinstance(tool_input, str) else json.dumps(tool_input)[:500]
                        content += f"\n\nInput: ```\n{inp_preview}\n```"
                    if result:
                        res_preview = result[:500] if isinstance(result, str) else json.dumps(result)[:500]
                        content += f"\n\nResultado: ```\n{res_preview}\n```"

                    mem_id = str(uuid.uuid4())
                    extracted.append({
                        "id": mem_id,
                        "title": title,
                        "content": content,
                        "category": "procedure",
                        "source_agent": agent,
                        "source_user": user,
                        "source_session_id": session_id,
                        "tags": json.dumps([tool_name, agent]),
                        "created_at": now,
                        "updated_at": now,
                    })

            # 2. Extract from text blocks (learnings)
            for block in blocks:
                if block.get("type") != "text":
                    continue
                text = block.get("text", "")
                if len(text) < 100:
                    continue

                # Check for learning patterns
                learning_markers = [
                    "importante notar", "recomendação", "boas práticas",
                    "atenção:", "nota:", "dica:", "solução:",
                    "procedimento:", "passo a passo", "como fazer",
                    "configuração necessária", "importante:",
                    "learned", "best practice", "note:",
                    "solution:", "workaround:", "fix:",
                ]
                text_lower = text.lower()
                if any(marker in text_lower for marker in learning_markers):
                    # Extract a summary (first 200 chars as title)
                    title_preview = text[:150].replace("\n", " ").strip()
                    if len(text) > 150:
                        title_preview += "…"

                    mem_id = str(uuid.uuid4())
                    extracted.append({
                        "id": mem_id,
                        "title": f"Aprendizado: {title_preview}",
                        "content": text[:2000],
                        "category": "learning",
                        "source_agent": agent,
                        "source_user": user,
                        "source_session_id": session_id,
                        "tags": json.dumps([agent, "auto-extracted"]),
                        "created_at": now,
                        "updated_at": now,
                    })

    # Deduplicate by title similarity (avoid storing identical procedures)
    seen_titles = set()
    unique = []
    for m in extracted:
        key = m["title"][:80].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(m)

    # Persist
    for m in unique:
        db.session.execute(
            db.text(
                "INSERT INTO global_memories "
                "(id, title, content, category, source_agent, source_user, source_session_id, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                m["id"], m["title"], m["content"], m["category"],
                m["source_agent"], m["source_user"], m["source_session_id"],
                m["tags"], m["created_at"], m["updated_at"],
            ],
        )
    db.session.commit()

    return jsonify({
        "extracted": len(unique),
        "memories": [{"id": m["id"], "title": m["title"], "category": m["category"]} for m in unique],
    })
