import pytest

from creditcard.parsers.registry import PARSERS, UnknownParserError, get_parser


def test_unknown_parser_name_raises_with_available_names():
    with pytest.raises(UnknownParserError) as exc:
        get_parser("barclays")
    assert "barclays" in str(exc.value)


def test_registry_is_a_dict():
    """Issuer parsers land in later tasks; the registry works before they exist."""
    assert isinstance(PARSERS, dict)


def test_error_lists_registered_names_when_some_exist():
    """The message must help, not just say no."""
    PARSERS["fakebank"] = object()
    try:
        with pytest.raises(UnknownParserError) as exc:
            get_parser("barclays")
        assert "fakebank" in str(exc.value)
    finally:
        del PARSERS["fakebank"]


def test_registered_parser_is_returned():
    sentinel = object()
    PARSERS["fakebank"] = sentinel
    try:
        assert get_parser("fakebank") is sentinel
    finally:
        del PARSERS["fakebank"]


def test_every_configured_card_names_a_registered_parser():
    """A card naming a parser that does not exist fails on every statement it
    has. Each seed card must name one that does; the error for one that does
    not is covered above."""
    from pathlib import Path

    from creditcard.config import load_cards

    cards = load_cards(Path("config/cards.csv"))
    assert sorted(c.card_id for c in cards.values() if c.parser not in PARSERS) == []
