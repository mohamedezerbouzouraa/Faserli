"""Checks the MCP server, the agent loop (with a stand-in model) and, if running, Ollama.
Run:  python selftest.py      (no GPU or model needed for the first two parts)
"""
import asyncio
import json

import requests

import agent

ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


call = lambda name, args: asyncio.run(agent._call_once(name, args))  # noqa: E731

files = call("list_files", {}).splitlines()
check("list_files finds the samples", "sample_compile_error.cpp" in files, files)

src = call("read_file", {"path": "sample_compile_error.cpp"})
check("read_file returns the source", "totl += n" in src)
check("read_file blocks paths outside the project",
      call("read_file", {"path": "../../etc/passwd"}).startswith("ERROR"))

res = json.loads(call("compile_and_run", {"path": "sample_compile_error.cpp"}))
check("compile_and_run reports the compile error", res["status"] == "compile" and "totl" in res["output"], res)

fixed = src.replace("totl", "total")
res = json.loads(call("compile_and_run", {"path": "sample_compile_error.cpp", "code": fixed}))
check("compile_and_run accepts a fixed file", res["status"] == "ok", res)
check("the real file was not modified", "totl += n" in agent.read_local("sample_compile_error.cpp"))

# ---- agent loop with a stand-in model that behaves like the trained one
script = [
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "read_file", "arguments": {"path": "sample_compile_error.cpp"}}}]},
    {"role": "assistant", "content": "**Cause:** `totl` is a typo of `total`.\n\n**Fix:** Rename it.\n\n```cpp\n" + fixed + "```"},
]
real_chat = agent.chat
agent.chat = lambda messages, _it=iter(script): next(_it)
out = agent.analyze(res_err := json.loads(call("compile_and_run", {"path": "sample_compile_error.cpp"}))["output"],
                    "sample_compile_error.cpp")
agent.chat = real_chat
check("agent loop: model called read_file", [c["tool"] for c in out["log"]] == ["read_file"], out["log"])
check("agent loop: fix verified in sandbox", out["verify"] and out["verify"]["status"] == "ok", out["verify"])
check("agent loop: explanation extracted", "typo" in out["explanation"])

# ---- fallback when the model never asks for the file
script2 = [{"role": "assistant", "content": "Let me guess without the file."}, script[1]]
agent.chat = lambda messages, _it=iter(script2): next(_it)
out2 = agent.analyze(res_err, "sample_compile_error.cpp")
agent.chat = real_chat
check("fallback: app reads the file when the model does not",
      out2["log"] and "note" in out2["log"][0] and out2["verify"]["status"] == "ok", out2["log"])

# ---- Ollama (optional)
try:
    tags = requests.get(f"{agent.OLLAMA_URL}/api/tags", timeout=3).json()
    names = [m["name"] for m in tags.get("models", [])]
    have = any(n.split(":")[0] == agent.MODEL for n in names)
    print(("PASS " if have else "WARN ") + f"Ollama is running; model '{agent.MODEL}' "
          + ("found" if have else f"not found (have: {names}). Run: ollama create {agent.MODEL} -f Modelfile"))
except requests.RequestException:
    print(f"WARN Ollama not reachable at {agent.OLLAMA_URL} (start it with: ollama serve)")

print("\nALL CHECKS PASSED" if ok_all else "\nSOME CHECKS FAILED")
