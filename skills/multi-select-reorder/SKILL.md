---
name: multi-select-reorder
description: Use when the user wants an interactive ad hoc web page for choosing multiple options, reordering them, or moving items across named groups before submitting.
---

# Multi Select Reorder

Use this skill when a coding agent should present choices in a temporary local web UI, let the user select multiple items, reorder them with drag-and-drop, optionally move items across named groups/columns, and return the submitted result.

Prefer the MCP tool when available:

- `multi_select_reorder`: checkbox multi-select plus drag-and-drop ordering. Pass `groups` for board/column ordering.

Options are initially selected by default. To set initial state manually, pass
option objects with `selected: false`, or pass `initial_selected` as a list of
option ids. For compact calls, options can be tuple-style JSON arrays in the
form `[id, label, description, selected]`, with omitted trailing values using
the normal defaults.

Pass `edit_descriptions: true` when the user should be able to edit option
descriptions in the same browser window.

For grouped board ordering, pass `groups` as a list of columns. Each group can
be an object like `{"id": "draft", "label": "Draft", "options": [...]}` or a
tuple-style array `[id, label, options]`. Options inside groups use the same
object/string/tuple formats as the 1D API. The result keeps the old flat fields
and adds:

- `layout`: `grouped`.
- `grouped_order`: selected option ids keyed by group id.
- `group_labels`: group labels keyed by group id.

The selector writes JSON with:

- `mode`: `multi_select_reorder`.
- `selected`: selected option ids.
- `ordered`: selected option ids in the submitted flat order.
- `descriptions`: final descriptions keyed by option id.
- `cancelled`: whether the user cancelled.

Do not use this skill for terminal-native TUI selection. It is specifically for browser-based multi-select reorder flows.
