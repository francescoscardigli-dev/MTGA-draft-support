"""UI web locale (Fase 8): FastAPI + WebSocket sopra la sessione live della Fase 7.

Avvio: ``python -m draftbot ui [--log Player.log] [--port 8765]``, poi aprire http://127.0.0.1:8765.
Il server ascolta solo su 127.0.0.1: non e' raggiungibile da altri computer.

- un thread segue Player.log e passa le righe a ``LiveSession``;
- gli eventi (pick, build, status, error) vanno a tutti i browser connessi via WebSocket ``/ws``;
- ``GET /api/state``: ultimo pick e ultima build (per chi si connette a draft iniziato);
- ``GET /api/drafts``: draft letti dal log; ``GET /api/review/{draft_id}``: revisione post-draft.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..live import LiveSession, LogTailer
from ..sources.seventeenlands import ATTRIBUTION

STATIC = Path(__file__).resolve().parent / "static"


class Hub:
    """Collega il thread del log ai client WebSocket (asyncio)."""

    def __init__(self, echo: bool = False) -> None:
        self.echo = echo
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queues: set[asyncio.Queue] = set()
        self.last: dict[str, dict] = {}
        self.messages: list[str] = []

    def publish(self, ev: dict) -> None:
        t = ev.get("type")
        if t in ("pick", "build"):
            self.last[t] = ev
            if t == "pick":
                self.last.pop("build", None) if ev.get("pack") == 1 and ev.get("pick") == 1 else None
        else:
            self.messages = (self.messages + [ev.get("message", "")])[-20:]
            if self.echo:
                print(time.strftime("%H:%M:%S"), ev.get("message", ""), flush=True)
        if self.loop is None:
            return
        for q in list(self.queues):
            self.loop.call_soon_threadsafe(q.put_nowait, ev)


def create_app(log_path: Path | None, set_code: str | None = None, fmt: str | None = None,
               poll: float = 0.5, from_start: bool = True, session: LiveSession | None = None,
               before: list[Path] | None = None, echo: bool = False) -> FastAPI:
    """``before``: log letti una volta prima di seguire ``log_path`` (``Player-prev.log``: dopo un riavvio di
    Arena il draft appena fatto e' li'). ``echo``: stampa nel terminale i messaggi di stato."""
    hub = Hub(echo=echo)
    sess = session or LiveSession(set_code, fmt)
    sess.on_event(hub.publish)
    lock = threading.Lock()
    stop = threading.Event()

    def tail_loop() -> None:
        try:
            sess.preload()
        except Exception as e:
            hub.publish({"type": "error", "message": f"errore caricando il motore: {e}"})
        if from_start:
            for p in before or []:
                if p.is_file() and p != log_path:
                    hub.publish({"type": "status", "message": f"leggo il log precedente: {p}"})
                    try:
                        with lock:
                            sess.feed(p.read_text(encoding="utf-8", errors="replace").split("\n"))
                    except Exception as e:
                        hub.publish({"type": "error", "message": f"errore leggendo {p}: {e}"})
        tail = LogTailer(log_path, from_start=from_start)
        found = None
        while not stop.is_set():
            exists = log_path.is_file()
            if exists != found:
                found = exists
                hub.publish({"type": "status" if exists else "error",
                             "message": (f"seguo il log: {log_path}" if exists else
                                         f"log non trovato: {log_path} (in Arena: Opzioni > Account > "
                                         f"Detailed Logs; oppure usa --log)")})
            try:
                lines = tail.read_new()
                if lines:
                    with lock:
                        sess.feed(lines)
            except Exception as e:  # il server resta su anche se una riga manda in errore il motore
                hub.publish({"type": "error", "message": f"errore leggendo il log: {e}"})
            stop.wait(poll)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.loop = asyncio.get_running_loop()
        th = None
        if log_path is not None:
            th = threading.Thread(target=tail_loop, daemon=True)
            th.start()
        yield
        stop.set()

    app = FastAPI(title="draftbot", lifespan=lifespan)
    app.state.hub, app.state.session, app.state.lock = hub, sess, lock

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse({"log": str(log_path) if log_path else None, "pick": hub.last.get("pick"),
                             "build": hub.last.get("build"), "messages": hub.messages,
                             "attribution": ATTRIBUTION})

    @app.get("/api/drafts")
    def drafts() -> list[dict]:
        with lock:
            out = []
            for d in sess.parser.results():
                out.append({"draft_id": d.draft_id, "event": d.event_name, "set": d.set_code,
                            "format": d.fmt.name if d.fmt else None, "picks": len(d.picks)})
            return out

    @app.get("/api/review/{draft_id}")
    def review(draft_id: str) -> dict:
        from ..config import load_set
        from ..engine.review import review_draft
        with lock:
            d = next((x for x in sess.parser.results() if x.draft_id == draft_id), None)
            if d is None:
                raise HTTPException(404, "draft non trovato nel log")
            set_code = sess.forced[0] or d.set_code
            fmt_name = sess.forced[1] or (d.fmt.name if d.fmt else None)
            if not set_code or not fmt_name:
                raise HTTPException(400, "set o formato del draft non riconosciuti")
            eng = sess.engine(load_set(set_code).code, fmt_name)
            if eng is None:
                raise HTTPException(500, "motore non disponibile per questo set")
            return review_draft(eng, d)

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        q: asyncio.Queue = asyncio.Queue()
        hub.queues.add(q)
        try:
            for t in ("pick", "build"):
                if t in hub.last:
                    await socket.send_json(hub.last[t])
            while True:
                ev = await q.get()
                await socket.send_json(ev)
        except WebSocketDisconnect:
            pass
        finally:
            hub.queues.discard(q)

    return app


def serve(log_path: Path | None, set_code: str | None, fmt: str | None, port: int = 8765,
          from_start: bool = True, before: list[Path] | None = None) -> int:
    import uvicorn
    app = create_app(log_path, set_code, fmt, from_start=from_start, before=before, echo=True)
    print(f"UI su http://127.0.0.1:{port}  (log: {log_path or 'nessuno'}; Ctrl+C per uscire)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0
