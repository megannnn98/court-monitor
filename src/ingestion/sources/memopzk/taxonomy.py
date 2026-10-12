"""Reading a figurant card's taxonomy slugs back into Russian.

The REST collection lists a card's terms only as CSS classes built from transliterated
slugs (`uk-205-2-ch-2`, `regions-respublika-saha-yakutiya`), and the term names are not
exposed. Articles decode by their grammar; regions by transliterating the known names the
same way the site does; the small closed vocabularies are listed as they are.
"""

from __future__ import annotations

import re

# The site's transliteration (GOST 7.79 system B, as WordPress «Cyr-To-Lat» applies it).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "cz",
    "ч": "ch", "ш": "sh", "щ": "shh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}  # fmt: skip

_REGIONS = (
    "Республика Адыгея", "Республика Алтай", "Республика Башкортостан", "Республика Бурятия",
    "Республика Дагестан", "Республика Ингушетия", "Кабардино-Балкарская Республика",
    "Республика Калмыкия", "Карачаево-Черкесская Республика", "Республика Карелия",
    "Республика Коми", "Республика Крым", "Республика Марий Эл", "Республика Мордовия",
    "Республика Саха (Якутия)", "Республика Северная Осетия — Алания",
    "Республика Татарстан", "Республика Тыва", "Удмуртская Республика",
    "Республика Хакасия", "Чеченская Республика", "Чувашская Республика",
    "Алтайский край", "Забайкальский край", "Камчатский край", "Краснодарский край",
    "Красноярский край", "Пермский край", "Приморский край", "Ставропольский край",
    "Хабаровский край", "Амурская область", "Архангельская область",
    "Астраханская область", "Белгородская область", "Брянская область",
    "Владимирская область", "Волгоградская область", "Вологодская область",
    "Воронежская область", "Ивановская область", "Иркутская область",
    "Калининградская область", "Калужская область", "Кемеровская область — Кузбасс",
    "Кировская область", "Костромская область", "Курганская область", "Курская область",
    "Ленинградская область", "Липецкая область", "Магаданская область",
    "Московская область", "Мурманская область", "Нижегородская область",
    "Новгородская область", "Новосибирская область", "Омская область",
    "Оренбургская область", "Орловская область", "Пензенская область",
    "Псковская область", "Ростовская область", "Рязанская область", "Самарская область",
    "Саратовская область", "Сахалинская область", "Свердловская область",
    "Смоленская область", "Тамбовская область", "Тверская область", "Томская область",
    "Тульская область", "Тюменская область", "Ульяновская область",
    "Челябинская область", "Ярославская область", "Москва", "Санкт-Петербург",
    "Севастополь", "Еврейская автономная область", "Ненецкий автономный округ",
    "Ханты-Мансийский автономный округ — Югра", "Чукотский автономный округ",
    "Ямало-Ненецкий автономный округ", "Донецкая Народная Республика",
    "Луганская Народная Республика", "Запорожская область", "Херсонская область",
)  # fmt: skip

# Short or legacy region slugs the site uses beside the full names.
_REGION_ALIASES = {
    "krym": "Республика Крым",
    "doneczkaya-oblast": "Донецкая область",
    "luganskaya-oblast": "Луганская область",
    "zaporozhskaya-oblast": "Запорожская область",
    "hersonskaya-oblast": "Херсонская область",
    "dagestan": "Республика Дагестан",
    "chechnya": "Чеченская Республика",
    "adygeya": "Республика Адыгея",
    "kareliya": "Республика Карелия",
    "respublika-hakasiya-2": "Республика Хакасия",
    "chuvashskaya-respublika-chuvashiya": "Чувашская Республика",
    "chuvashiya": "Чувашская Республика",
    "chukotskij-ao": "Чукотский автономный округ",
    "severnaya-osetiya-alaniya": "Республика Северная Осетия — Алания",
    "tuva-tyva": "Республика Тыва",
    "ukraina": "Украина",
    "sumskaya-oblast": "Сумская область",
    "kievskaya-oblast": "Киевская область",
    "harkovskaya-oblast": "Харьковская область",
    "dnepropetrovskaya-oblast": "Днепропетровская область",
    "belarus": "Беларусь",
}
STAGES = {
    "predvaritelnoe-sledstvie": "следствие",
    "sud-pervoj-instanczii": "суд первой инстанции",
    "apellyacziya": "апелляция",
    "reshenie-suda-vstupilo-v-silu": "приговор вступил в силу",
    "ispolnenie-nakazaniya": "отбывает наказание",
    "otbyl-nakazanie": "отбыл наказание",
    "uslovnoye-nakazanie": "условное наказание",
    "smert": "умер",
}
# The stages after a verdict: the person is sentenced, not only charged.
SENTENCED_STAGES = frozenset(
    {
        "apellyacziya",
        "reshenie-suda-vstupilo-v-silu",
        "ispolnenie-nakazaniya",
        "otbyl-nakazanie",
        "uslovnoye-nakazanie",
    }
)
REPRESSIONS = {
    "zaklyuchenie-pod-strazhu": "заключение под стражу",
    "zaochnoe-zaklyuchenie-pod-strazhu": "заочное заключение под стражу",
    "lishenie-svobody": "лишение свободы",
    "pozhiznennoe-lishenie-svobody": "пожизненное лишение свободы",
    "zaochnoe-lishenie-svobody": "заочное лишение свободы",
    "vneproczessualnoe-lishenie-svobody": "внепроцессуальное лишение свободы",
    "domashnij-arest": "домашний арест",
    "uslovnoe-nakazanie": "условное наказание",
    "shtraf": "штраф",
    "podpiska-o-nevyezde": "подписка о невыезде",
    "v-rozyske": "в розыске",
    "ogranichenie-svobody": "ограничение свободы",
    "zapret-opredelennyh-dejstvij": "запрет определённых действий",
    "prinuditelnye-raboty": "принудительные работы",
    "prinud-lechenie-staczionar": "принудительное лечение в стационаре",
    "prinuditelnye-mery-mediczinskogo-haraktera": "принудительные меры медицинского характера",
    "net-presledovaniya": "преследование прекращено",
}
LISTS = {
    "spisok-politzaklyuchyonnyh-bez-presleduemyh-za-religiyu": (
        "Список политзаключённых (без преследуемых за религию)"
    ),
    "spisok-politzaklyuchyonnyh-presleduemyh-za-religiyu": (
        "Список политзаключённых, преследуемых за религию"
    ),
    "veroyatnye-zhertvy-ne-voshedshie-v-spiski": "Другие жертвы политических репрессий",
    "antivoennoe-delo": "Антивоенное дело",
    "aktualnyj-spisok-presleduemyh-bez-lisheniya-svobody": (
        "Список преследуемых без лишения свободы"
    ),
    "svideteli-iegovy": "Свидетели Иеговы",
    "spisok-presleduemyh-chlenov-hizb-ut-tahrir": "Преследуемые члены «Хизб ут-Тахрир»",
    "list-of-deceased-victims-of-political-persecution": (
        "Погибшие жертвы политического преследования"
    ),
    "v-arhive": "Архив",
}
# The case categories that say what the case is about, in the site's words.
CATEGORIES = {
    "vojna-v-ukraine-posle-2022": "война в Украине",
    "svideteli-iegovy": "Свидетели Иеговы",
    "terrorizm": "терроризм",
    "voennoplennye": "военнопленные",
    "gosizmena": "госизмена",
    "hizb-ut-tahrir": "«Хизб ут-Тахрир»",
    "ukrainskie-voennye": "украинские военные",
    "antivoennye-vyskazyvaniya": "антивоенные высказывания",
    "ataka-na-zh-d": "атака на железную дорогу",
    "presledovanie-po-religioznomu-priznaku": "преследование по религиозному признаку",
    "ukrainskij-sled": "«украинский след»",
    "vyskazyvaniya": "высказывания",
    "vyskazyvaniya-pro-vlast": "высказывания про власть",
    "dela-protiv-storonnikov-navalnogo": "дела против сторонников Навального",
    "diversiya": "диверсия",
    "shpionazh": "шпионаж",
    "svoboda-sobranij-mitingi": "свобода собраний, митинги",
    "legion-svoboda-rossii": "легион «Свобода России»",
    "konfidenczialnoe-sotrudnichestvo-s-inostranczami": (
        "конфиденциальное сотрудничество с иностранцами"
    ),
    "artpodgotovka": "«Артподготовка»",
    "finansirovanie-vsu": "финансирование ВСУ",
    "antivoennye-dejstviya": "антивоенные действия",
    "dela-grazhdanskih-aktivistov": "дела гражданских активистов",
    "rdk": "РДК",
}
FEMALE = "sex-zhenskij"

_CODES = {"uk": "УК РФ", "koap": "КоАП РФ"}
_ARTICLE_SLUG = re.compile(r"^(uk|koap)-(.+)$")


def slugify(name: str) -> str:
    lowered = "".join(_TRANSLIT.get(char, char) for char in name.lower())
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


REGIONS = {slugify(name): name for name in _REGIONS} | _REGION_ALIASES


def terms(class_list: list[str], prefix: str) -> list[str]:
    """The slugs of one taxonomy among a card's classes, without the prefix."""
    return [item.removeprefix(prefix) for item in class_list if item.startswith(prefix)]


def legal_references(slug: str) -> list[str]:
    """`uk-205-1-ch-1-1` → «ч. 1.1 ст. 205.1 УК РФ»; `uk-275-cherez-ch-3-st-30` names two.

    An unreadable slug gives nothing rather than a guessed article.
    """
    match = _ARTICLE_SLUG.match(slug)
    if match is None:
        return []
    code = _CODES[match.group(1)]
    # «часть неизвестна», «pt-2», an English copy («-en»): the same article, spelt otherwise.
    body = re.sub(r"-(?:chast-neizvestna|en)$", "", match.group(2)).replace("-pt-", "-ch-")
    main, _, attempt = body.partition("-cherez-")
    references = [_reference(main, code)]
    if attempt:
        # «через ч. 3 ст. 30»: preparation or attempt, cited as its own article.
        references.append(_reference(re.sub(r"^ch-(\d+)-st-(\d+)$", r"\2-ch-\1", attempt), code))
    return [reference for reference in references if reference is not None]


def _reference(body: str, code: str) -> str | None:
    match = re.fullmatch(r"(\d+(?:-\d+)*)(?:-ch-(\d+(?:-\d+)?))?(?:-p-([a-z]+))?", body)
    if match is None:
        return None
    article = match.group(1).replace("-", ".")
    part = f"ч. {match.group(2).replace('-', '.')} " if match.group(2) else ""
    clause = f"п. «{''.join(_letter(c) for c in match.group(3))}» " if match.group(3) else ""
    return f"{clause}{part}ст. {article} {code}"


def _letter(latin: str) -> str:
    reverse = {value: key for key, value in _TRANSLIT.items() if len(value) == 1}
    return reverse.get(latin, latin)
