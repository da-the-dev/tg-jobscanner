"""FastAPI app so the dashboard can write application status back to the DB.

Bound to loopback by default and unauthenticated — it is a single-user local
tool. CORS is wide open on purpose: a dashboard.html opened straight from disk
has origin "null" and still needs to flush its queued changes here.
"""
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import dashboard
from .models import STATUSES

Status = Literal[STATUSES]  # type: ignore[valid-type]


class Change(BaseModel):
    hash: str = Field(min_length=1)
    # Deliberately not a Literal: an unknown status must be rejected per item by
    # the DB layer, not 422 the whole batch and wedge the browser's retry queue.
    status: str | None = Field(default=None, examples=list(STATUSES))
    note: str | None = None


class StatusRequest(BaseModel):
    """Either a single change or a batch flushed from the browser queue."""
    changes: list[Change] | None = None
    hash: str | None = None
    status: str | None = None
    note: str | None = None

    def items(self):
        if self.changes is not None:
            return self.changes
        if self.hash:
            return [Change(hash=self.hash, status=self.status, note=self.note)]
        return []


def create_app(cfg, db, log=print):
    app = FastAPI(title="jobscan", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    app.state.cfg, app.state.db, app.state.log = cfg, db, log

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        html, _ = dashboard.build(request.app.state.cfg, request.app.state.db, live=True)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health(request: Request):
        return {"ok": True, "counts": request.app.state.db.application_counts()}

    @app.get("/api/applications")
    def applications(request: Request, status: Status | None = None):
        return {"applications": request.app.state.db.applications(status)}

    @app.post("/api/status")
    def set_status(req: StatusRequest, request: Request):
        # A bad entry is reported, not fatal: one malformed change must not wedge
        # a browser queue that keeps retrying the whole batch.
        db = request.app.state.db
        applied, rejected = [], []
        for ch in req.items():
            try:
                row = db.set_application(ch.hash, ch.status, ch.note)
            except ValueError as e:
                rejected.append({"hash": ch.hash, "error": str(e)})
                continue
            applied.append({"hash": ch.hash, "status": row["status"], "note": row["note"],
                            "applied_at": row["applied_at"] or "",
                            "status_updated": row["updated_at"] or ""})
        request.app.state.log(f"  saved {len(applied)} status change(s)"
                              + (f", rejected {len(rejected)}" if rejected else ""))
        return {"ok": not rejected, "applied": applied, "rejected": rejected}

    return app


def serve(cfg, db, host=None, port=None, open_browser=False, log=print):
    import uvicorn

    sc = cfg.get("serve") or {}
    host = host or sc.get("host", "127.0.0.1")
    port = int(port or sc.get("port", 8765))
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"
    log(f"Dashboard live at {url}  (status changes save straight to {cfg['db_path']})")
    log("Ctrl-C to stop.")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.7, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(cfg, db, log), host=host, port=port, log_level="warning")
