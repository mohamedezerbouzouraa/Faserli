"""Web interface server.  Run:  python server.py   (opens http://127.0.0.1:8000)

A small local web server: it serves the page in ./web and exposes the agent
(model + MCP tools) to it. It only listens on your own computer.
"""
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import uuid
import webbrowser
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent

WEB = Path(__file__).parent / "web"
app = FastAPI(title="C++ Bug Fixer")

# Proposals the model made: only these can be applied, and only through their id.
PROPOSALS = {}


@app.middleware("http")
async def local_only(request, call_next):
    """Refuse requests that did not come from a page served by this server (e.g. another website)."""
    host = request.headers.get("host", "")
    if host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
        return JSONResponse({"detail": "this server only answers on localhost"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != host:
        return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
    return await call_next(request)


def _compiler():
    return next((c for c in ("g++", "clang++", "c++") if shutil.which(c)), None)


def _clean(exc: Exception) -> str:
    while hasattr(exc, "exceptions") and exc.exceptions:      # unwrap ExceptionGroup
        exc = exc.exceptions[0]
    return str(exc) or exc.__class__.__name__


@app.get("/api/status")
def status():
    ollama, model_ready = False, False
    try:
        r = requests.get(f"{agent.OLLAMA_URL}/api/tags", timeout=2)
        ollama = r.ok
        names = [m["name"] for m in r.json().get("models", [])]
        model_ready = any(n == agent.MODEL or n.split(":")[0] == agent.MODEL for n in names)
    except Exception:  # noqa: BLE001
        pass
    cxx, version = _compiler(), None
    if cxx:
        try:
            out = subprocess.run([cxx, "--version"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"(\d+(?:\.\d+)+)", out.splitlines()[0] if out else "")
            version = m.group(1) if m else None
        except Exception:  # noqa: BLE001
            pass
    return {"ollama": ollama, "model": agent.MODEL, "model_ready": model_ready,
            "compiler": cxx, "compiler_version": version,
            "project": str(agent.PROJECT_DIR), "project_name": agent.PROJECT_DIR.name}


@app.get("/api/files")
def files():
    return {"files": agent.source_files()}


@app.get("/api/file")
def file(path: str):
    try:
        return {"path": path, "content": agent.read_local(path), "has_backup": agent.has_backup(path)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, f"{path} was not found in the project folder")


class RunReq(BaseModel):
    path: str


@app.post("/api/run")
def run(req: RunReq):
    try:
        return agent.run_file(req.path)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "output": _clean(e)}


class FixReq(BaseModel):
    error: str
    path: str


@app.post("/api/fix")
def fix(req: FixReq):
    events = queue.Queue()

    def work():
        try:
            res = agent.analyze(req.error, req.path, progress=events.put)
            code, target = res["code"], res["target"]
            result = {"explanation": res["explanation"] or res["raw"], "code": code, "target": target,
                      "verify": res["verify"], "log": res["log"], "id": None, "redline": [], "old": ""}
            if code and target:
                try:
                    old = agent.read_local(target)
                except Exception:  # noqa: BLE001
                    old = ""
                pid = uuid.uuid4().hex
                verified = bool(res["verify"]) and res["verify"].get("status") == "ok"
                PROPOSALS[pid] = {"target": target, "code": code, "verified": verified}
                result.update(id=pid, old=old, redline=agent.redline(old, code))
            events.put({"stage": "done", "result": result})
        except Exception as e:  # noqa: BLE001
            events.put({"stage": "error", "message": _clean(e)})
        events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ApplyReq(BaseModel):
    id: str
    force: bool = False


@app.post("/api/apply")
def apply(req: ApplyReq):
    p = PROPOSALS.get(req.id)
    if not p:
        raise HTTPException(404, "That proposal is no longer available. Run Explain and fix again.")
    if not p["verified"] and not req.force:
        raise HTTPException(409, "The fix did not pass the sandbox check.")
    try:
        agent.apply_fix(p["target"], p["code"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, _clean(e))
    return {"target": p["target"]}


class UndoReq(BaseModel):
    path: str


@app.post("/api/undo")
def undo(req: UndoReq):
    try:
        if not agent.undo_fix(req.path):
            raise HTTPException(404, "There is no backup to restore.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": req.path}


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


def _free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found near %d" % start)


if __name__ == "__main__":
    port = _free_port(int(os.environ.get("PORT", "8000")))
    url = f"http://127.0.0.1:{port}"
    print(f"C++ Bug Fixer is running at {url}   (press Ctrl+C to stop)")
    print(f"Project folder: {agent.PROJECT_DIR}   Model: {agent.MODEL}")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
