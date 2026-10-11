"""MCP server for the C++ bug fixer.

Tools:
  read_file(path)                  -> source text (what the model calls)
  list_files()                     -> source files in the project
  compile_and_run(path, code, ...) -> JSON {status, output, ...}; builds a COPY of the
                                      project, so your real files are never touched.

Run by the agent over stdio. Set PROJECT_DIR to point at your C++ project.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("PROJECT_DIR") or (Path(__file__).parent / "workspace")).resolve()
SRC_EXT = {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"}
SKIP_DIRS = {".git", "build", "venv", ".venv", "node_modules", "__pycache__"}
MAX_READ = 60_000

mcp = FastMCP("cpp-bugfix-tools")


def _safe(rel: str) -> Path:
    """Resolve a project-relative path and refuse anything outside the project folder."""
    full = (ROOT / rel).resolve()
    if full != ROOT and not full.is_relative_to(ROOT):
        raise ValueError("path is outside the project folder")
    return full


def _list() -> list:
    out = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and p.suffix.lower() in SRC_EXT and not (set(rel.parts[:-1]) & SKIP_DIRS):
            out.append(rel.as_posix())
    return out


# Same trimming the model saw during training: first error only, short traces.
def clean_compile_error(s: str) -> str:
    s = re.sub(r"/tmp/cc\w+\.o", "main.o", s)
    lines = s.splitlines()
    if "undefined reference" in s:
        return "\n".join(lines[:12])
    out, n_err = [], 0
    for line in lines:
        if "error:" in line:
            n_err += 1
            if n_err == 2:
                break
        out.append(line)
        if len(out) >= 14:
            break
    return "\n".join(out)


def clean_runtime_error(s: str, workdir: str) -> str:
    s = re.sub(r"==\d+==", "==", s)
    s = s.replace(workdir + os.sep, "").replace(workdir + "/", "")
    out = []
    for line in s.splitlines():
        if not line.strip():
            continue
        out.append(line)
        if "SUMMARY:" in line or len(out) >= 14:
            break
    return "\n".join(out)


def _result(status: str, output: str, **extra) -> str:
    return json.dumps({"status": status, "output": output, **extra})


@mcp.tool()
def read_file(path: str) -> str:
    """Read a source file from the project."""
    try:
        f = _safe(path)
    except ValueError as e:
        return f"ERROR: {e}"
    if not f.is_file():
        # forgiving: accept a bare file name that matches exactly one project file
        matches = [p for p in _list() if Path(p).name == Path(path).name]
        if len(matches) == 1:
            f = _safe(matches[0])
        else:
            return f"ERROR: file not found: {path}. Files in the project: {', '.join(_list()) or '(none)'}"
    text = f.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ:
        text = text[:MAX_READ] + "\n// ... file truncated ..."
    return text


@mcp.tool()
def list_files() -> str:
    """List the C/C++ source files in the project."""
    return "\n".join(_list()) or "(no source files)"


@mcp.tool()
def compile_and_run(path: str, code: str = "", sanitize: bool = True) -> str:
    """Compile and run one source file in a temporary copy of the project.

    If `code` is given it replaces the file's content in the copy (use it to test a
    proposed fix). With sanitize=True it builds with AddressSanitizer + UBSan when the
    compiler supports them. Returns JSON: {"status": "ok"|"compile"|"runtime"|"error", "output": ...}.
    """
    cxx = next((c for c in ("g++", "clang++", "c++") if shutil.which(c)), None)
    if not cxx:
        return _result("error", "No C++ compiler found. Install g++ (or clang++) and make sure it is on PATH.")
    try:
        target = _safe(path)
    except ValueError as e:
        return _result("error", str(e))
    rel = target.relative_to(ROOT).as_posix()

    tmp = Path(tempfile.mkdtemp(prefix="cppfix_"))
    try:
        work = tmp / "proj"
        shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        src = work / rel
        if code:
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(code, encoding="utf-8")
        elif not src.is_file():
            return _result("error", f"file not found: {path}")

        exe = "prog.exe" if os.name == "nt" else "prog"
        base = [cxx, "-std=c++17", "-g", "-O0", "-I", ".", rel, "-o", exe]
        san = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]

        def build(cmd):
            return subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=120)

        sanitized = False
        built = build(base + san) if sanitize else None
        if built is not None and built.returncode == 0:
            sanitized = True
        else:
            # either sanitizers were not requested, or the sanitized build failed:
            # a plain build tells us whether it is a real code error or missing sanitizer support
            plain = build(base)
            if plain.returncode != 0:
                return _result("compile", clean_compile_error(plain.stderr))

        try:
            run = subprocess.run([str(work / exe)], cwd=work, capture_output=True, text=True,
                                 timeout=10, stdin=subprocess.DEVNULL,
                                 env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"))
        except subprocess.TimeoutExpired:
            return _result("runtime", "The program did not finish (timeout).", sanitized=sanitized)
        if run.returncode != 0:
            err = clean_runtime_error(run.stderr, str(work)) or f"The program crashed (exit code {run.returncode})."
            return _result("runtime", err, sanitized=sanitized)
        return _result("ok", run.stdout[:800], sanitized=sanitized)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    mcp.run()
