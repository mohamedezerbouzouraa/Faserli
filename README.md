# C++ Bug Fixer: QLoRA model + MCP tools + web interface

```
Browser page (web/)  ──►  server.py  ──►  agent.py  ──►  Ollama (your tuned model)
                                              │
                                              └──►  mcp_server.py   read_file · list_files · compile_and_run
                                                          │
                                                          └── your C++ files (workspace/)
```

The model can only call `read_file`. The sandbox check and the Apply button belong to the app,
so the model can never change your files by itself.

## Run it

1. Python 3.10+, then in this folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Ollama must be installed and your model registered (`ollama list` shows `cpp-bugfix`).
3. A C++ compiler on PATH (`g++ --version` works). On Windows, MSYS2/MinGW builds and runs code fine
   but has no sanitizers, so memory bugs like buffer overflows may not be detected. WSL gives you both.
4. Check everything: `python selftest.py` (should end with ALL CHECKS PASSED)
5. Start the interface: `python server.py`. It opens http://127.0.0.1:8000 in your browser.

## Use it

1. Pick a file and click **Build and run**. If it fails, the error appears in the box and the faulty line is underlined.
   You can also paste an error from your own build.
2. Click **Explain and fix**. You see each step the assistant takes, including the MCP tool calls.
3. Read the cause and the fix. The code view shows the change as a redline: struck-through lines are removed, green lines are added.
   Switch between Original, Redline and Fixed.
4. **Apply fix** writes the file (the old version is kept as `file.bak`). **Undo last apply** restores it.

To use your own project: `set PROJECT_DIR=C:\path\to\project` (Windows) or
`export PROJECT_DIR=/path/to/project`, then run `python server.py`.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `PROJECT_DIR` | `./workspace` | Folder with the C++ files |
| `OLLAMA_MODEL` | `cpp-bugfix` | Model name in Ollama |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama address |
| `PORT` | `8000` | Preferred port for the page |

## Files

- `server.py` and `web/`: the interface (HTML, CSS, JavaScript) and the small local server behind it. It only answers on your own computer.
- `agent.py`: talks to Ollama and to the MCP server.
- `mcp_server.py`: the MCP tools.
- `app.py`: an older, plain Gradio interface (needs `pip install gradio`). Optional.

