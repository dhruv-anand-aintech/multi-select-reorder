from multi_select_reorder.mcp_server import (
    _SESSION_TOKEN_FIELD,
    _choice_page,
    _coerce_browser_group_result,
    _coerce_browser_result,
    _coerce_rating_result,
    _group_selector_page,
    _is_valid_session_payload,
    _read_json_payload,
    _rating_page,
    _selector_page,
)
from multi_select_reorder.selector import normalize_groups, normalize_options
from http import HTTPStatus
from io import BytesIO


class _FakeHandler:
    def __init__(self, body: bytes, content_length: str | None = None) -> None:
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.rfile = BytesIO(body)
        self.errors: list[HTTPStatus] = []

    def send_error(self, code: HTTPStatus) -> None:
        self.errors.append(code)


def test_1d_browser_result_keeps_selected_order_only() -> None:
    options = normalize_options([["a", "Alpha"], ["b", "Beta"], ["c", "Gamma"]])

    result = _coerce_browser_result(
        options,
        {
            "selected": ["c", "a"],
            "ordered": ["b", "c", "a", "missing"],
            "descriptions": {"a": "Edited", "missing": "Nope"},
            "cancelled": False,
        },
    )

    assert result == {
        "mode": "multi_select_reorder",
        "selected": ["c", "a"],
        "ordered": ["c", "a"],
        "descriptions": {"a": "Edited"},
        "cancelled": False,
    }


def test_group_browser_result_returns_grouped_and_flat_selected_order() -> None:
    groups = normalize_groups(
        [
            ["draft", "Draft", [["a", "Alpha"], ["b", "Beta"]]],
            ["live", "Live", [["c", "Gamma"]]],
        ]
    )

    result = _coerce_browser_group_result(
        groups,
        {
            "selected": ["c", "a"],
            "grouped_order": {
                "draft": ["b", "a"],
                "live": ["c", "missing"],
            },
            "descriptions": {"c": "Edited", "missing": "Nope"},
            "cancelled": False,
        },
    )

    assert result == {
        "mode": "multi_select_reorder",
        "layout": "grouped",
        "selected": ["c", "a"],
        "ordered": ["a", "c"],
        "grouped_order": {
            "draft": ["a"],
            "live": ["c"],
        },
        "group_labels": {
            "draft": "Draft",
            "live": "Live",
        },
        "descriptions": {"c": "Edited"},
        "cancelled": False,
    }


def test_rating_result_returns_rank_reject_and_pair_choices() -> None:
    options = normalize_options([["a", "Alpha"], ["b", "Beta"], ["c", "Gamma"]])

    result = _coerce_rating_result(
        options,
        {
            "ordered": ["b", "a", "missing"],
            "rejected": ["c"],
            "choices": [
                {"winner": "b", "loser": "a"},
                {"winner": "missing", "loser": "a"},
            ],
            "scores": {"b": 2, "a": 1, "missing": 9},
            "cancelled": False,
        },
        mode="facemash",
    )

    assert result == {
        "mode": "rating_tool",
        "rating_mode": "facemash",
        "selected": ["b", "a"],
        "ordered": ["b", "a"],
        "rejected": ["c"],
        "ratings": {"b": 1, "a": 2, "c": 0},
        "choices": [{"winner": "b", "loser": "a"}],
        "scores": {"b": 2.0, "a": 1.0},
        "cancelled": False,
    }


def test_session_token_validation_requires_exact_payload_token() -> None:
    assert _is_valid_session_payload({_SESSION_TOKEN_FIELD: "abc"}, "abc")
    assert not _is_valid_session_payload({}, "abc")
    assert not _is_valid_session_payload({_SESSION_TOKEN_FIELD: "wrong"}, "abc")
    assert not _is_valid_session_payload([], "abc")


def test_read_json_payload_rejects_bad_content_lengths() -> None:
    for value in ("abc", "-1"):
        handler = _FakeHandler(b"{}", value)
        assert _read_json_payload(handler) is None
        assert handler.errors == [HTTPStatus.BAD_REQUEST]


def test_read_json_payload_rejects_oversized_body_before_reading() -> None:
    handler = _FakeHandler(b"{}", str(1024 * 1024 + 1))
    assert _read_json_payload(handler) is None
    assert handler.errors == [HTTPStatus.REQUEST_ENTITY_TOO_LARGE]
    assert handler.rfile.tell() == 0


def test_read_json_payload_rejects_malformed_json() -> None:
    handler = _FakeHandler(b"{", "1")
    assert _read_json_payload(handler) is None
    assert handler.errors == [HTTPStatus.BAD_REQUEST]


def test_read_json_payload_returns_valid_json() -> None:
    handler = _FakeHandler(b'{"ok": true}', "12")
    assert _read_json_payload(handler) == {"ok": True}
    assert handler.errors == []


def test_browser_pages_embed_session_token_in_submit_payload() -> None:
    pages = [
        _selector_page("Pick", [{"id": "a", "label": "Alpha", "description": "", "selected": True}], session_token="abc"),
        _group_selector_page(
            "Group",
            [
                {
                    "id": "g",
                    "label": "Group",
                    "options": [{"id": "a", "label": "Alpha", "description": "", "selected": True}],
                }
            ],
            session_token="abc",
        ),
        _rating_page(
            "Rate",
            [{"id": "a", "label": "Alpha", "description": "", "selected": True}],
            mode="rank",
            session_token="abc",
        ),
        _choice_page(
            "Choose",
            [
                {
                    "id": "q",
                    "label": "Question",
                    "options": [{"id": "a", "label": "Alpha", "description": "", "selected": True}],
                }
            ],
            session_token="abc",
        ),
    ]

    for page in pages:
        assert '"sessionToken": "abc"' in page
        assert f"{_SESSION_TOKEN_FIELD}: DATA.sessionToken" in page
