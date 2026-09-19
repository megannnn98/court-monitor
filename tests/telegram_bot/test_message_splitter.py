"""Splitting long answers: Telegram's limit, whole links, no broken entities."""

from __future__ import annotations

from telegram_bot.message_splitter import (
    DEFAULT_PART_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    TRUNCATION_NOTICE,
    _safe_cut,
    split_blocks,
)

LINK = '<a href="https://news.example/1">Заголовок</a>'


def test_short_blocks_stay_in_one_message() -> None:
    assert split_blocks(["первый", "второй"]) == ["первый\n\nвторой"]


def test_blocks_are_split_at_block_boundaries() -> None:
    blocks = ["x" * 2000, "y" * 2000, "z" * 2000]

    parts = split_blocks(blocks)

    assert len(parts) == 3
    assert parts[0].startswith("x") and parts[1].startswith("y")
    assert all(len(part) <= DEFAULT_PART_LIMIT for part in parts)


def test_every_part_fits_the_telegram_limit() -> None:
    parts = split_blocks([f"{index}. {LINK} {'т' * 300}" for index in range(200)])

    assert parts
    assert all(len(part) <= TELEGRAM_MESSAGE_LIMIT for part in parts)


def test_a_link_is_never_cut_in_half() -> None:
    parts = split_blocks([LINK] * 400)

    for part in parts:
        assert part.count("<a href=") == part.count("</a>")
        assert not part.rstrip().endswith("<a")


def test_an_html_entity_is_never_cut_in_half() -> None:
    parts = split_blocks(["&amp;" * 4000], limit=100, max_parts=1000)

    for part in parts:
        # Every "&" opened an entity that this part also closes.
        assert part.count("&") == part.count("&amp;")


def test_order_is_preserved() -> None:
    parts = split_blocks([f"{index}-{'ц' * 1000}" for index in range(10)])

    joined = "\n\n".join(parts)
    positions = [joined.index(f"{index}-") for index in range(10)]
    assert positions == sorted(positions)


def test_a_runaway_answer_is_truncated_instead_of_flooding() -> None:
    parts = split_blocks([f"{index} {'я' * 3000}" for index in range(500)], max_parts=3)

    assert len(parts) == 3
    assert parts[-1].endswith(TRUNCATION_NOTICE)
    assert all(len(part) <= DEFAULT_PART_LIMIT for part in parts)


def test_a_single_block_longer_than_a_message_is_cut_into_pieces() -> None:
    parts = split_blocks(["строка " * 2000])

    assert len(parts) > 1
    assert all(len(part) <= DEFAULT_PART_LIMIT for part in parts)


def test_unicode_is_counted_in_characters() -> None:
    block = "ё" * 3899

    assert split_blocks([block]) == [block]


def test_no_blocks_produce_no_messages() -> None:
    assert split_blocks([]) == []


def test_a_retreat_to_a_space_never_lands_inside_a_tag() -> None:
    # `<a href="…">` carries a space of its own: retreating to it would break the tag.
    text = '<a href="https://news.example/averyveryverylongpath">' + "x" * 40

    cut = _safe_cut(text, 60)

    assert text.rfind("<", 0, cut) <= text.rfind(">", 0, cut)
