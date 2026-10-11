"use strict";

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- state
const S = {
  files: [], file: null, content: "", lines: [], oldHtml: [],
  hasBackup: false, errLine: null,
  proposal: null, view: "original", applied: false, busy: false,
};

// ---------------------------------------------------------------- helpers
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3600);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

// ---------------------------------------------------------------- C++ highlighting (small, no dependencies)
const KEYWORDS = new Set(("alignas alignof and asm auto break case catch class const constexpr const_cast continue decltype default " +
  "delete do dynamic_cast else enum explicit export extern false for friend goto if inline mutable namespace new noexcept not " +
  "nullptr operator or override private protected public register reinterpret_cast return sizeof static static_assert " +
  "static_cast struct switch template this throw true try typedef typeid typename union using virtual volatile while").split(" "));
const TYPES = new Set("bool char double float int long short signed unsigned void size_t string vector map set unique_ptr shared_ptr".split(" "));

function highlight(code) {
  const re = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(^[ \t]*#[^\n]*)|(\b\d[\w.]*\b)|([A-Za-z_]\w*)(?=\s*\()|([A-Za-z_]\w*)/gm;
  let out = "", last = 0, m;
  const push = (text, cls) => {
    out += text.split("\n").map((p) => (cls && p ? `<span class="${cls}">${esc(p)}</span>` : esc(p))).join("\n");
  };
  while ((m = re.exec(code))) {
    push(code.slice(last, m.index));
    if (m[1]) push(m[0], "c");
    else if (m[2]) push(m[0], "s");
    else if (m[3]) push(m[0], "pp");
    else if (m[4]) push(m[0], "n");
    else if (m[5]) push(m[0], KEYWORDS.has(m[0]) ? "k" : "f");
    else push(m[0], KEYWORDS.has(m[0]) ? "k" : TYPES.has(m[0]) ? "ty" : "");
    last = re.lastIndex;
  }
  push(code.slice(last));
  return out.split("\n");
}

const indentOf = (line) => (line.match(/^[ \t]*/)[0].replace(/\t/g, "    ")).length;

// ---------------------------------------------------------------- listing
function row(kind, num, mark, html, rawLine) {
  const body = html === "" ? "​" : html;
  const inner = kind === "err"
    ? `<span class="txt" style="--indent:${indentOf(rawLine || "")}">${body}</span>`
    : body;
  return `<div class="ln ${kind}"><span class="num">${num}</span><span class="mk">${mark}</span><span class="t">${inner}</span></div>`;
}

function renderCode() {
  const P = S.proposal;
  let rows = [];
  if (P && S.view === "fixed") {
    rows = P.newHtml.map((h, i) => row("eq", i + 1, "", h));
  } else if (P && S.view === "redline") {
    rows = P.redline.map((r) => {
      if (r.t === "eq") return row("eq", r.o + 1, "", S.oldHtml[r.o]);
      if (r.t === "del") return row("del", r.o + 1, "−", S.oldHtml[r.o]);
      return row("add", "", "+", P.newHtml[r.n]);
    });
  } else {
    rows = S.oldHtml.map((h, i) => (i + 1 === S.errLine
      ? row("err", i + 1, "", h, S.lines[i])
      : row("eq", i + 1, "", h)));
  }
  $("code").innerHTML = rows.join("");

  // bring the interesting line into view inside the sheet, not the whole page
  const target = $("code").querySelector(".ln.err, .ln.del, .ln.add");
  const sheet = $("sheet");
  if (target) {
    sheet.scrollTo({ top: Math.max(0, target.offsetTop - sheet.clientHeight / 3), behavior: "smooth" });
  } else {
    sheet.scrollTop = 0;
  }
}

function setView(view) {
  S.view = view;
  document.querySelectorAll("#views button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.view === view)));
  renderCode();
}

function showViews(on) { $("views").hidden = !on; }

function clearProposal(keepNotes) {
  S.proposal = null;
  S.view = "original";
  showViews(false);
  if (!keepNotes) {
    $("decision").hidden = true;
    $("storyblock").hidden = true;
    $("traceblock").hidden = true;
    $("empty").hidden = false;
  }
}

function errorLineFrom(text) {
  const base = (S.file || "").split("/").pop();
  const re = /([\w./\\-]+\.(?:cpp|cc|cxx|hpp|h|c)):(\d+)/g;
  let m, first = null;
  while ((m = re.exec(text))) {
    const name = m[1].replace(/\\/g, "/").split("/").pop();
    if (name === base) return parseInt(m[2], 10);
    if (first === null) first = parseInt(m[2], 10);
  }
  return first;
}

async function loadFile(path, keepNotes) {
  try {
    const f = await api("/api/file?path=" + encodeURIComponent(path));
    S.file = f.path; S.content = f.content; S.hasBackup = f.has_backup;
    S.lines = f.content.split("\n");
    if (S.lines.length && S.lines[S.lines.length - 1] === "") S.lines.pop();
    S.oldHtml = highlight(S.lines.join("\n"));
    S.errLine = null;
    clearProposal(keepNotes);
    $("runstrip").hidden = true;
    renderCode();
    $("undo").hidden = !S.hasBackup;
  } catch (e) {
    $("code").innerHTML = "";
    toast(e.message);
  }
}

// ---------------------------------------------------------------- status and files
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const pills = [];
    if (!s.ollama) pills.push(["bad", "AI engine offline"]);
    else if (!s.model_ready) pills.push(["bad", "AI model not found"]);
    else pills.push(["ok", "AI engine online"]);
    $("status").innerHTML = pills.map(([c, t]) =>
      `<span class="pill" ${t.startsWith("Project") ? `title="${esc(s.project)}"` : ""}><span class="dot ${c}"></span>${esc(t)}</span>`).join("");
  } catch (_) {
    $("status").innerHTML = `<span class="pill"><span class="dot bad"></span>Server not reachable</span>`;
  }
}

async function loadFiles(keep) {
  const { files } = await api("/api/files");
  S.files = files;
  const sel = $("file");
  sel.innerHTML = files.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  if (!files.length) { $("code").innerHTML = ""; toast("No C++ files found in the project folder."); return; }
  const first = keep && files.includes(keep) ? keep : files[0];
  sel.value = first;
  await loadFile(first);
}

// ---------------------------------------------------------------- build and run
async function buildAndRun() {
  if (S.busy || !S.file) return;
  S.busy = true; $("run").disabled = true;
  const strip = $("runstrip");
  strip.hidden = false; strip.className = "runstrip";
  strip.innerHTML = `<p class="sub">Building ${esc(S.file)} in a sandbox copy…</p>`;
  try {
    const r = await post("/api/run", { path: S.file });
    clearProposal();
    if (r.status === "ok") {
      S.errLine = null;
      strip.innerHTML = `<p>Ran without errors.</p>` +
        (r.sanitized === false ? `<p class="sub">Sanitizers are not available with this compiler, so memory bugs may go unnoticed.</p>` : "") +
        (r.output ? `<pre>${esc(r.output)}</pre>` : "");
    } else if (r.status === "compile" || r.status === "runtime") {
      strip.className = "runstrip bad";
      strip.innerHTML = `<p>${r.status === "compile" ? "The build failed." : "The program crashed while running."} The error is in the box on the right.</p>`;
      $("error").value = r.output;
      S.errLine = errorLineFrom(r.output);
      $("fixhint").textContent = "Ctrl+Enter also works."; $("fixhint").classList.remove("warn");
    } else {
      strip.className = "runstrip bad";
      strip.innerHTML = `<p>${esc(r.output)}</p>`;
    }
    renderCode();
  } catch (e) {
    strip.className = "runstrip bad";
    strip.innerHTML = `<p>${esc(e.message)}</p>`;
  } finally {
    S.busy = false; $("run").disabled = false;
  }
}

// ---------------------------------------------------------------- trace (the live list of tool calls)
const trace = { items: [], tools: 0 };

function traceReset() {
  trace.items = []; trace.tools = 0;
  $("trace").innerHTML = "";
  $("traceblock").hidden = false;
}

function traceFinish() {
  trace.items.forEach((li) => {
    if (li.classList.contains("run")) {
      li.classList.replace("run", "done");
      const t = li.querySelector("time");
      if (t) t.textContent = ((performance.now() - li._t0) / 1000).toFixed(1) + " s";
    }
  });
}

function traceAdd({ label, sub, code, mcp, running }) {
  traceFinish();
  const li = document.createElement("li");
  li.className = running ? "run" : "done";
  li._t0 = performance.now();
  li.innerHTML = `<span class="mk"></span>
    <div><div class="row"><span class="what">${esc(label)}</span>${code ? `<code>${esc(code)}</code>` : ""}${mcp ? `<span class="via">MCP</span>` : ""}</div>
    ${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</div><time></time>`;
  $("trace").appendChild(li);
  trace.items.push(li);
  return li;
}

function onEvent(ev) {
  if (ev.stage === "model") {
    traceAdd({ label: trace.tools === 0 ? "Reading the error" : "Writing the fix", running: true });
  } else if (ev.stage === "tool") {
    trace.tools += 1;
    traceAdd({
      label: "Read your file", code: `${ev.tool}("${(ev.arguments || {}).path || ""}")`, mcp: true,
      sub: ev.fallback ? "The model did not ask for it, so the app fetched it." : "",
    });
  } else if (ev.stage === "verify") {
    traceAdd({ label: "Checking the fix in the sandbox", code: `compile_and_run("${ev.path || ""}")`, mcp: true, running: true });
  }
}

// ---------------------------------------------------------------- results
function renderStory(text) {
  const t = text.trim();
  const fixAt = t.search(/\*\*Fix:?\*\*/i);
  const hasCause = /\*\*Cause:?\*\*/i.test(t);
  const notes = [];
  if (hasCause || fixAt >= 0) {
    const cause = (fixAt < 0 ? t : t.slice(0, fixAt)).replace(/\*\*Cause:?\*\*:?/i, "").trim();
    const fix = fixAt < 0 ? "" : t.slice(fixAt).replace(/\*\*Fix:?\*\*:?/i, "").trim();
    if (cause) notes.push(`<div class="note cause"><h3>Cause</h3><p>${inline(cause)}</p></div>`);
    if (fix) notes.push(`<div class="note fix"><h3>Fix</h3><p>${inline(fix)}</p></div>`);
  } else {
    notes.push(`<div class="note"><p>${inline(t).replace(/\n+/g, "<br>")}</p></div>`);
  }
  $("story").innerHTML = notes.join("");
  $("storyblock").hidden = false;
}

function renderResult(r) {
  traceFinish();
  renderStory(r.explanation || "The model did not return an explanation.");

  const dec = $("decision");
  if (!r.code || !r.id) {
    dec.hidden = true;
    $("verdict").className = "verdict";
    toast("The model did not return a corrected file. Try again, or check the explanation.");
    return;
  }
  S.proposal = { id: r.id, target: r.target, redline: r.redline, newHtml: highlight(r.code.replace(/\n$/, "")), verified: r.verify && r.verify.status === "ok" };
  S.applied = false;

  const ok = S.proposal.verified;
  const v = r.verify || {};
  $("verdict").className = "verdict" + (ok ? "" : " bad");
  $("verdict").innerHTML = `<span class="badge"></span><div>` +
    (ok ? `<p>The fixed file builds and runs cleanly in the sandbox.</p>`
        : `<p>The fixed file failed in the sandbox${v.status ? " (" + esc(v.status) + ")" : ""}.</p>${v.output ? `<pre>${esc(v.output)}</pre>` : ""}`) +
    `</div>`;
  $("forcewrap").hidden = ok; $("force").checked = false;
  $("apply").disabled = false;
  $("apply").hidden = false;
  dec.hidden = false;

  showViews(true);
  setView("redline");
  $("storyblock").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function explainAndFix() {
  if (S.busy) return;
  const error = $("error").value.trim();
  const hint = $("fixhint");
  if (!error) {
    hint.textContent = "Paste an error first, or build the file to capture one.";
    hint.classList.add("warn");
    $("error").focus();
    return;
  }
  hint.textContent = "Ctrl+Enter also works."; hint.classList.remove("warn");
  if (S.errLine === null) { S.errLine = errorLineFrom(error); }

  S.busy = true; $("fix").disabled = true; $("run").disabled = true;
  clearProposal(); renderCode();
  $("empty").hidden = true;
  traceReset();
  try {
    const res = await fetch("/api/fix", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ error, path: S.file }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "", failed = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.stage === "done") renderResult(ev.result);
        else if (ev.stage === "error") failed = ev.message;
        else onEvent(ev);
      }
    }
    if (failed) throw new Error(failed);
  } catch (e) {
    traceFinish();
    $("storyblock").hidden = false;
    $("story").innerHTML = `<div class="note cause"><h3>It did not work</h3><p>${inline(e.message)}</p></div>`;
  } finally {
    S.busy = false; $("fix").disabled = false; $("run").disabled = false;
  }
}

// ---------------------------------------------------------------- apply and undo
async function applyFix() {
  const P = S.proposal;
  if (!P) return;
  $("apply").disabled = true;
  try {
    await post("/api/apply", { id: P.id, force: $("force").checked });
    toast(`Applied to ${P.target}. The previous version is saved as ${P.target}.bak.`);
    S.errLine = null;
    $("file").value = P.target;
    await loadFile(P.target, true);
    $("verdict").className = "verdict";
    $("verdict").innerHTML = `<span class="badge"></span><div><p>Applied to ${esc(P.target)}. Build and run to confirm.</p></div>`;
    $("forcewrap").hidden = true; $("apply").hidden = true;
  } catch (e) {
    $("apply").disabled = false;
    toast(e.message);
  }
}

async function undoFix() {
  try {
    await post("/api/undo", { path: S.file });
    toast(`Restored ${S.file} to its previous version.`);
    $("apply").hidden = false;
    await loadFile(S.file);
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- wiring
$("file").addEventListener("change", (e) => loadFile(e.target.value));
$("run").addEventListener("click", buildAndRun);
$("fix").addEventListener("click", explainAndFix);
$("apply").addEventListener("click", applyFix);
$("undo").addEventListener("click", undoFix);
$("views").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setView(b.dataset.view); });
$("error").addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") explainAndFix(); });
$("error").addEventListener("input", () => { S.errLine = errorLineFrom($("error").value); if (!S.proposal) renderCode(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadStatus(); });

loadStatus();
setInterval(loadStatus, 20000);
loadFiles().catch((e) => toast(e.message));
