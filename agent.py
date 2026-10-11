"""Agent: connects the fine-tuned model (served by Ollama) to the MCP server.

Flow for one request:
  error text -> model -> (read_file tool call -> MCP server -> file text) -> model -> explanation + fixed file
  then the fixed file is checked with the MCP compile_and_run tool, in a temp copy of the project.
The model only gets the read_file tool, exactly as in training. Applying a fix is done by the UI, never by the model.
"""
import asyncio
import difflib
import json
import os
import re
import shutil
import sys
from pathlib import Path

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).parent
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR") or (HERE / "workspace")).resolve()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "cpp-bugfix")
MAX_STEPS = 4
SRC_EXT = {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"}

# Must stay identical to the system prompt and tool schema used in the training notebook.
SYSTEM = ("You are a C++ debugging assistant. The user pastes a compiler or runtime error. "
          "Read the source with the read_file tool, then reply with **Cause:** (the root cause in plain words), "
          "**Fix:** (one sentence), and the full corrected file in a ```cpp block.")

TOOLS = [{"type": "function", "function": {
    "name": "read_file",
    "description": "Read a source file from the project.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string", "description": "File path, e.g. main.cpp"}},
                   "required": ["path"]}}}]


# ---------------------------------------------------------------- local file helpers (used by the UI)
def _safe(rel: str) -> Path:
    full = (PROJECT_DIR / rel).resolve()
    if full != PROJECT_DIR and not full.is_relative_to(PROJECT_DIR):
        raise ValueError("path is outside the project folder")
    return full


def source_files() -> list:
    return sorted(p.relative_to(PROJECT_DIR).as_posix() for p in PROJECT_DIR.rglob("*")
                  if p.is_file() and p.suffix.lower() in SRC_EXT)


def read_local(rel: str) -> str:
    return _safe(rel).read_text(encoding="utf-8", errors="replace")


def make_diff(old: str, new: str, name: str) -> str:
    return "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                          f"a/{name}", f"b/{name}", lineterm=""))


def apply_fix(rel: str, code: str) -> str:
    """Write the fix to the real file, keeping a .bak copy so it can be undone."""
    f = _safe(rel)
    if f.exists():
        shutil.copy2(f, f.with_name(f.name + ".bak"))
    f.write_text(code, encoding="utf-8")
    return str(f)


def undo_fix(rel: str) -> bool:
    f = _safe(rel)
    bak = f.with_name(f.name + ".bak")
    if not bak.exists():
        return False
    shutil.copy2(bak, f)
    bak.unlink()
    return True


# ---------------------------------------------------------------- model
def chat(messages: list) -> dict:
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=900, json={
            "model": MODEL, "messages": messages, "tools": TOOLS, "stream": False,
            "options": {"temperature": 0, "num_ctx": 4096}})
        r.raise_for_status()
    except requests.ConnectionError:
        raise RuntimeError(f"Cannot reach Ollama at {OLLAMA_URL}. Start it (ollama serve) and check that "
                           f"`ollama list` shows the model '{MODEL}'.")
    except requests.HTTPError as e:
        raise RuntimeError(f"Ollama error: {e.response.text[:300]}")
    return r.json()["message"]


def _tool_calls(msg: dict) -> list:
    """Tool calls from Ollama's structured field, or from <tool_call> tags in the text (fallback)."""
    calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append((fn.get("name"), args))
    if not calls:
        for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", msg.get("content") or "", re.S):
            try:
                d = json.loads(m.group(1))
                calls.append((d.get("name"), d.get("arguments") or {}))
            except ValueError:
                pass
    return calls


def _extract_code(text: str):
    blocks = re.findall(r"```(?:cpp|c\+\+)\n(.*?)```", text, re.S)
    return blocks[-1] if blocks else None


def _guess_path(error_text: str, default_path: str) -> str:
    m = re.search(r"([\w./\\-]+\.(?:cpp|cc|cxx|hpp|h)):\d+", error_text)
    if m:
        p = m.group(1).replace("\\", "/")
        if (PROJECT_DIR / p).is_file():
            return p
    return default_path


# ---------------------------------------------------------------- MCP client
def _server_params() -> StdioServerParameters:
    env = dict(os.environ, PROJECT_DIR=str(PROJECT_DIR))
    return StdioServerParameters(command=sys.executable, args=[str(HERE / "mcp_server.py")], env=env)


def _text(res) -> str:
    return "\n".join(getattr(c, "text", "") for c in res.content)


async def _call_once(name: str, args: dict) -> str:
    async with stdio_client(_server_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return _text(await session.call_tool(name, args))


def run_file(path: str) -> dict:
    """Compile and run a project file in the sandbox (used by the UI's 'Run file' button)."""
    return json.loads(asyncio.run(_call_once("compile_and_run", {"path": path})))


async def _analyze(error_text: str, default_path: str, progress=None) -> dict:
    log = []
    emit = progress or (lambda event: None)
    async with stdio_client(_server_params()) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            async def tool(name, args):
                return _text(await session.call_tool(name, args))

            messages = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": f"I get this error with my C++ program:\n\n```\n{error_text.strip()}\n```"}]
            target, has_read, final = default_path, False, None

            for _ in range(MAX_STEPS):
                emit({"stage": "model"})
                msg = await asyncio.to_thread(chat, messages)
                calls = _tool_calls(msg)

                if calls:
                    if msg.get("tool_calls"):
                        messages.append({"role": "assistant", "content": msg.get("content") or "",
                                         "tool_calls": [{"function": {"name": n, "arguments": a}} for n, a in calls]})
                    else:
                        messages.append({"role": "assistant", "content": msg.get("content") or ""})
                    for name, args in calls:
                        if name != "read_file":
                            result = f"ERROR: unknown tool {name}"
                        else:
                            result = await tool("read_file", args)
                            if not result.startswith("ERROR"):
                                target, has_read = args.get("path") or target, True
                        log.append({"tool": name, "arguments": args, "result_preview": result[:200]})
                        emit({"stage": "tool", "tool": name, "arguments": args})
                        messages.append({"role": "tool", "tool_name": name, "content": result})
                    continue

                if not has_read and default_path:
                    # The model answered without reading the file. Do the read for it, the same way
                    # the training conversations look, and ask again.
                    path = _guess_path(error_text, default_path)
                    result = await tool("read_file", {"path": path})
                    log.append({"tool": "read_file", "arguments": {"path": path}, "result_preview": result[:200],
                                "note": "the model did not ask, so the app read the file for it"})
                    emit({"stage": "tool", "tool": "read_file", "arguments": {"path": path}, "fallback": True})
                    messages.append({"role": "assistant", "content": "",
                                     "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": path}}}]})
                    messages.append({"role": "tool", "tool_name": "read_file", "content": result})
                    target, has_read = path, True
                    continue

                final = msg.get("content") or ""
                break

            if final is None:
                final = "The model did not produce an answer."

            code = _extract_code(final)
            explanation = re.sub(r"```(?:cpp|c\+\+)\n.*?```", "", final, flags=re.S).strip()
            verify = None
            if code and target:
                code = code.rstrip() + "\n"
                emit({"stage": "verify", "path": target})
                try:
                    verify = json.loads(await tool("compile_and_run", {"path": target, "code": code}))
                except ValueError:
                    verify = {"status": "error", "output": "could not read the sandbox result"}
            return {"explanation": explanation, "code": code, "target": target,
                    "verify": verify, "log": log, "raw": final}


def analyze(error_text: str, default_path: str, progress=None) -> dict:
    """progress, if given, is called with small dicts ({"stage": "model" | "tool" | "verify", ...})."""
    return asyncio.run(_analyze(error_text, default_path, progress))


# ---------------------------------------------------------------- helpers for the web interface
def has_backup(rel: str) -> bool:
    f = _safe(rel)
    return f.with_name(f.name + ".bak").exists()


def redline(old: str, new: str) -> list:
    """Line-by-line markup: kept lines, deleted lines and added lines, with their line indexes."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [{"t": "eq", "o": i, "n": j} for i, j in zip(range(i1, i2), range(j1, j2))]
        else:
            out += [{"t": "del", "o": i, "n": None} for i in range(i1, i2)]
            out += [{"t": "add", "o": None, "n": j} for j in range(j1, j2)]
    return out
