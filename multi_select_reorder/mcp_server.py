from typing import Any
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP

from multi_select_reorder.selector import OptionGroup, normalize_groups, normalize_options, run_selector

_SESSION_TOKEN_FIELD = "__session_token"

mcp = FastMCP(
    "multi-select-reorder",
    instructions=(
        "Multi-select reorder tool. This opens a short-lived local web page "
        "for checkbox selection and drag-and-drop reordering."
    ),
)


def _select(
    title: str,
    options: list[Any] | None,
    initial_selected: list[Any] | None = None,
    edit_descriptions: bool = False,
    groups: list[Any] | None = None,
) -> dict[str, Any]:
    if groups is not None:
        normalized_groups = normalize_groups(groups, initial_selected=initial_selected)
        return _select_groups_in_browser(title, normalized_groups, edit_descriptions=edit_descriptions)

    if options is None:
        return _error_result("options are required when groups are not provided")

    normalized = normalize_options(options, initial_selected=initial_selected)
    if os.environ.get("MULTI_SELECT_REORDER_DIRECT_TTY") != "1":
        return _select_in_browser(title, normalized, edit_descriptions=edit_descriptions)

    try:
        result = run_selector(normalized, mode="multi", title=title, tty_path="/dev/tty")
        result["mode"] = "multi_select_reorder"
        result["ordered"] = result.get("selected", [])
        return result
    except OSError as exc:
        return {
            "mode": "multi_select_reorder",
            "selected": [],
            "ordered": [],
            "cancelled": True,
            "error": (
                f"Could not open /dev/tty for terminal selection: {exc}. "
                "Run bin/multi-select-reorder directly from an interactive shell, "
                "or configure the MCP host to launch this server with a controlling terminal."
            ),
        }


def _select_in_browser(title: str, options: list[Any], *, edit_descriptions: bool = False) -> dict[str, Any]:
    state: dict[str, Any] = {"result": None}
    done = threading.Event()
    session_token = _new_session_token()
    page = _selector_page(
        title=title,
        edit_descriptions=edit_descriptions,
        session_token=session_token,
        options=[
            {
                "id": option.id,
                "label": option.label,
                "description": option.description,
                "selected": option.selected,
            }
            for option in options
        ],
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/submit":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if not _is_valid_session_payload(payload, session_token):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            state["result"] = _coerce_browser_result(options, payload)
            done.set()
            body = b'{"ok":true}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        _open_browser(url)
        if not done.wait(3600):
            return _error_result("Web selector timed out after 1 hour")
        result = state.get("result")
        if isinstance(result, dict):
            return result
        return _error_result("Web selector returned no result")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _select_groups_in_browser(
    title: str,
    groups: list[OptionGroup],
    *,
    edit_descriptions: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {"result": None}
    done = threading.Event()
    session_token = _new_session_token()
    page = _group_selector_page(
        title=title,
        edit_descriptions=edit_descriptions,
        session_token=session_token,
        groups=[
            {
                "id": group.id,
                "label": group.label,
                "options": [
                    {
                        "id": option.id,
                        "label": option.label,
                        "description": option.description,
                        "selected": option.selected,
                    }
                    for option in group.options
                ],
            }
            for group in groups
        ],
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/submit":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if not _is_valid_session_payload(payload, session_token):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            state["result"] = _coerce_browser_group_result(groups, payload)
            done.set()
            body = b'{"ok":true}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        _open_browser(url)
        if not done.wait(3600):
            return _error_result("Web selector timed out after 1 hour")
        result = state.get("result")
        if isinstance(result, dict):
            return result
        return _error_result("Web selector returned no result")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _rate_in_browser(
    title: str,
    options: list[Any],
    *,
    mode: str = "rank",
    initial_selected: list[Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_options(options, initial_selected=initial_selected)
    if not normalized:
        return _error_result("options are required")
    if mode not in {"rank", "tinder", "facemash", "pair"}:
        return _error_result("mode must be one of: rank, tinder, facemash, pair")

    state: dict[str, Any] = {"result": None}
    done = threading.Event()
    session_token = _new_session_token()
    page = _rating_page(
        title=title,
        mode=mode,
        session_token=session_token,
        options=[
            {
                "id": option.id,
                "label": option.label,
                "description": option.description,
                "selected": option.selected,
            }
            for option in normalized
        ],
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/submit":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if not _is_valid_session_payload(payload, session_token):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            state["result"] = _coerce_rating_result(normalized, payload, mode=mode)
            done.set()
            body = b'{"ok":true}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        _open_browser(url)
        if not done.wait(3600):
            return _error_result("Rating UI timed out after 1 hour")
        result = state.get("result")
        if isinstance(result, dict):
            return result
        return _error_result("Rating UI returned no result")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _choice_in_browser(title: str, questions: list[OptionGroup]) -> dict[str, Any]:
    """Survey-style pick-one-per-question UI.

    Each question is an OptionGroup: the group label is the prompt, its options
    are the candidate answers. Keyboard: left/right pick a candidate within the
    focused question, up/down move between questions, Enter submits, Esc cancels.
    """
    if not questions:
        return _error_result("questions are required")
    state: dict[str, Any] = {"result": None}
    done = threading.Event()
    session_token = _new_session_token()
    page = _choice_page(
        title=title,
        session_token=session_token,
        questions=[
            {
                "id": q.id,
                "label": q.label,
                "options": [
                    {"id": o.id, "label": o.label, "description": o.description, "selected": o.selected}
                    for o in q.options
                ],
            }
            for q in questions
        ],
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/submit":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if not _is_valid_session_payload(payload, session_token):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            state["result"] = _coerce_choice_result(questions, payload)
            done.set()
            body = b'{"ok":true}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        _open_browser(url)
        if not done.wait(3600):
            return _error_result("Choice UI timed out after 1 hour")
        result = state.get("result")
        if isinstance(result, dict):
            return result
        return _error_result("Choice UI returned no result")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _coerce_choice_result(questions: list[OptionGroup], payload: dict[str, Any]) -> dict[str, Any]:
    valid = {q.id: {o.id for o in q.options} for q in questions}
    raw = payload.get("answers", {})
    answers: dict[str, str] = {}
    if isinstance(raw, dict):
        for qid, oid in raw.items():
            qid, oid = str(qid), str(oid)
            if qid in valid and oid in valid[qid]:
                answers[qid] = oid
    return {
        "mode": "rating_tool",
        "rating_mode": "choice",
        "answers": answers,
        "question_labels": {q.id: q.label for q in questions},
        "option_labels": {o.id: o.label for q in questions for o in q.options},
        "unanswered": [q.id for q in questions if q.id not in answers],
        "cancelled": bool(payload.get("cancelled", False)),
    }


def _new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _is_valid_session_payload(payload: Any, session_token: str) -> bool:
    return isinstance(payload, dict) and payload.get(_SESSION_TOKEN_FIELD) == session_token


def _choice_page(title: str, questions: list[dict[str, Any]], *, session_token: str) -> str:
    data = json.dumps(
        {"title": title, "questions": questions, "sessionToken": session_token},
        ensure_ascii=False,
    )
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: Canvas; color: CanvasText; }
main { max-width: 920px; margin: 28px auto; padding: 0 20px 28px; }
h1 { font-size: 24px; margin: 0 0 8px; }
.hint { color: color-mix(in srgb, CanvasText 68%, Canvas); margin: 0 0 18px; }
.qlist { display: grid; gap: 10px; }
.q { padding: 12px 14px; border: 1px solid color-mix(in srgb, CanvasText 16%, Canvas); border-radius: 9px; background: Canvas; }
.q.focused { outline: 2px solid Highlight; outline-offset: 1px; }
.qlabel { font-weight: 650; font-size: 17px; margin-bottom: 9px; }
.choices { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { padding: 8px 12px; border: 1px solid color-mix(in srgb, CanvasText 22%, Canvas); border-radius: 999px; cursor: pointer; font: inherit; background: Canvas; color: CanvasText; display: grid; }
.chip .desc { font-size: 11px; color: color-mix(in srgb, CanvasText 55%, Canvas); }
.chip.chosen { background: Highlight; border-color: Highlight; color: HighlightText; }
.chip.chosen .desc { color: color-mix(in srgb, HighlightText 80%, Highlight); }
.chip.cursor { outline: 2px dashed color-mix(in srgb, CanvasText 50%, Canvas); outline-offset: 1px; }
footer { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 18px; }
.progress { color: color-mix(in srgb, CanvasText 60%, Canvas); }
button { appearance: none; border: 1px solid color-mix(in srgb, CanvasText 20%, Canvas); border-radius: 7px; background: Canvas; color: CanvasText; padding: 9px 14px; font: inherit; cursor: pointer; }
button.primary { background: Highlight; border-color: Highlight; color: HighlightText; }
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<p id="hint" class="hint"></p>
<section id="qlist" class="qlist"></section>
<footer>
<span id="progress" class="progress"></span>
<span><button id="cancel" type="button">Cancel</button>
<button id="submit" class="primary" type="button">Submit</button></span>
</footer>
</main>
<script>
const DATA = __DATA__;
const answers = {};       // questionId -> optionId
const cursorOpt = {};     // questionId -> index of the highlighted candidate
DATA.questions.forEach(q => {
  const pre = q.options.find(o => o.selected === true);
  if (pre) answers[q.id] = pre.id;
  cursorOpt[q.id] = Math.max(0, q.options.findIndex(o => o.id === (answers[q.id] ?? null)));
});
let qFocus = 0;
const listEl = document.getElementById("qlist");
document.getElementById("title").textContent = DATA.title;
document.getElementById("hint").textContent = "←/→ choose an answer, ↑/↓ move between questions, Enter submit, Esc cancel. Click works too.";

function render() {
  listEl.innerHTML = "";
  DATA.questions.forEach((q, qi) => {
    const card = document.createElement("div");
    card.className = "q" + (qi === qFocus ? " focused" : "");
    card.dataset.qid = q.id;
    const label = document.createElement("div");
    label.className = "qlabel";
    label.textContent = q.label;
    const choices = document.createElement("div");
    choices.className = "choices";
    q.options.forEach((o, oi) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip"
        + (answers[q.id] === o.id ? " chosen" : "")
        + (qi === qFocus && oi === cursorOpt[q.id] ? " cursor" : "");
      const lab = document.createElement("span");
      lab.textContent = o.label;
      chip.append(lab);
      if (o.description) { const d = document.createElement("span"); d.className = "desc"; d.textContent = o.description; chip.append(d); }
      chip.onclick = () => { answers[q.id] = o.id; cursorOpt[q.id] = oi; qFocus = qi; render(); };
      choices.append(chip);
    });
    card.append(label, choices);
    listEl.append(card);
  });
  const answered = Object.keys(answers).length;
  document.getElementById("progress").textContent = answered + " / " + DATA.questions.length + " answered";
  listEl.children[qFocus]?.scrollIntoView({ block: "nearest" });
}

document.addEventListener("keydown", event => {
  const k = event.key;
  if (k === "Escape") { event.preventDefault(); finish(true); return; }
  if (k === "Enter") { event.preventDefault(); finish(false); return; }
  const q = DATA.questions[qFocus];
  if (!q) return;
  if (k === "ArrowRight" || k === "ArrowLeft") {
    event.preventDefault();
    const n = q.options.length;
    let i = cursorOpt[q.id] ?? 0;
    i = (k === "ArrowRight") ? (i + 1) % n : (i - 1 + n) % n;
    cursorOpt[q.id] = i;
    answers[q.id] = q.options[i].id;
    render();
  } else if (k === "ArrowDown") {
    event.preventDefault();
    qFocus = Math.min(DATA.questions.length - 1, qFocus + 1);
    render();
  } else if (k === "ArrowUp") {
    event.preventDefault();
    qFocus = Math.max(0, qFocus - 1);
    render();
  }
});

async function finish(cancelled) {
  await fetch("/submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answers, cancelled, __session_token: DATA.sessionToken }) });
  document.body.innerHTML = "<main><h1>Submitted</h1><p class='hint'>You can close this tab.</p></main>";
}
document.getElementById("submit").onclick = () => finish(false);
document.getElementById("cancel").onclick = () => finish(true);
render();
</script>
</body>
</html>"""
    return html.replace("__TITLE__", _html_escape(title)).replace("__DATA__", data)


def _open_browser(url: str) -> None:
    if platform.system() == "Darwin":
        subprocess.run(["open", url], check=False)
    else:
        webbrowser.open(url)


def _coerce_browser_result(options: list[Any], payload: dict[str, Any]) -> dict[str, Any]:
    valid_ids = {option.id for option in options}
    selected = [str(item) for item in payload.get("selected", []) if str(item) in valid_ids]
    ordered = [str(item) for item in payload.get("ordered", []) if str(item) in valid_ids]
    selected_set = set(selected)
    ordered = [item for item in ordered if item in selected_set]
    raw_descriptions = payload.get("descriptions", {})
    descriptions = {
        str(key): str(value)
        for key, value in raw_descriptions.items()
        if str(key) in valid_ids
    } if isinstance(raw_descriptions, dict) else {}
    return {
        "mode": "multi_select_reorder",
        "selected": selected,
        "ordered": ordered,
        "descriptions": descriptions,
        "cancelled": bool(payload.get("cancelled", False)),
    }


def _coerce_browser_group_result(groups: list[OptionGroup], payload: dict[str, Any]) -> dict[str, Any]:
    group_labels = {group.id: group.label for group in groups}
    option_ids = {option.id for group in groups for option in group.options}
    raw_selected = payload.get("selected", [])
    selected = [str(item) for item in raw_selected if str(item) in option_ids] if isinstance(raw_selected, list) else []
    selected_set = set(selected)
    raw_grouped_order = payload.get("grouped_order", {})
    grouped_order: dict[str, list[str]] = {}
    if isinstance(raw_grouped_order, dict):
        for group in groups:
            raw_items = raw_grouped_order.get(group.id, [])
            grouped_order[group.id] = [
                str(item)
                for item in raw_items
                if str(item) in option_ids and str(item) in selected_set
            ] if isinstance(raw_items, list) else []
    else:
        grouped_order = {group.id: [] for group in groups}
    ordered = [item for group in groups for item in grouped_order.get(group.id, [])]
    raw_descriptions = payload.get("descriptions", {})
    descriptions = {
        str(key): str(value)
        for key, value in raw_descriptions.items()
        if str(key) in option_ids
    } if isinstance(raw_descriptions, dict) else {}
    return {
        "mode": "multi_select_reorder",
        "layout": "grouped",
        "selected": selected,
        "ordered": ordered,
        "grouped_order": grouped_order,
        "group_labels": group_labels,
        "descriptions": descriptions,
        "cancelled": bool(payload.get("cancelled", False)),
    }


def _coerce_rating_result(options: list[Any], payload: dict[str, Any], *, mode: str) -> dict[str, Any]:
    valid_ids = {option.id for option in options}
    raw_ordered = payload.get("ordered", [])
    ordered = [str(item) for item in raw_ordered if str(item) in valid_ids] if isinstance(raw_ordered, list) else []
    raw_rejected = payload.get("rejected", [])
    rejected = [str(item) for item in raw_rejected if str(item) in valid_ids] if isinstance(raw_rejected, list) else []
    raw_choices = payload.get("choices", [])
    choices = [
        {
            "winner": str(choice.get("winner")),
            "loser": str(choice.get("loser")),
        }
        for choice in raw_choices
        if isinstance(choice, dict)
        and str(choice.get("winner")) in valid_ids
        and str(choice.get("loser")) in valid_ids
    ] if isinstance(raw_choices, list) else []
    raw_scores = payload.get("scores", {})
    scores = {
        str(key): float(value)
        for key, value in raw_scores.items()
        if str(key) in valid_ids and isinstance(value, (int, float))
    } if isinstance(raw_scores, dict) else {}
    ratings = {item: index + 1 for index, item in enumerate(ordered)}
    ratings.update({item: 0 for item in rejected})
    selected = [item for item in ordered if item not in rejected]
    return {
        "mode": "rating_tool",
        "rating_mode": mode,
        "selected": selected,
        "ordered": ordered,
        "rejected": rejected,
        "ratings": ratings,
        "choices": choices,
        "scores": scores,
        "cancelled": bool(payload.get("cancelled", False)),
    }


def _selector_page(
    title: str,
    options: list[dict[str, str]],
    *,
    edit_descriptions: bool = False,
    session_token: str,
) -> str:
    data = json.dumps(
        {
            "title": title,
            "options": options,
            "editDescriptions": edit_descriptions,
            "sessionToken": session_token,
        },
        ensure_ascii=False,
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html_escape(title)}</title>
<style>
:root {{ color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
body {{ margin: 0; background: Canvas; color: CanvasText; }}
main {{ max-width: 760px; margin: 32px auto; padding: 0 20px 28px; }}
h1 {{ font-size: 24px; margin: 0 0 8px; }}
.hint {{ color: color-mix(in srgb, CanvasText 68%, Canvas); margin: 0 0 18px; }}
.list {{ border: 1px solid color-mix(in srgb, CanvasText 18%, Canvas); border-radius: 8px; overflow: hidden; }}
.option {{ display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; padding: 12px 14px; border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, Canvas); background: Canvas; }}
.option:last-child {{ border-bottom: 0; }}
.option[draggable="true"] {{ cursor: grab; }}
.option.dragging {{ opacity: .45; }}
.option.selected {{ background: color-mix(in srgb, Highlight 15%, Canvas); }}
.label {{ font-weight: 600; }}
.description {{ font-size: 13px; color: color-mix(in srgb, CanvasText 62%, Canvas); margin-top: 3px; }}
textarea.description {{ box-sizing: border-box; width: 100%; min-height: 42px; resize: vertical; border: 1px solid color-mix(in srgb, CanvasText 18%, Canvas); border-radius: 6px; background: Canvas; color: CanvasText; padding: 6px 8px; font: inherit; font-size: 13px; }}
.rank {{ min-width: 28px; color: color-mix(in srgb, CanvasText 60%, Canvas); font-variant-numeric: tabular-nums; }}
input[type="checkbox"], input[type="radio"] {{ width: 18px; height: 18px; }}
.drag {{ font-size: 18px; color: color-mix(in srgb, CanvasText 46%, Canvas); user-select: none; }}
footer {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }}
button {{ appearance: none; border: 1px solid color-mix(in srgb, CanvasText 20%, Canvas); border-radius: 7px; background: Canvas; color: CanvasText; padding: 9px 14px; font: inherit; cursor: pointer; }}
button.primary {{ background: Highlight; border-color: Highlight; color: HighlightText; }}
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<p id="hint" class="hint"></p>
<section id="list" class="list"></section>
<footer>
<button id="cancel" type="button">Cancel</button>
<button id="submit" class="primary" type="button">Submit</button>
</footer>
</main>
<script>
const DATA = {data};
let selected = new Set(DATA.options.filter(option => option.selected).map(option => option.id));
let ordered = DATA.options.map(option => option.id);
let dragId = null;
const titleEl = document.getElementById("title");
const hintEl = document.getElementById("hint");
const listEl = document.getElementById("list");
const submitEl = document.getElementById("submit");
const cancelEl = document.getElementById("cancel");

titleEl.textContent = DATA.title;
hintEl.textContent = "Choose one or more options. Drag rows to reorder before submitting.";

function render() {{
  listEl.innerHTML = "";
  const rows = ordered.map(id => DATA.options.find(option => option.id === id)).filter(Boolean);
  rows.forEach((option, index) => {{
    const row = document.createElement("div");
    row.className = "option" + (selected.has(option.id) ? " selected" : "");
    row.draggable = true;
    row.dataset.id = option.id;
    row.innerHTML = `
      ${{controlHtml(option, index)}}
      <div><div class="label"></div><div class="description"></div></div>
      <div class="drag">::</div>
    `;
    row.querySelector(".label").textContent = option.label;
    setDescriptionControl(row, option);
    row.querySelector("input").addEventListener("change", event => {{
      if (event.target.checked) {{
        selected.add(option.id);
      }} else {{
        selected.delete(option.id);
      }}
      row.classList.toggle("selected", event.target.checked);
    }});
    row.addEventListener("click", event => {{
      if (event.target.closest("input, textarea")) return;
      toggle(option.id);
    }});
    row.addEventListener("dragstart", () => {{ dragId = option.id; row.classList.add("dragging"); }});
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dragover", event => event.preventDefault());
    row.addEventListener("drop", event => {{
      event.preventDefault();
      moveBefore(dragId, option.id);
    }});
    listEl.append(row);
  }});
}}

function controlHtml(option, index) {{
  return `<input type="checkbox" ${{selected.has(option.id) ? "checked" : ""}}>`;
}}

function setDescriptionControl(row, option) {{
  const container = row.querySelector(".description");
  if (!DATA.editDescriptions) {{
    container.textContent = option.description || "";
    return;
  }}
  const textarea = document.createElement("textarea");
  textarea.className = "description";
  textarea.value = option.description || "";
  textarea.dataset.field = "description";
  textarea.addEventListener("input", event => {{
    option.description = event.target.value;
  }});
  container.replaceWith(textarea);
}}

function toggle(id) {{
  selected.has(id) ? selected.delete(id) : selected.add(id);
  render();
}}

function moveBefore(fromId, toId) {{
  if (!fromId || fromId === toId) return;
  const next = ordered.filter(id => id !== fromId);
  next.splice(next.indexOf(toId), 0, fromId);
  ordered = next;
  render();
}}

function currentState() {{
  const rows = [...listEl.querySelectorAll(".option")];
  const selectedIds = rows
    .filter(row => row.querySelector("input").checked)
    .map(row => row.dataset.id);
  const orderedIds = rows
    .map(row => row.dataset.id)
    .filter(id => selectedIds.includes(id));
  const descriptions = Object.fromEntries(rows.map(row => [
    row.dataset.id,
    row.querySelector('[data-field="description"]')?.value ?? DATA.options.find(option => option.id === row.dataset.id)?.description ?? ""
  ]));
  selected = new Set(selectedIds);
  ordered = rows.map(row => row.dataset.id);
  return {{ selected: selectedIds, ordered: orderedIds, descriptions }};
}}

async function finish(cancelled) {{
  const state = currentState();
  const payload = {{
    selected: state.selected,
    ordered: state.ordered,
    descriptions: state.descriptions,
    cancelled,
    __session_token: DATA.sessionToken
  }};
  await fetch("/submit", {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(payload) }});
  document.body.innerHTML = "<main><h1>Submitted</h1><p class='hint'>You can close this tab.</p></main>";
}}

submitEl.addEventListener("click", () => finish(false));
cancelEl.addEventListener("click", () => finish(true));
render();
</script>
</body>
</html>"""


def _group_selector_page(
    title: str,
    groups: list[dict[str, Any]],
    *,
    edit_descriptions: bool = False,
    session_token: str,
) -> str:
    data = json.dumps(
        {
            "title": title,
            "groups": groups,
            "editDescriptions": edit_descriptions,
            "sessionToken": session_token,
        },
        ensure_ascii=False,
    )
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: Canvas; color: CanvasText; }
main { max-width: 1180px; margin: 28px auto; padding: 0 20px 28px; }
h1 { font-size: 24px; margin: 0 0 8px; }
.hint { color: color-mix(in srgb, CanvasText 68%, Canvas); margin: 0 0 18px; }
.board { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; align-items: start; }
.column { border: 1px solid color-mix(in srgb, CanvasText 18%, Canvas); border-radius: 8px; min-height: 160px; overflow: hidden; background: color-mix(in srgb, CanvasText 3%, Canvas); }
.column h2 { font-size: 15px; margin: 0; padding: 10px 12px; border-bottom: 1px solid color-mix(in srgb, CanvasText 14%, Canvas); background: Canvas; }
.items { min-height: 112px; padding: 8px; }
.option { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; margin-bottom: 8px; padding: 10px; border: 1px solid color-mix(in srgb, CanvasText 14%, Canvas); border-radius: 7px; background: Canvas; cursor: grab; }
.option:last-child { margin-bottom: 0; }
.option.dragging { opacity: .45; }
.option.selected { background: color-mix(in srgb, Highlight 15%, Canvas); }
.label { font-weight: 600; }
.description { font-size: 13px; color: color-mix(in srgb, CanvasText 62%, Canvas); margin-top: 3px; }
textarea.description { box-sizing: border-box; width: 100%; min-height: 42px; resize: vertical; border: 1px solid color-mix(in srgb, CanvasText 18%, Canvas); border-radius: 6px; background: Canvas; color: CanvasText; padding: 6px 8px; font: inherit; font-size: 13px; }
input[type="checkbox"] { width: 18px; height: 18px; }
.drag { font-size: 18px; color: color-mix(in srgb, CanvasText 46%, Canvas); user-select: none; }
footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
button { appearance: none; border: 1px solid color-mix(in srgb, CanvasText 20%, Canvas); border-radius: 7px; background: Canvas; color: CanvasText; padding: 9px 14px; font: inherit; cursor: pointer; }
button.primary { background: Highlight; border-color: Highlight; color: HighlightText; }
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<p id="hint" class="hint"></p>
<section id="board" class="board"></section>
<footer>
<button id="cancel" type="button">Cancel</button>
<button id="submit" class="primary" type="button">Submit</button>
</footer>
</main>
<script>
const DATA = __DATA__;
let dragRow = null;
const optionById = new Map(DATA.groups.flatMap(group => group.options.map(option => [option.id, option])));
const titleEl = document.getElementById("title");
const hintEl = document.getElementById("hint");
const boardEl = document.getElementById("board");
const submitEl = document.getElementById("submit");
const cancelEl = document.getElementById("cancel");

titleEl.textContent = DATA.title;
hintEl.textContent = "Choose items, drag them within or across columns, then submit the grouped order.";

function render() {
  boardEl.innerHTML = "";
  DATA.groups.forEach(group => {
    const column = document.createElement("section");
    column.className = "column";
    column.dataset.groupId = group.id;
    const heading = document.createElement("h2");
    heading.textContent = group.label;
    const items = document.createElement("div");
    items.className = "items";
    items.addEventListener("dragover", event => event.preventDefault());
    items.addEventListener("drop", event => {
      event.preventDefault();
      if (dragRow) items.append(dragRow);
    });
    group.options.forEach(option => items.append(createRow(option)));
    column.append(heading, items);
    boardEl.append(column);
  });
}

function createRow(option) {
  const row = document.createElement("div");
  row.className = "option" + (option.selected ? " selected" : "");
  row.draggable = true;
  row.dataset.id = option.id;
  row.innerHTML = `
    <input type="checkbox" ${option.selected ? "checked" : ""}>
    <div><div class="label"></div><div class="description"></div></div>
    <div class="drag">::</div>
  `;
  row.querySelector(".label").textContent = option.label;
  setDescriptionControl(row, option);
  row.querySelector("input").addEventListener("change", event => {
    row.classList.toggle("selected", event.target.checked);
  });
  row.addEventListener("click", event => {
    if (event.target.closest("input, textarea")) return;
    const input = row.querySelector("input");
    input.checked = !input.checked;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  row.addEventListener("dragstart", () => {
    dragRow = row;
    row.classList.add("dragging");
  });
  row.addEventListener("dragend", () => {
    row.classList.remove("dragging");
    dragRow = null;
  });
  row.addEventListener("dragover", event => event.preventDefault());
  row.addEventListener("drop", event => {
    event.preventDefault();
    event.stopPropagation();
    if (dragRow && dragRow !== row) row.before(dragRow);
  });
  return row;
}

function setDescriptionControl(row, option) {
  const container = row.querySelector(".description");
  if (!DATA.editDescriptions) {
    container.textContent = option.description || "";
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.className = "description";
  textarea.value = option.description || "";
  textarea.dataset.field = "description";
  container.replaceWith(textarea);
}

function currentState() {
  const groupedOrder = {};
  const selectedIds = [];
  const descriptions = {};
  [...boardEl.querySelectorAll(".column")].forEach(column => {
    const groupId = column.dataset.groupId;
    groupedOrder[groupId] = [];
    [...column.querySelectorAll(".option")].forEach(row => {
      const id = row.dataset.id;
      const checked = row.querySelector("input").checked;
      descriptions[id] = row.querySelector('[data-field="description"]')?.value ?? optionById.get(id)?.description ?? "";
      if (checked) {
        selectedIds.push(id);
        groupedOrder[groupId].push(id);
      }
    });
  });
  return {
    selected: selectedIds,
    ordered: Object.values(groupedOrder).flat(),
    grouped_order: groupedOrder,
    descriptions
  };
}

async function finish(cancelled) {
  const state = currentState();
  const payload = {
    selected: state.selected,
    ordered: state.ordered,
    grouped_order: state.grouped_order,
    descriptions: state.descriptions,
    cancelled,
    __session_token: DATA.sessionToken
  };
  await fetch("/submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  document.body.innerHTML = "<main><h1>Submitted</h1><p class='hint'>You can close this tab.</p></main>";
}

submitEl.addEventListener("click", () => finish(false));
cancelEl.addEventListener("click", () => finish(true));
render();
</script>
</body>
</html>"""
    return html.replace("__TITLE__", _html_escape(title)).replace("__DATA__", data)


def _rating_page(title: str, options: list[dict[str, Any]], *, mode: str, session_token: str) -> str:
    data = json.dumps(
        {"title": title, "mode": mode, "options": options, "sessionToken": session_token},
        ensure_ascii=False,
    )
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: Canvas; color: CanvasText; }
main { max-width: 920px; margin: 28px auto; padding: 0 20px 28px; }
h1 { font-size: 24px; margin: 0 0 8px; }
.hint { color: color-mix(in srgb, CanvasText 68%, Canvas); margin: 0 0 18px; }
.list { display: grid; gap: 8px; }
.option { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; padding: 11px 12px; border: 1px solid color-mix(in srgb, CanvasText 16%, Canvas); border-radius: 8px; background: Canvas; }
.option[draggable="true"] { cursor: grab; }
.option.dragging { opacity: .45; }
.option.focused { outline: 2px solid Highlight; outline-offset: 1px; }
.rank { width: 30px; height: 30px; border-radius: 999px; display: grid; place-items: center; background: Highlight; color: HighlightText; font-weight: 700; }
.label { font-weight: 650; }
.description { font-size: 13px; color: color-mix(in srgb, CanvasText 62%, Canvas); margin-top: 3px; }
.actions { display: flex; gap: 6px; }
.choice { display: grid; grid-template-columns: 1fr auto 1fr; gap: 14px; align-items: stretch; }
.choice button.card { min-height: 160px; text-align: left; font-size: 18px; }
.versus { display: grid; place-items: center; font-weight: 800; color: color-mix(in srgb, CanvasText 55%, Canvas); }
details { margin-top: 12px; border: 1px dashed color-mix(in srgb, CanvasText 20%, Canvas); border-radius: 8px; padding: 8px 10px; }
footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
button { appearance: none; border: 1px solid color-mix(in srgb, CanvasText 20%, Canvas); border-radius: 7px; background: Canvas; color: CanvasText; padding: 9px 14px; font: inherit; cursor: pointer; }
button.primary { background: Highlight; border-color: Highlight; color: HighlightText; }
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<p id="hint" class="hint"></p>
<section id="app"></section>
<footer>
<button id="cancel" type="button">Cancel</button>
<button id="submit" class="primary" type="button">Submit</button>
</footer>
</main>
<script>
const DATA = __DATA__;
// Only rank mode pre-seeds the ordered list; accept/reject and face-off modes
// start empty so every option is presented as "remaining" to review.
let order = DATA.mode === "rank" ? DATA.options.filter(o => o.selected !== false).map(o => o.id) : [];
let rejected = DATA.mode === "rank" ? DATA.options.filter(o => o.selected === false).map(o => o.id) : [];
let choices = [];
let scores = Object.fromEntries(DATA.options.map(o => [o.id, 0]));
let pairIndex = 0;
let dragId = null;
const byId = new Map(DATA.options.map(o => [o.id, o]));
const app = document.getElementById("app");
document.getElementById("title").textContent = DATA.title;
document.getElementById("hint").textContent = hint();

function hint() {
  if (DATA.mode === "tinder") return "Accept (→/Enter) or reject (←/Backspace) one at a time. Esc cancels.";
  if (DATA.mode === "facemash") return "Pick the better option: ← left, → right. Esc cancels.";
  if (DATA.mode === "pair") return "Pick winners: ← left, → right. Esc cancels.";
  return "↑/↓ move focus, Shift+↑/↓ reorder, r reject, Enter submit, Esc cancel. Drag also works.";
}
function renderOption(id, rank, isRejected=false) {
  const option = byId.get(id);
  const row = document.createElement("div");
  row.className = "option";
  row.draggable = !isRejected;
  row.dataset.id = id;
  row.innerHTML = `<div class="rank">${isRejected ? "0" : rank}</div><div><div class="label"></div><div class="description"></div></div><div class="actions"></div>`;
  row.querySelector(".label").textContent = option.label;
  row.querySelector(".description").textContent = option.description || "";
  const btn = document.createElement("button");
  btn.textContent = isRejected ? "Restore" : "Reject";
  btn.onclick = () => { moveReject(id, !isRejected); render(); };
  row.querySelector(".actions").append(btn);
  row.addEventListener("dragstart", () => { dragId = id; row.classList.add("dragging"); });
  row.addEventListener("dragend", () => row.classList.remove("dragging"));
  row.addEventListener("dragover", event => event.preventDefault());
  row.addEventListener("drop", event => { event.preventDefault(); moveBefore(dragId, id); render(); });
  return row;
}
function moveReject(id, reject) {
  order = order.filter(x => x !== id);
  rejected = rejected.filter(x => x !== id);
  if (reject) rejected.push(id); else order.push(id);
}
function moveBefore(fromId, toId) {
  if (!fromId || fromId === toId) return;
  const next = order.filter(id => id !== fromId);
  next.splice(next.indexOf(toId), 0, fromId);
  order = next;
}
function renderRank() {
  app.innerHTML = "";
  const list = document.createElement("div");
  list.className = "list";
  order.forEach((id, index) => list.append(renderOption(id, index + 1)));
  app.append(list);
  const details = document.createElement("details");
  details.innerHTML = `<summary>Rejected (${rejected.length})</summary>`;
  const rejectList = document.createElement("div");
  rejectList.className = "list";
  rejected.forEach(id => rejectList.append(renderOption(id, 0, true)));
  details.append(rejectList);
  app.append(details);
}
function renderTinder() {
  app.innerHTML = "";
  const remaining = DATA.options.map(o => o.id).filter(id => !order.includes(id) && !rejected.includes(id));
  if (!remaining.length) { app.innerHTML = "<p class='hint'>All options reviewed.</p>"; return; }
  const id = remaining[0];
  const option = byId.get(id);
  app.innerHTML = `<div class="option"><div class="rank">${remaining.length}</div><div><div class="label"></div><div class="description"></div></div><div class="actions"><button id="reject">Reject</button><button id="accept" class="primary">Accept</button></div></div>`;
  app.querySelector(".label").textContent = option.label;
  app.querySelector(".description").textContent = option.description || "";
  app.querySelector("#accept").onclick = () => { order.push(id); render(); };
  app.querySelector("#reject").onclick = () => { rejected.push(id); render(); };
}
function pairs() {
  const ids = DATA.options.map(o => o.id);
  const out = [];
  for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) out.push([ids[i], ids[j]]);
  return out;
}
function choosePair(winner, loser) {
  choices.push({winner, loser});
  scores[winner] = (scores[winner] || 0) + 1;
  scores[loser] = scores[loser] || 0;
  pairIndex++;
  order = [...DATA.options.map(o => o.id)].sort((a, b) => (scores[b] || 0) - (scores[a] || 0));
  render();
}
function renderPair() {
  const allPairs = pairs();
  app.innerHTML = "";
  if (pairIndex >= allPairs.length) { app.innerHTML = "<p class='hint'>All pairs reviewed.</p>"; return; }
  const [a, b] = allPairs[pairIndex];
  const ao = byId.get(a), bo = byId.get(b);
  app.innerHTML = `<div class="choice"><button class="card" id="a"><strong></strong><p></p></button><div class="versus">vs</div><button class="card" id="b"><strong></strong><p></p></button></div><p class="hint">${pairIndex + 1} / ${allPairs.length}</p>`;
  app.querySelector("#a strong").textContent = ao.label;
  app.querySelector("#a p").textContent = ao.description || "";
  app.querySelector("#b strong").textContent = bo.label;
  app.querySelector("#b p").textContent = bo.description || "";
  app.querySelector("#a").onclick = () => choosePair(a, b);
  app.querySelector("#b").onclick = () => choosePair(b, a);
}
function render() {
  if (DATA.mode === "tinder") renderTinder();
  else if (DATA.mode === "facemash" || DATA.mode === "pair") renderPair();
  else renderRank();
}
async function finish(cancelled) {
  const payload = { ordered: order, rejected, choices, scores, cancelled, __session_token: DATA.sessionToken };
  await fetch("/submit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  document.body.innerHTML = "<main><h1>Submitted</h1><p class='hint'>You can close this tab.</p></main>";
}
document.getElementById("submit").onclick = () => finish(false);
document.getElementById("cancel").onclick = () => finish(true);

// --- Keyboard navigation -------------------------------------------------
// tinder:    left/Backspace = reject, right/Enter = accept
// facemash/pair: left = pick left card, right = pick right card
// rank:     up/down move focus, shift+up/down reorder, r/Delete reject-toggle
// global:   Enter (rank) submit, Esc cancel
let focusIdx = 0;
function clampFocus() {
  if (focusIdx < 0) focusIdx = 0;
  if (focusIdx > order.length - 1) focusIdx = order.length - 1;
}
function paintFocus() {
  const rows = [...app.querySelectorAll(".list > .option")];
  rows.forEach((r, i) => r.classList.toggle("focused", i === focusIdx));
  rows[focusIdx]?.scrollIntoView({ block: "nearest" });
}
const _origRenderRank = renderRank;
renderRank = function () {
  _origRenderRank();
  clampFocus();
  paintFocus();
};
document.addEventListener("keydown", event => {
  const k = event.key;
  if (k === "Escape") { event.preventDefault(); finish(true); return; }
  if (DATA.mode === "tinder") {
    const remaining = DATA.options.map(o => o.id).filter(id => !order.includes(id) && !rejected.includes(id));
    const id = remaining[0];
    if (!id) { if (k === "Enter") finish(false); return; }
    if (k === "ArrowRight" || k === "Enter") { event.preventDefault(); order.push(id); render(); }
    else if (k === "ArrowLeft" || k === "Backspace") { event.preventDefault(); rejected.push(id); render(); }
    return;
  }
  if (DATA.mode === "facemash" || DATA.mode === "pair") {
    const allPairs = pairs();
    if (pairIndex >= allPairs.length) { if (k === "Enter") finish(false); return; }
    const [a, b] = allPairs[pairIndex];
    if (k === "ArrowLeft") { event.preventDefault(); choosePair(a, b); }
    else if (k === "ArrowRight") { event.preventDefault(); choosePair(b, a); }
    return;
  }
  // rank mode
  if (k === "Enter") { event.preventDefault(); finish(false); return; }
  if (!order.length) return;
  if (k === "ArrowDown") {
    event.preventDefault();
    if (event.shiftKey && focusIdx < order.length - 1) {
      const id = order[focusIdx];
      order.splice(focusIdx, 1); order.splice(focusIdx + 1, 0, id);
      focusIdx++; render();
    } else { focusIdx++; clampFocus(); paintFocus(); }
  } else if (k === "ArrowUp") {
    event.preventDefault();
    if (event.shiftKey && focusIdx > 0) {
      const id = order[focusIdx];
      order.splice(focusIdx, 1); order.splice(focusIdx - 1, 0, id);
      focusIdx--; render();
    } else { focusIdx--; clampFocus(); paintFocus(); }
  } else if (k === "r" || k === "Delete") {
    event.preventDefault();
    const id = order[focusIdx];
    if (id) { moveReject(id, true); render(); }
  }
});

render();
if (DATA.mode === "rank") paintFocus();
</script>
</body>
</html>"""
    return html.replace("__TITLE__", _html_escape(title)).replace("__DATA__", data)


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _error_result(message: str) -> dict[str, Any]:
    return {
        "mode": "multi_select_reorder",
        "selected": [],
        "ordered": [],
        "cancelled": True,
        "error": message,
    }


@mcp.tool()
def multi_select_reorder(
    title: str,
    options: list[Any] | None = None,
    initial_selected: list[Any] | None = None,
    edit_descriptions: bool = False,
    groups: list[Any] | None = None,
) -> dict[str, Any]:
    """Open a web UI to select and reorder options by drag-and-drop.

    Options are selected by default. Pass option objects with selected=false or
    pass initial_selected with option ids to override the initial state. Options
    can also be compact tuples/lists: [id, label, description, selected].
    Set edit_descriptions=true to edit descriptions in the same browser window.
    For board/grid ordering, pass groups as columns with nested options/items.
    """
    return _select(
        title,
        options,
        initial_selected=initial_selected,
        edit_descriptions=edit_descriptions,
        groups=groups,
    )


@mcp.tool()
def rating_tool(
    title: str,
    options: list[Any] | None = None,
    mode: str = "rank",
    initial_selected: list[Any] | None = None,
    questions: list[Any] | None = None,
) -> dict[str, Any]:
    """Open a local rating UI. Every mode is keyboard-navigable.

    Modes:
    - rank: drag-and-drop preference ranking with reject/rating 0.
      Keys: up/down focus, shift+up/down reorder, r reject, Enter submit.
    - tinder: single-option accept/reject flow.
      Keys: right/Enter accept, left/Backspace reject.
    - facemash / pair: pairwise winner face-offs, scored by wins.
      Keys: left = left card, right = right card.
    - choice: survey of many questions, pick one candidate answer each.
      Pass `questions` as groups: [{id, label, options:[{id,label,description,selected}]}].
      Keys: left/right choose a candidate, up/down move between questions, Enter submit.
    Esc cancels in every mode.
    """
    if mode == "choice":
        if questions is None:
            return _error_result("choice mode requires `questions` (a list of groups)")
        return _choice_in_browser(title, normalize_groups(questions, initial_selected=initial_selected))
    if options is None:
        return _error_result("options are required for this mode")
    return _rate_in_browser(title, options, mode=mode, initial_selected=initial_selected)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
