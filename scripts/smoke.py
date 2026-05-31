from __future__ import annotations

from multi_select_reorder.mcp_server import _coerce_browser_group_result
from multi_select_reorder.selector import normalize_groups, normalize_options


def main() -> int:
    options = normalize_options([["a", "Alpha"], ["b", "Beta", "Detail", False]])
    assert [option.id for option in options] == ["a", "b"]
    assert [option.selected for option in options] == [True, False]

    groups = normalize_groups(
        [
            ["todo", "Todo", [["a", "Alpha"], ["b", "Beta"]]],
            ["done", "Done", [["c", "Gamma"]]],
        ],
        initial_selected=["a", "c"],
    )
    result = _coerce_browser_group_result(
        groups,
        {
            "selected": ["c", "a"],
            "grouped_order": {"todo": ["a"], "done": ["c"]},
            "cancelled": False,
        },
    )
    assert result["ordered"] == ["a", "c"]
    assert result["grouped_order"] == {"todo": ["a"], "done": ["c"]}
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
