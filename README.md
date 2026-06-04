# Multi Select Reorder

Local browser UI for agent-driven choice selection, drag-and-drop reordering,
and first-pass grouped board ordering.

The main interface is the MCP stdio tool `multi_select_reorder`. When called,
it starts a temporary localhost HTTP server, opens a browser page, waits for
submit or cancel, returns JSON to the agent, and shuts the temporary server
down. No external API calls are made by the tool.

## Install

```bash
uv sync --extra dev
```

Run the MCP server directly:

```bash
uv run python -m multi_select_reorder.mcp_server
```

Or use Python directly if dependencies are already installed:

```bash
python3 -m multi_select_reorder.mcp_server
```

## Generic MCP stdio config

Point any MCP-capable agent at this project directory:

```json
{
  "mcpServers": {
    "multi-select-reorder": {
      "command": "python3",
      "args": ["-m", "multi_select_reorder.mcp_server"],
      "cwd": "/absolute/path/to/multi-select-reorder"
    }
  }
}
```

The included `.mcp.json` uses the same server command with a relative `cwd` for
plugin-style installs.

## Codex, Claude, and Cursor plugin metadata

This repo includes:

- `.cursor-plugin/plugin.json`
- `.codex-plugin/plugin.json`
- `.claude-plugin/plugin.json`
- `.mcp.json`
- `skills/multi-select-reorder/SKILL.md`

### Cursor local install

Symlink or copy this repo into Cursor’s local plugin directory, then enable the plugin in Cursor Settings:

```bash
mkdir -p ~/.cursor/plugins/local
ln -sfn "$(pwd)" ~/.cursor/plugins/local/multi-select-reorder
```

Reload the window (or restart Cursor) so the `multi-select-reorder` skill and MCP server are discovered.

Those files are intended to keep the same Codex MCP tool name:
`multi_select_reorder`.

## 1D usage

Existing calls remain compatible:

```json
{
  "title": "Pick work",
  "options": [
    {"id": "a", "label": "Alpha"},
    {"id": "b", "label": "Beta", "selected": false}
  ],
  "initial_selected": ["a"],
  "edit_descriptions": true
}
```

Options are selected by default. To override initial state, pass option objects
with `selected: false`, or pass `initial_selected` as a list of option ids.

For compact calls, options can be tuple-style JSON arrays:

```json
{
  "title": "Pick work",
  "options": [
    ["a", "Alpha"],
    ["b", "Beta", "Optional detail", false]
  ]
}
```

Tuple positions are `[id, label, description, selected]`; omitted values use
the same defaults as object options.

1D result shape:

```json
{
  "mode": "multi_select_reorder",
  "selected": ["id-a", "id-b"],
  "ordered": ["id-b", "id-a"],
  "descriptions": {"id-a": "Edited description"},
  "cancelled": false
}
```

## Grouped board usage

Pass `groups` to show named columns. Items can be dragged within a column or
across columns before submitting.

```json
{
  "title": "Order showcase sections",
  "groups": [
    {
      "id": "tools",
      "label": "Tools",
      "options": [
        ["cloudsweeper", "CloudSweeper"],
        ["bandwidth", "BandwidthPlusApps"]
      ]
    },
    {
      "id": "demos",
      "label": "Demos",
      "options": [
        {"id": "lineage", "label": "Lineage DNA Viz", "selected": false}
      ]
    }
  ]
}
```

Group objects accept `options` or `items`. Compact group arrays use
`[id, label, options]`.

Grouped result shape:

```json
{
  "mode": "multi_select_reorder",
  "layout": "grouped",
  "selected": ["cloudsweeper", "bandwidth"],
  "ordered": ["bandwidth", "cloudsweeper"],
  "grouped_order": {
    "tools": ["bandwidth"],
    "demos": ["cloudsweeper"]
  },
  "group_labels": {
    "tools": "Tools",
    "demos": "Demos"
  },
  "descriptions": {},
  "cancelled": false
}
```

`ordered` is the selected flat order by column order. `grouped_order` is the
selected order keyed by group id.

## Legacy CLI

`bin/multi-select-reorder` provides the older terminal selector CLI for direct
shell usage. The MCP tool uses the browser workflow by default.

```bash
bin/multi-select-reorder --mode multi --option Alpha --option Beta
```

## Test

```bash
uv run --extra dev pytest
uv run python scripts/smoke.py
```
