"""FastAPI app: upload exports, review suggestions, download the review sheet."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .asa_export import AsaExportError, apply_asa_export, format_asa_export, parse_asa_export
from .asa_template import TemplateError, check_template, fill_template, final_asas
from .asa_library import XLSX_MIME, LibraryError, fetch_google_sheet_xlsx, load_library_xlsx
from .auth import current_user, install_auth
from .evaluate import evaluate
from .export_parser import ExportError, form_label, parse_export
from .exporter import review_sheet
from .matcher import Matcher
from .models import FormField
from .past_mappings import decisions_to_mappings
from .service import build_review, option_suggestions
from .settings import Settings
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"
SEARCH_LIMIT = 30


class OptionRequest(BaseModel):
    field_key: str
    parent_asa: str


class AppState:
    """Current library and matcher; rebuilt when the library or past mappings change."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir / "asa_mapper.sqlite")
        self.library = None
        self.matcher = None
        self.meta = {"source": None, "synced_at": None, "last_error": None}
        self.lock = threading.Lock()
        saved = self.store.load_library()
        if saved:
            data, source, synced_at = saved
            try:
                self.set_library(load_library_xlsx(data, settings.sheet_tab), source, synced_at)
            except LibraryError as exc:
                self.meta["last_error"] = f"Saved library unreadable: {exc}"
        elif settings.service_account:
            try:
                self.refresh_from_google()
            except Exception as exc:  # network/auth errors must not stop the app from starting
                self.meta["last_error"] = f"Google sync failed: {exc}"

    def set_library(self, library, source: str, synced_at: str) -> None:
        with self.lock:
            self.library = library
            self.meta.update(source=source, synced_at=synced_at)
            self.matcher = Matcher(library, self.store.list_past())

    def rebuild_matcher(self) -> None:
        with self.lock:
            if self.library:
                self.matcher = Matcher(self.library, self.store.list_past())

    def load_xlsx(self, data: bytes, source: str) -> None:
        library = load_library_xlsx(data, self.settings.sheet_tab)  # validate before saving anything
        synced_at = self.store.save_library(data, source)
        self.set_library(library, source, synced_at)
        self.meta["last_error"] = None

    def refresh_from_google(self) -> None:
        self.load_xlsx(fetch_google_sheet_xlsx(self.settings.sheet_id, self.settings.service_account), "Google Sheet")


def _used_asas(decisions: dict):
    for d in decisions.values():
        if isinstance(d, dict):
            yield d.get("asa")
            for od in (d.get("options") or {}).values():
                yield od.get("asa")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = AppState(settings)
    app = FastAPI(title="ASA Mapper")
    install_auth(app, settings)

    def require_library() -> None:
        if state.library is None:
            raise HTTPException(409, "Load the ASA Library first (upload the .xlsx or refresh from Google).")

    def require_session(sid: str) -> dict:
        session = state.store.get_session(sid)
        if session is None:
            raise HTTPException(404, "Review not found.")
        return session

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/me")
    def me(request: Request):
        return {"email": current_user(request)}

    @app.get("/api/library")
    def library_status():
        lib = state.library
        names = len(lib.names()) if lib else 0
        return {"loaded": lib is not None, **state.meta, "google_configured": bool(settings.service_account),
                "names": names, "options": len(lib.entries) - names if lib else 0,
                "issue_count": len(lib.issues) if lib else 0, "issues": lib.issues[:50] if lib else []}

    @app.post("/api/library/upload")
    async def upload_library(file: UploadFile = File(...)):
        try:
            state.load_xlsx(await file.read(), f"Uploaded {file.filename or 'file'}")
        except LibraryError as exc:
            raise HTTPException(400, str(exc))
        return library_status()

    @app.post("/api/library/refresh")
    def refresh_library():
        if not settings.service_account:
            raise HTTPException(503, "Google Sheet sync is not configured (set ASA_GOOGLE_SERVICE_ACCOUNT).")
        try:
            state.refresh_from_google()
        except Exception as exc:
            state.meta["last_error"] = f"Google sync failed: {exc}"
            raise HTTPException(502, f"{state.meta['last_error']}. Still using the last saved copy.")
        return library_status()

    @app.get("/api/search")
    def search(q: str = "", kind: str = "any", parent: str = ""):
        """Entries containing every word of q; options under `parent` are listed first, never exclusively."""
        require_library()
        words = q.lower().split()
        hits = [e for e in state.library.entries
                if (kind == "any" or e.kind == kind)
                and all(w in f"{e.asa} {e.summary} {e.section} {e.group}".lower() for w in words)]
        hits.sort(key=lambda e: not (parent and e.parent == parent))  # stable: keeps library order otherwise
        return [{"asa": e.asa, "kind": e.kind, "summary": e.summary, "section": e.section,
                 "group": e.group, "parent": e.parent} for e in hits[:SEARCH_LIMIT]]

    @app.get("/api/sessions")
    def list_sessions():
        return state.store.list_sessions()

    @app.post("/api/sessions")
    async def create_session(file: UploadFile = File(...), template: UploadFile = File(...),
                             asa_file: Optional[UploadFile] = File(None)):
        require_library()
        filename = file.filename or "form.zip"
        try:
            form = parse_export(await file.read(), fallback_name=Path(filename).stem)
        except ExportError as exc:
            raise HTTPException(400, str(exc))
        form.name = form_label(form.name, filename)
        unmatched = []
        if asa_file is not None:
            try:
                mapping = parse_asa_export((await asa_file.read()).decode("utf-8-sig"))
            except (AsaExportError, UnicodeDecodeError) as exc:
                raise HTTPException(400, f"ASA export: {exc}")
            unmatched = apply_asa_export(form, mapping)
        template_data = await template.read()
        try:
            template_check = check_template(template_data, form)
        except TemplateError as exc:
            raise HTTPException(400, f"ASA template: {exc}")
        review = build_review(form, state.matcher, state.library)
        review["existing_source"] = "asa_export" if asa_file is not None else "pdf"
        sid = state.store.create_session(form.name, review, template=template_data)
        fields = review["fields"]
        return {"id": sid, "form_name": form.name, "fields": len(fields),
                "skipped": sum(1 for f in fields if f["skip_reason"]),
                # counted like the form builder's ASA column: fields plus options
                "existing_asa": sum(bool(f["existing_asa"]) + sum(bool(o["existing_asa"]) for o in f["options"])
                                    for f in fields),
                "asa_unmatched": unmatched, "template": template_check}

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str):
        session = require_session(sid)
        if state.library:
            session["missing_asas"] = sorted({a for a in _used_asas(session["decisions"])
                                              if a and a not in state.library})
        return session

    @app.put("/api/sessions/{sid}/decisions")
    def save_decisions(sid: str, request: Request, decisions: dict = Body(...)):
        previous = require_session(sid)["decisions"]
        user = current_user(request)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for key, d in decisions.items():
            if not isinstance(d, dict) or d.get("status", "pending") == "pending":
                continue
            p = previous.get(key) or {}
            unchanged = (p.get("status"), p.get("asa"), p.get("options")) == (d.get("status"), d.get("asa"), d.get("options"))
            d["reviewer"] = p.get("reviewer", user) if unchanged else user
            d["at"] = p.get("at", stamp) if unchanged else stamp
        state.store.save_decisions(sid, decisions)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/options")
    def suggest_options(sid: str, req: OptionRequest):
        require_library()
        session = require_session(sid)
        match = next((f for f in session["review"]["fields"] if f["key"] == req.field_key), None)
        if match is None:
            raise HTTPException(404, "Field not found.")
        return option_suggestions(FormField.from_dict(match), req.parent_asa, state.matcher)

    @app.get("/api/sessions/{sid}/sheet.xlsx")
    def download_sheet(sid: str):
        session = require_session(sid)
        data = review_sheet(session["form_name"], session["review"], session["decisions"])
        filename = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", session["form_name"]) + " - ASA review.xlsx"
        return Response(data, media_type=XLSX_MIME,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/sessions/{sid}/template.xlsx")
    def download_template(sid: str):
        session = require_session(sid)
        template_data = state.store.get_template(sid)
        if template_data is None:
            raise HTTPException(404, "This review was started without an ASA template.")
        assignments = final_asas(session["review"], session["decisions"])
        filled, no_row = fill_template(template_data, assignments)
        filename = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", session["form_name"]) + "_asa_mapping_template.xlsx"
        return Response(filled, media_type=XLSX_MIME, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-ASA-Rows-Filled": str(len(assignments) - len(no_row))})

    @app.get("/api/sessions/{sid}/asa.txt")
    def download_asa_txt(sid: str):
        session = require_session(sid)
        filename = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", session["form_name"]) + " - ASA.txt"
        return Response(format_asa_export(session["decisions"]), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/sessions/{sid}/finish")
    def finish(sid: str):
        require_library()
        session = require_session(sid)
        mappings = decisions_to_mappings(session["form_name"], session["review"], session["decisions"], state.library)
        state.store.replace_past(session["form_name"], mappings)
        state.store.finish_session(sid)
        state.rebuild_matcher()
        return {"saved": len(mappings)}

    @app.get("/api/accuracy")
    def accuracy():
        require_library()
        return evaluate(state.library, state.store.list_past())

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
