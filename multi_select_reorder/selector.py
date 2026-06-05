import curses
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    description: str = ""
    selected: bool = True


@dataclass(frozen=True)
class OptionGroup:
    id: str
    label: str
    options: list[Option]


def normalize_options(raw_options: list[Any], initial_selected: list[Any] | None = None) -> list[Option]:
    initial_selected_ids = None
    if initial_selected is not None:
        initial_selected_ids = {str(item) for item in initial_selected}

    options: list[Option] = []
    for index, item in enumerate(raw_options):
        option = _normalize_option(item, index=index, initial_selected_ids=initial_selected_ids)
        if option is not None:
            options.append(option)
    return options


def normalize_groups(raw_groups: list[Any], initial_selected: list[Any] | None = None) -> list[OptionGroup]:
    initial_selected_ids = None
    if initial_selected is not None:
        initial_selected_ids = {str(item) for item in initial_selected}

    groups: list[OptionGroup] = []
    seen_option_ids: set[str] = set()
    for index, item in enumerate(raw_groups):
        group_id, label, raw_options = _normalize_group_item(item, index=index)
        options: list[Option] = []
        for option_index, raw_option in enumerate(raw_options):
            option = _normalize_option(
                raw_option,
                index=option_index,
                initial_selected_ids=initial_selected_ids,
            )
            if option is None:
                continue
            if option.id in seen_option_ids:
                raise ValueError(f"duplicate option id across groups: {option.id}")
            seen_option_ids.add(option.id)
            options.append(option)
        groups.append(OptionGroup(id=group_id, label=label, options=options))
    return groups


def _normalize_option(
    item: Any,
    *,
    index: int,
    initial_selected_ids: set[str] | None,
) -> Option | None:
    if isinstance(item, str):
        is_selected = initial_selected_ids is None or item in initial_selected_ids
        return Option(id=item, label=item, selected=is_selected)
    if isinstance(item, dict):
        label = str(item.get("label") or item.get("name") or item.get("id") or "")
        option_id = str(item.get("id") or label or index)
        description = str(item.get("description") or "")
        if not label:
            return None
        is_selected = (
            option_id in initial_selected_ids
            if initial_selected_ids is not None
            else bool(item.get("selected", True))
        )
        return Option(
            id=option_id,
            label=label,
            description=description,
            selected=is_selected,
        )
    if isinstance(item, (list, tuple)):
        if not item:
            return None
        option_id = str(item[0])
        label = str(item[1] if len(item) > 1 else item[0])
        description = str(item[2] if len(item) > 2 else "")
        is_selected = (
            option_id in initial_selected_ids
            if initial_selected_ids is not None
            else bool(item[3] if len(item) > 3 else True)
        )
        return Option(
            id=option_id,
            label=label,
            description=description,
            selected=is_selected,
        )
    label = str(item)
    is_selected = initial_selected_ids is None or label in initial_selected_ids
    return Option(id=label, label=label, selected=is_selected)


def _normalize_group_item(item: Any, *, index: int) -> tuple[str, str, list[Any]]:
    if isinstance(item, str):
        return item, item, []
    if isinstance(item, dict):
        label = str(item.get("label") or item.get("name") or item.get("id") or f"Group {index + 1}")
        group_id = str(item.get("id") or label or index)
        raw_options = item.get("options", item.get("items", []))
        if not isinstance(raw_options, list):
            raise ValueError(f"group {group_id} options must be a list")
        return group_id, label, raw_options
    if isinstance(item, (list, tuple)):
        if not item:
            return str(index), f"Group {index + 1}", []
        group_id = str(item[0])
        label = str(item[1] if len(item) > 1 else item[0])
        raw_options = item[2] if len(item) > 2 else []
        if not isinstance(raw_options, list):
            raise ValueError(f"group {group_id} options must be a list")
        return group_id, label, raw_options
    label = str(item)
    return label, label, []


def load_options(
    input_path: str | None,
    stdin: TextIO = sys.stdin,
    inline_data: str | None = None,
) -> tuple[str, list[Option]]:
    if inline_data is not None:
        data = inline_data
    elif input_path:
        data = Path(input_path).read_text(encoding="utf-8")
    else:
        data = stdin.read()

    data = data.strip()
    if not data:
        raise ValueError("no options provided")

    if data[0] in "[{":
        payload = json.loads(data)
        if isinstance(payload, dict):
            title = str(payload.get("title") or "Select")
            raw_options = payload.get("options") or []
            initial_selected = payload.get("initial_selected")
        else:
            title = "Select"
            raw_options = payload
            initial_selected = None
    else:
        title = "Select"
        raw_options = [line for line in data.splitlines() if line.strip()]
        initial_selected = None

    options = normalize_options(raw_options, initial_selected=initial_selected)
    if not options:
        raise ValueError("no valid options provided")
    return title, options


def run_selector(
    options: list[Option],
    *,
    mode: str,
    title: str = "Select",
    tty_path: str | None = None,
) -> dict[str, Any]:
    if mode not in {"single", "multi", "rank"}:
        raise ValueError("mode must be one of: single, multi, rank")
    if not options:
        raise ValueError("at least one option is required")

    def _wrapped(stdscr: Any) -> dict[str, Any]:
        return _selector_loop(stdscr, options=options, mode=mode, title=title)

    if tty_path:
        with _redirect_stdio_to_tty(tty_path):
            return curses.wrapper(_wrapped)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise OSError("terminal selector requires an interactive TTY")
    return curses.wrapper(_wrapped)


@contextmanager
def _redirect_stdio_to_tty(tty_path: str) -> Any:
    tty_fd = os.open(tty_path, os.O_RDWR)
    saved_stdin = os.dup(0)
    saved_stdout = os.dup(1)
    try:
        os.dup2(tty_fd, 0)
        os.dup2(tty_fd, 1)
        yield
    finally:
        os.dup2(saved_stdin, 0)
        os.dup2(saved_stdout, 1)
        os.close(saved_stdin)
        os.close(saved_stdout)
        os.close(tty_fd)


def _selector_loop(stdscr: Any, *, options: list[Option], mode: str, title: str) -> dict[str, Any]:
    curses.curs_set(0)
    stdscr.keypad(True)

    # Explicit color pairs so dark-mode terminals get readable highlights
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # cursor highlight
    curses.init_pair(2, curses.COLOR_YELLOW, -1)                  # title
    curses.init_pair(3, curses.COLOR_WHITE, -1)                   # normal text
    HIGHLIGHT = curses.color_pair(1) | curses.A_BOLD
    TITLE_ATTR = curses.color_pair(2) | curses.A_BOLD
    NORMAL_ATTR = curses.color_pair(3)
    DIM_ATTR = curses.A_DIM

    selected: set[int] = {index for index, option in enumerate(options) if option.selected}
    ordered: list[int] = list(range(len(options)))
    cursor = 0
    offset = 0

    while True:
        height, width = stdscr.getmaxyx()
        visible_height = max(1, height - 5)
        if cursor < offset:
            offset = cursor
        if cursor >= offset + visible_height:
            offset = cursor - visible_height + 1

        stdscr.erase()
        help_text = _help_text(mode)
        _add_line(stdscr, 0, 0, title[: width - 1], TITLE_ATTR)
        _add_line(stdscr, 1, 0, help_text[: width - 1], DIM_ATTR)

        for row, option_index in enumerate(range(offset, min(len(options), offset + visible_height)), start=3):
            option = options[option_index]
            marker = _marker(option_index, selected, ordered, mode)
            prefix = "> " if option_index == cursor else "  "
            line = f"{prefix}{marker} {option.label}"
            attr = HIGHLIGHT if option_index == cursor else NORMAL_ATTR
            _add_line(stdscr, row, 0, line[: width - 1], attr)
            if option.description and width > 32:
                desc = f"    {option.description}"
                _add_line(stdscr, row + 1, 0, desc[: width - 1], DIM_ATTR)

        status = f"{len(options)} options"
        if mode == "multi":
            status += f" | {len(selected)} selected"
        if mode == "rank":
            status += f" | {len(ordered)} ranked"
        _add_line(stdscr, height - 1, 0, status[: width - 1], DIM_ATTR)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(options) - 1, cursor + 1)
        elif key in (curses.KEY_HOME, ord("g")):
            cursor = 0
        elif key in (curses.KEY_END, ord("G")):
            cursor = len(options) - 1
        elif key in (ord("q"), 27):
            return _result(options, mode, selected, ordered, cancelled=True)
        elif key in (10, 13):
            if mode == "single":
                selected = {cursor}
            return _result(options, mode, selected, ordered, cancelled=False)
        elif key == ord(" "):
            if mode == "multi":
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
            elif mode == "rank":
                if cursor in ordered:
                    ordered.remove(cursor)
                else:
                    ordered.append(cursor)


def _help_text(mode: str) -> str:
    if mode == "single":
        return "Up/down to move, Enter to choose, q/Esc to cancel"
    if mode == "multi":
        return "Up/down to move, Space to toggle, Enter to confirm, q/Esc to cancel"
    return "Up/down to move, Space to add/remove from ranking, Enter to confirm, q/Esc to cancel"


def _marker(index: int, selected: set[int], ordered: list[int], mode: str) -> str:
    if mode == "single":
        return "( )"
    if mode == "multi":
        return "[x]" if index in selected else "[ ]"
    if index in ordered:
        return f"{ordered.index(index) + 1:>2}."
    return " --"


def _result(
    options: list[Option],
    mode: str,
    selected: set[int],
    ordered: list[int],
    *,
    cancelled: bool,
) -> dict[str, Any]:
    selected_ids = [options[index].id for index in sorted(selected)]
    ordered_ids = [options[index].id for index in ordered]
    if mode == "rank":
        selected_ids = ordered_ids
    return {
        "mode": mode,
        "selected": selected_ids,
        "ordered": ordered_ids,
        "cancelled": cancelled,
    }


def _add_line(stdscr: Any, y: int, x: int, text: str, attr: int = curses.A_NORMAL) -> None:
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass
