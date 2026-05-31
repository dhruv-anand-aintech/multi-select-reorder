import pytest

from multi_select_reorder.selector import normalize_groups, normalize_options


def test_normalize_options_preserves_existing_tuple_and_selection_contract() -> None:
    options = normalize_options(
        [
            ["a", "Alpha"],
            ["b", "Beta", "Detail", False],
            {"id": "c", "label": "Gamma", "selected": True},
        ]
    )

    assert [option.id for option in options] == ["a", "b", "c"]
    assert [option.label for option in options] == ["Alpha", "Beta", "Gamma"]
    assert [option.selected for option in options] == [True, False, True]
    assert options[1].description == "Detail"


def test_initial_selected_overrides_group_item_defaults() -> None:
    groups = normalize_groups(
        [
            {
                "id": "one",
                "label": "One",
                "options": [
                    ["a", "Alpha"],
                    ["b", "Beta"],
                ],
            },
            ["two", "Two", [{"id": "c", "label": "Gamma", "selected": True}]],
        ],
        initial_selected=["b"],
    )

    assert [group.id for group in groups] == ["one", "two"]
    assert [option.selected for option in groups[0].options] == [False, True]
    assert [option.selected for option in groups[1].options] == [False]


def test_group_options_must_have_unique_ids() -> None:
    with pytest.raises(ValueError, match="duplicate option id"):
        normalize_groups(
            [
                {"id": "one", "options": [["a", "Alpha"]]},
                {"id": "two", "options": [["a", "Again"]]},
            ]
        )
