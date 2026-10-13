"""Whose name a text holds.

The rule this replaces matched the first three letters of each word anywhere in the
text, so a text about «Андрей Карлов» and «Игорь Сечин» was about «Игорь Карлов»: it
holds both names, in one text, and they have nothing to do with each other. Now the
words of a name must stand together, in any order and any case, and an initial counts
for the word it stands for.
"""

from investigation.services.external import names


def test_a_text_about_two_other_people_is_not_about_this_one() -> None:
    text = "Андрей Карлов заявил, что Игорь Сечин на допрос не приходил."
    assert not names(text, "Игорь Карлов")


def test_two_names_touching_in_a_text_are_two_people() -> None:
    text = "Игорь Сечин заявил. Позже Андрей Карлов дал показания."
    assert not names(text, "Игорь Карлов")


def test_the_declined_case_of_the_name_is_the_name() -> None:
    assert names("Игоря Карлова признали виновным.", "Игорь Карлов")


def test_the_name_written_the_other_way_round_is_the_name() -> None:
    assert names("Карлов Игорь отбывает наказание.", "Игорь Карлов")


def test_an_initial_stands_for_the_word_it_stands_for() -> None:
    assert names("И. Карлов осуждён судом.", "Игорь Карлов")


def test_a_declined_place_is_the_place_looked_for() -> None:
    assert names("Суд в Тюмени открылся.", "Тюмень")


def test_the_name_written_with_a_space_between_is_the_name() -> None:
    assert names("Мария Бонцлер дала показания.", "Мария Бонцлер")
    assert names("Бонцлер Мария дала показания.", "Мария Бонцлер")
    assert names("Бонцлер М. дала показания.", "Мария Бонцлер")


def test_one_word_written_between_the_words_of_a_name_still_agrees() -> None:
    """A name may be written apart by an apposition — «Игорь, сын Карлова» — and still be
    the name. What it may not do is stand in two unrelated places, and that is where the
    window ends: three tokens, and «Андрей Карлов заявил, что Игорь Сечин» is two people."""
    assert names("Игорь, сын Карлова, отбывает наказание.", "Игорь Карлов")
    assert not names("Андрей Карлов заявил, что Игорь Сечин не приходил.", "Игорь Карлов")


def test_a_name_of_one_word_is_matched_without_proximity() -> None:
    """A surname alone, or the place an unnamed publication is looked up by: there is
    nothing to be near."""
    assert names("Дело в отношении Карлова передано в суд.", "Карлов")
    assert names("Поиск по Тюмени дал один результат.", "Тюмень")


def test_the_conjunction_is_not_an_initial() -> None:
    """An initial is written capital and with a period. «И» on its own is the commonest
    word in the language: written small, or capital without a period, it is not a name,
    and a text holding only that and a surname is not a text about this person."""
    assert not names("Дело И это разные люди", "Игорь Карлов")
    assert not names("Карлов И это разные люди", "Игорь Карлов")
    assert names("Карлов И. осуждён судом.", "Игорь Карлов")


def test_no_name_means_no_filter() -> None:
    assert names("Любой текст вообще", "")
