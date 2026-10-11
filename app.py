"""Gradio interface for the C++ bug fixer.  Run:  python app.py"""
import json

import gradio as gr

import agent

STATUS_ICON = {"ok": "✅", "compile": "❌", "runtime": "❌", "error": "⚠️"}


def _files():
    return agent.source_files()


def show_file(path):
    try:
        return agent.read_local(path) if path else ""
    except Exception as e:  # noqa: BLE001
        return f"// could not read {path}: {e}"


def refresh_files():
    files = _files()
    first = files[0] if files else None
    return gr.update(choices=files, value=first), show_file(first)


def run_file(path):
    """Build and run the selected file in the sandbox and paste any error into the error box."""
    if not path:
        return "", "Pick a file first."
    try:
        res = agent.run_file(path)
    except Exception as e:  # noqa: BLE001
        return "", f"⚠️ {e}"
    if res["status"] in ("compile", "runtime"):
        return res["output"], f"❌ {res['status']} error captured below. Click **Explain & fix**."
    if res["status"] == "ok":
        note = "" if res.get("sanitized") else " (no sanitizers on this compiler)"
        return "", f"✅ Ran without errors{note}.\n\n```\n{res['output']}\n```"
    return "", f"⚠️ {res['output']}"


def explain_and_fix(error, path):
    empty = ("", "", "", "", None, gr.update(interactive=False))
    if not error or not error.strip():
        return ("Paste an error first (or use **Run file & capture error**).",) + empty[1:]
    if not path:
        return ("Pick a file first.",) + empty[1:]
    try:
        res = agent.analyze(error, path)
    except Exception as e:  # noqa: BLE001
        return (f"⚠️ {e}",) + empty[1:]

    explanation = res["explanation"] or res["raw"]
    diff_md, verify_md, state, can_apply = "", "", None, False

    if res["code"]:
        try:
            old = agent.read_local(res["target"])
        except Exception:  # noqa: BLE001
            old = ""
        diff = agent.make_diff(old, res["code"], res["target"])
        diff_md = f"```diff\n{diff or '(no changes)'}\n```"
        v = res["verify"] or {}
        icon = STATUS_ICON.get(v.get("status"), "⚠️")
        if v.get("status") == "ok":
            verify_md = f"{icon} **The fixed file builds and runs cleanly in the sandbox.**"
        else:
            verify_md = (f"{icon} **The proposed fix did not pass the sandbox** ({v.get('status')}):\n\n"
                         f"```\n{v.get('output', '')}\n```")
        state = {"target": res["target"], "code": res["code"], "verified": v.get("status") == "ok"}
        can_apply = True
    else:
        verify_md = "⚠️ The model did not return a corrected file."

    log_md = "\n".join(
        f"- `{c['tool']}({json.dumps(c['arguments'])})`" + (f" — {c['note']}" if c.get("note") else "")
        for c in res["log"]) or "_no tool calls_"
    return explanation, diff_md, verify_md, log_md, state, gr.update(interactive=can_apply)


def apply(state, force, path):
    if not state:
        return "Nothing to apply yet.", show_file(path)
    if not state["verified"] and not force:
        return ("🛑 This fix did not pass the sandbox. Tick **Apply even if the sandbox check failed** "
                "if you still want it."), show_file(path)
    try:
        agent.apply_fix(state["target"], state["code"])
    except Exception as e:  # noqa: BLE001
        return f"⚠️ {e}", show_file(path)
    shown = state["target"] if state["target"] == path else path
    return f"✅ Applied to `{state['target']}`. The old version is saved as `{state['target']}.bak`.", show_file(shown)


def undo(state, path):
    target = (state or {}).get("target") or path
    if target and agent.undo_fix(target):
        return f"↩️ Restored `{target}` from its .bak copy.", show_file(path)
    return "No backup to restore.", show_file(path)


with gr.Blocks(title="C++ Bug Fixer") as demo:
    gr.Markdown("# C++ Bug Fixer\nQLoRA-tuned model + MCP tools. "
                f"Project folder: `{agent.PROJECT_DIR}` · model: `{agent.MODEL}`")
    state = gr.State(None)

    with gr.Row():
        with gr.Column(scale=1):
            files = _files()
            file_dd = gr.Dropdown(choices=files, value=files[0] if files else None, label="File")
            refresh_btn = gr.Button("Refresh file list", size="sm")
            code_view = gr.Code(value=show_file(files[0] if files else None), language="cpp",
                                label="Current file", interactive=False, lines=18)
            run_btn = gr.Button("Run file & capture error")
            run_status = gr.Markdown()

        with gr.Column(scale=1):
            error_box = gr.Textbox(label="Error (paste it, or capture it with the button)", lines=8)
            fix_btn = gr.Button("Explain & fix", variant="primary")
            explanation = gr.Markdown()
            diff_md = gr.Markdown()
            verify_md = gr.Markdown()
            with gr.Row():
                apply_btn = gr.Button("Apply fix", variant="primary", interactive=False)
                undo_btn = gr.Button("Undo last apply")
            force = gr.Checkbox(label="Apply even if the sandbox check failed", value=False)
            apply_status = gr.Markdown()
            with gr.Accordion("Tool calls the model made", open=False):
                log_md = gr.Markdown()

    file_dd.change(show_file, file_dd, code_view)
    refresh_btn.click(refresh_files, None, [file_dd, code_view])
    run_btn.click(run_file, file_dd, [error_box, run_status])
    fix_btn.click(explain_and_fix, [error_box, file_dd],
                  [explanation, diff_md, verify_md, log_md, state, apply_btn])
    apply_btn.click(apply, [state, force, file_dd], [apply_status, code_view])
    undo_btn.click(undo, [state, file_dd], [apply_status, code_view])

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", inbrowser=True)
