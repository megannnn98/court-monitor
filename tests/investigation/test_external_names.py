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
    assert names("Юрия Дудя внесли в реестр.", "Юрий Дудь")
    assert names("Ильи Яшина признали виновным.", "Илья Яшин")
    assert names("Олега Куваева признали виновным.", "Олег Куваев")


def test_the_name_written_the_other_way_round_is_the_name() -> None:
    assert names("Карлов Игорь отбывает наказание.", "Игорь Карлов")


def test_an_initial_stands_for_the_word_it_stands_for() -> None:
    assert names("И. Карлов осуждён судом.", "Игорь Карлов")


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


def test_a_patronymic_between_the_words_of_a_name_is_still_the_name() -> None:
    """A Russian name is written in three words and the middle one is the patronymic.
    Whether it stands between the two that were looked for or after them, the name is
    the name: the window of three tokens holds all three."""
    assert names("Мария Владимировна Бонцлер дала показания.", "Мария Бонцлер")
    assert names("Бонцлер Мария Владимировна дала показания.", "Мария Бонцлер")
    assert names("Марии Владимировны Бонцлер признали виновной.", "Мария Бонцлер")


def test_a_surname_the_dictionary_declines_by_the_wrong_rule_is_still_the_name() -> None:
    """«Дудя» comes back from pymorphy as the inflected form of the verb «дудеть» and is
    nowhere known as a name, so there is no dictionary form to compare. Two words the
    dictionary does not know as a person's name may still differ by an ending and nothing
    else. Eleven real Telegram quotes about Юрия Дудя stopped passing without this."""
    assert names("Юрия Дудя внесли в реестр «экстремистов и террористов»", "Юрий Дудь")
    assert names("Приставы начали взыскивать с блогера Юрия Дудя 200 тысяч рублей", "Юрий Дудь")


def test_two_names_that_only_look_alike_are_two_people() -> None:
    """A shared root is not a name. Each pair below shares letters, or is one letter off,
    or is a word the dictionary knows as a different name — and no two of them is one
    person."""
    assert not names("Игорь Карпов дал показания.", "Игорь Карлов")
    assert not names("Александр Смирнов дал показания.", "Алексей Смирнов")
    assert not names("Марина Окрушко дала показания.", "Мария Окрушко")
    assert not names("Олег Кузнецов дал показания.", "Олег Кузьмин")
    assert not names("Алена Кардашова дала показания.", "Алексей Кардашов")
    assert not names("Юрий Петровский дал показания.", "Юрий Петров")
    # A given name is not a patronymic, though the dictionary knows both.
    assert not names("Андреев Владимир дал показания.", "Андрей Владимиров")
    assert not names("Дмитрий Смирнов дал показания.", "Дмитрий Смирнягин")
    assert not names("Наталья Бонцлер дала показания.", "Мария Бонцлер")


def test_a_declined_surname_the_dictionary_reads_as_another_word_is_the_name() -> None:
    """«Халилова» comes back first as the possessive «халиловый»; the surname is the
    dictionary's second reading. A word the dictionary declined may be looked for by any
    of the forms it was declined into — but only if its own first reading is another word,
    or «Андреев», whose forms include «Андрей», would answer to «Андрей»."""
    assert names("Суд КБР рассматривает дело о банкротстве Андзора Халилова.", "Андзор Халилов")


def test_a_surname_with_a_second_dictionary_reading_is_the_name() -> None:
    """«Кынева» has a female-surname reading after its first dictionary reading."""
    assert names("Политолога Александра Кынева отправили в СИЗО.", "Александр Кынев")


def test_the_same_three_letters_of_two_names_that_are_two_people() -> None:
    assert not names("Президент Украины Владимир Зеленский посетил Чехию.", "Павел Зеленский")


def test_a_declined_place_is_the_place_looked_for() -> None:
    """A one-word name may be a place, and a place is declined by an adjective. The
    allowance is longer than a name's ending and is reached only where the dictionary
    knows neither word as a person's name, so it never makes «Петров» and «Петровский»
    one word."""
    assert names("Поиск по Тюменской области дал результат.", "Тюмень")
    assert names("Суд в Тюмени открылся.", "Тюмень")


def test_an_apposition_of_two_words_between_the_name_breaks_it() -> None:
    """The window ends at three tokens, and this is what that costs: «Мария, дочь
    Владимира Бонцлер» writes the name in four tokens with two words of apposition
    between. It does not pass. Measured on the hand-annotated set: nothing of it does."""
    assert not names("Мария, дочь Владимира Бонцлер, дала показания.", "Мария Бонцлер")


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
