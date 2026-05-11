"""
terminal_proxy.py — Proxy reverso Flask para o terminal-server Node.js

Encaminha todas as requisições HTTP de /terminal/* para o terminal-server
interno rodando na porta 32352.

Para WebSocket (/terminal/ws), usa flask-sock + websocket-client para
bridge bidirecional. websocket-client já é dependência transitiva do
flask-sock e requests; caso não esteja disponível, o endpoint WS retorna
501 graciosamente sem crashar o servidor.

Registrado em app.py como blueprint com url_prefix='/terminal'.
"""
import os
import sys
import threading
import requests
from flask import Blueprint, request, Response, stream_with_context

TERMINAL_PORT = int(os.environ.get("TERMINAL_SERVER_PORT", 32352))
TERMINAL_BASE = f"http://127.0.0.1:{TERMINAL_PORT}"
TERMINAL_WS   = f"ws://127.0.0.1:{TERMINAL_PORT}"

bp = Blueprint("terminal_proxy", __name__, url_prefix="/terminal")

# ── WS bridge (opcional — requer websocket-client) ────────────────────────────
_sock_initialized = False

def init_sock(app):
    """
    Inicializa o WebSocket bridge /terminal/ws via flask-sock.
    Chamado pelo app.py após register_blueprint.
    Se websocket-client não estiver disponível, o WS não é registrado mas
    o HTTP proxy continua funcionando normalmente.
    """
    global _sock_initialized
    if _sock_initialized:
        return
    _sock_initialized = True

    try:
        from flask_sock import Sock
        import websocket as _ws_client

        sock = Sock(app)

        @sock.route("/terminal/ws")
        def terminal_ws(ws):
            """Bridge bidirecional browser ↔ terminal-server."""
            try:
                qs = request.query_string.decode('utf-8')
                upstream_url = f"{TERMINAL_WS}/ws"
                if qs:
                    upstream_url += f"?{qs}"
                
                upstream = _ws_client.WebSocket()
                upstream.connect(upstream_url)
                print(f"[terminal_proxy] WS upstream connected to {upstream_url}", file=sys.stderr)
            except Exception as e:
                print(f"[terminal_proxy] WS upstream connect error: {e}", file=sys.stderr)
                try:
                    ws.close(message=f"upstream error: {e}")
                except Exception:
                    pass
                return

            stop = threading.Event()

            def forward_up():
                """Browser → upstream (terminal-server)."""
                try:
                    while not stop.is_set():
                        try:
                            data = ws.receive(timeout=30)
                        except Exception:
                            break
                        if data is None:
                            break
                        try:
                            # simple-websocket gives str for text, bytes for binary
                            upstream.send(data)
                        except Exception:
                            break
                except Exception:
                    pass
                finally:
                    stop.set()

            def forward_down():
                """Upstream (terminal-server) → browser."""
                try:
                    while not stop.is_set():
                        try:
                            data = upstream.recv()
                        except Exception:
                            break
                        if not data:
                            break
                        try:
                            # websocket-client recv() returns str for text frames
                            ws.send(data)
                        except Exception:
                            break
                except Exception:
                    pass
                finally:
                    stop.set()

            t_up   = threading.Thread(target=forward_up,   daemon=True)
            t_down = threading.Thread(target=forward_down, daemon=True)
            t_up.start()
            t_down.start()
            t_up.join()
            t_down.join()
            try:
                upstream.close()
            except Exception:
                pass

    except ImportError as e:
        print(f"[terminal_proxy] WS bridge desabilitado: {e}")


# ── HTTP proxy ─────────────────────────────────────────────────────────────────
_SKIP_HEADERS = {"host", "transfer-encoding", "content-length", "connection"}


def _proxy(subpath: str) -> Response:
    """Encaminha uma requisição HTTP para o terminal-server."""
    url = f"{TERMINAL_BASE}/{subpath}"
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers if k.lower() not in _SKIP_HEADERS},
            data=request.get_data(),
            params=request.args,
            stream=True,
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        return Response(
            '{"error":"terminal-server unavailable","detail":"port 32352 not reachable"}',
            status=503,
            content_type="application/json",
        )
    except Exception as exc:
        return Response(
            f'{{"error":"proxy error","detail":"{exc}"}}',
            status=502,
            content_type="application/json",
        )

    excluded = {"content-encoding", "transfer-encoding", "connection", "keep-alive"}
    headers  = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]

    return Response(
        stream_with_context(resp.iter_content(chunk_size=4096)),
        status=resp.status_code,
        headers=headers,
        content_type=resp.headers.get("Content-Type", "application/json"),
    )


@bp.route("/", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@bp.route("/<path:subpath>",             methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy(subpath: str):
    return _proxy(subpath)
