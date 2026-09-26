"""Person mentions gathered into entities: one name, however the news declined it.

Pure logic, no database. The extraction normalizer already puts names it knows into the
nominative; a surname its dictionary does not know stays declined («Моора», «Моору»).
So two surnames are one when one is the other plus an oblique case ending, and one
entity is one given name with one surname — and one patronymic when the text gave one.

A patronymic keeps namesakes apart: «Николай Викторович Бондаренко» of a registry card
is not the «Николай Бондаренко» of the news about a deputy. A name without a patronymic
joins the patronymic one only in the same article, where it is the same person named
again; elsewhere it is its own entity. The price: one person written both ways in
different articles is two entities. A mention of a surname alone, or with initials,
joins an entity only through a full name of that surname in the same article.
Namesakes without a patronymic still become one entity: a known simplification.

A registry card's region parts namesakes further: two cards of one «Денис Владимирович
Попов», of Курская and Краснодарский край, are two people. Their keys carry the region
after `REGION_SEP`. A full name without a region (the news) joins a card's entity only
when that name has cards of one region.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from extraction.name_frequency import lookup_gender

# Oblique endings of a surname the dictionary left declined: «Моор» → «Моора», «Моору»,
# «Моором», «Мооре»; «Давидис» → «Давидиса»; «Курылев» → «Курылева».
CASE_ENDINGS = ("а", "я", "у", "ю", "е", "ом", "ем", "ым", "им", "ой", "ей", "ы", "и")
# A surname that is an adjective declines as one, soft or hard: «Заболотний» →
# «Заболотнего», «Заболотнему» (the stem «заболотнь»); «Заболотный» → «Заболотного»,
# «Заболотная» → «Заболотную» (the stem «заболотн»). Two surnames, never one.
SOFT_ADJECTIVE_ENDINGS = ("ий", "его", "ему", "яя", "юю")
HARD_ADJECTIVE_ENDINGS = ("ый", "ого", "ому", "ая", "ую")
# The genitive plural of a family: «Невзоровых», «Ильиных».
_PLURAL_OF = ("овых", "евых", "иных", "ыных")
# The nominative among them, and the soft sign's. Not «-ой»: «Ивановой» is Иванова
# declined far more often than a «Толстой».
_NOMINATIVE_TAILS = ("ь", "ий", "ый", "ая", "яя")
_VOWELS = frozenset("аеёиоуыэюя")
# A shorter stem is no surname: «Ли» must not become the base of «Лиа» and «Лию».
MIN_STEM = 3
# Between a key's name and the region of the registry card that parts namesakes.
REGION_SEP = " · "


@dataclass(frozen=True)
class PersonMention:
    mention_id: int
    article_id: int
    first_name: str | None
    last_name: str | None
    patronymic: str | None = None
    # The region a registry card gives for its person («Регион: Луганская область»).
    region: str | None = None
    # The name as the text wrote it («Евгении Беркович»): what a model reads. The
    # normalizer's fields may be wrong («Евгении» → «Евгений»).
    surface: str | None = None


@dataclass
class Entity:
    key: str
    name: str
    mention_ids: list[int] = field(default_factory=list)
    variants: Counter[str] = field(default_factory=Counter)
    gender: str | None = None
    # "rules" (this module) or "model" (a name a model gave, `apply_names`).
    name_source: str = "rules"
    # The folded patronymic that keeps this entity apart from its namesakes; None when
    # the text named the person without one.
    patronymic: str | None = None
    # Region → publications naming it (registry cards only).
    regions: Counter[str] = field(default_factory=Counter)
    # The card's region in the key, when it parts this entity from namesakes.
    region_tag: str | None = None


@dataclass(frozen=True)
class GivenName:
    """A reading of an entity — a model's, or `nominative_form`'s: its nominative name,
    gender, and whether a person."""

    nominative: str
    gender: str
    is_person: bool
    source: str = "model"


def _fold(word: str) -> str:
    return word.strip().lower().replace("ё", "е")


def _is_initial(first_name: str | None) -> bool:
    return first_name is None or len(first_name.rstrip(".")) <= 1


def _bases(surname: str) -> set[str]:
    """The surname and what it would be without each oblique ending it may carry.

    A soft sign goes as an ending does: «Дудь» declines «Дудя», «Дудю», «Дудем», all on
    the stem «дуд»."""
    bases = {surname}
    for ending in (*CASE_ENDINGS, *HARD_ADJECTIVE_ENDINGS, "ь"):
        stem = surname[: -len(ending)]
        if surname.endswith(ending) and len(stem) >= MIN_STEM:
            bases.add(stem)
    for ending in SOFT_ADJECTIVE_ENDINGS:
        stem = surname[: -len(ending)]
        if surname.endswith(ending) and len(stem) >= MIN_STEM:
            bases.add(f"{stem}ь")
    # A family in the plural: «Александра и Лидии Невзоровых» (-ов, -ев, -ин surnames only:
    # «Черных» and «Седых» are no plural).
    if surname.endswith(_PLURAL_OF) and len(surname) - 2 >= MIN_STEM:
        bases.add(surname[:-2])
    return bases


def surname_roots(surnames: Iterable[str]) -> dict[str, str]:
    """Each folded surname → the key of its declension: surnames sharing a base are one.

    «Моор», «Моора», «Моору» share «моор»; so do «Моора» and «Моору» when the text never
    wrote «Моор». The key is the shortest base of the group."""
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            # The shorter (then alphabetically first) string represents the group.
            keep, drop = sorted((left, right), key=lambda item: (len(item), item))
            parent[drop] = keep

    for surname in set(surnames):
        parent.setdefault(surname, surname)
        for base in _bases(surname):
            parent.setdefault(base, base)
            union(surname, base)
    return {surname: find(surname) for surname in set(surnames)}


def split_key(key: str) -> tuple[str, str | None]:
    """(the name part, the region part or None) of an entity key."""
    name, sep, region = key.partition(REGION_SEP)
    return name, region if sep else None


def _patronymic_key(patronymic: str) -> str:
    """«Викторович», «Викторовича», «Викторовичем» → «викторович»: its shortest base."""
    return min(_bases(_fold(patronymic)), key=lambda base: (len(base), base))


def _cleaned(mention: PersonMention) -> PersonMention:
    """A «given name» that is the surname again («Горинов Горинов») is an extraction
    error: the mention is a bare surname."""
    first, last = mention.first_name, mention.last_name
    if first and last and _bases(_fold(first)) & _bases(_fold(last)):
        return replace(mention, first_name=None, patronymic=None)
    return mention


def group_mentions(mentions: Sequence[PersonMention]) -> list[Entity]:
    """Entities, most mentioned first."""
    mentions = [_cleaned(mention) for mention in mentions]
    full = [m for m in mentions if m.last_name and not _is_initial(m.first_name)]
    full_ids = {mention.mention_id for mention in full}
    # Declension is told apart within one given name only: «Александр Моора» joins
    # «Александр Моор», and no given name is ever read as another.
    by_first: dict[str, set[str]] = defaultdict(set)
    for mention in full:
        by_first[_fold(mention.first_name or "")].add(_fold(mention.last_name or ""))
    roots = {first: surname_roots(surnames) for first, surnames in by_first.items()}

    entities: dict[str, Entity] = {}
    surname_forms: dict[str, Counter[str]] = defaultdict(Counter)
    first_forms: dict[str, Counter[str]] = defaultdict(Counter)
    patronymic_forms: dict[str, Counter[str]] = defaultdict(Counter)
    # Per article, the entities a bare surname there may refer to.
    in_article: dict[tuple[int, str], set[str]] = defaultdict(set)
    # Per article, given name and surname, the patronymic entities it names.
    with_patronymic: dict[tuple[int, str, str], set[str]] = defaultdict(set)
    region_seen: set[tuple[str, int]] = set()
    # Per full name with a patronymic, the keys of its cards' regions.
    regional: dict[str, set[str]] = defaultdict(set)

    def add(
        key: str, mention: PersonMention, patronymic: str | None, region: str | None = None
    ) -> Entity:
        entity = entities.setdefault(
            key, Entity(key=key, name="", patronymic=patronymic, region_tag=region)
        )
        entity.mention_ids.append(mention.mention_id)
        entity.variants[_display(mention)] += 1
        if mention.region and (key, mention.article_id) not in region_seen:
            region_seen.add((key, mention.article_id))
            entity.regions[mention.region] += 1
        return entity

    def order(item: PersonMention) -> tuple[bool, bool]:
        """Cards with a patronymic and a region first, then other names with a patronymic:
        a name that lacks either then knows the entities it may join."""
        return not (item.patronymic or "").strip(), not item.region

    for mention in sorted(full, key=order):
        first = _fold(mention.first_name or "")
        surname = _fold(mention.last_name or "")
        root = roots[first][surname]
        written = (mention.patronymic or "").strip()
        region: str | None = None
        if written:
            patronymic: str | None = _patronymic_key(written)
            key = f"{first} {patronymic} {root}"
            if mention.region:
                region = mention.region.strip()
                key = f"{key}{REGION_SEP}{_fold(region)}"
                regional[f"{first} {patronymic} {root}"].add(key)
            else:
                cards = regional.get(key, set())
                if len(cards) == 1:
                    key = next(iter(cards))
            region = entities[key].region_tag if key in entities else region
            with_patronymic[(mention.article_id, first, root)].add(key)
            patronymic_forms[key][written] += 1
        else:
            patronymic = None
            named = with_patronymic.get((mention.article_id, first, root), set())
            key = next(iter(named)) if len(named) == 1 else f"{first} {root}"
            if key in entities:
                patronymic, region = entities[key].patronymic, entities[key].region_tag
        add(key, mention, patronymic, region)
        surname_forms[key][(mention.last_name or "").strip()] += 1
        first_forms[key][(mention.first_name or "").strip()] += 1
        for form in _bases(surname) | {root}:
            in_article[(mention.article_id, form)].add(key)

    for mention in mentions:
        if mention.mention_id in full_ids or not mention.last_name:
            continue
        candidates = set().union(
            *(
                in_article.get((mention.article_id, base), set())
                for base in _bases(_fold(mention.last_name))
            )
        )
        if mention.first_name:
            initial = _fold(mention.first_name)[:1]
            candidates = {key for key in candidates if key.startswith(initial)}
        # Two people of that surname in the article (father and son): nobody's mention.
        if len(candidates) == 1:
            key = next(iter(candidates))
            add(key, mention, entities[key].patronymic, entities[key].region_tag)
            # A bare surname is often the nominative the full names lacked («Моор»).
            surname_forms[key][mention.last_name.strip()] += 1

    for key, entity in entities.items():
        first = first_forms[key].most_common(1)[0][0]
        surname = _nominative_surname(first, key, surname_forms[key])
        patronymic_form = (
            f" {patronymic_forms[key].most_common(1)[0][0]}" if patronymic_forms[key] else ""
        )
        entity.name = f"{first}{patronymic_form} {surname}"
    return sorted(entities.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


def _nominative_surname(first: str, key: str, forms: Counter[str]) -> str:
    """The shortest surname form the news wrote, the most frequent of those.

    For a man whose surname only appeared declined («Александра Моора», «Моору») it is
    the stem the forms share: a masculine surname ending in a consonant takes the case
    endings («Моор»). A feminine or unknown given name keeps the written form («Мария
    Иванова» is already nominative), and so does a surname written one way only
    («Бабуа»)."""
    root = split_key(key)[0].rsplit(" ", 1)[1]
    # A surname in a soft sign or an adjective's ending, once written so, is its
    # nominative: «Дудь», not «Дудя»; «Заболотний», not «Заболотн».
    nominative = [
        form
        for form in forms
        if _fold(form).endswith(_NOMINATIVE_TAILS) and root in _bases(_fold(form))
    ]
    if nominative:
        return max(nominative, key=lambda form: forms[form])
    surname = min(forms.items(), key=lambda item: (len(item[0]), -item[1], item[0]))[0]
    declined = len({_fold(form) for form in forms}) > 1 and _fold(surname) != root
    if (
        declined
        and lookup_gender(first) == "masc"
        and _fold(surname).startswith(root)
        and root[-1] not in _VOWELS
    ):
        return surname[: len(root)]
    return surname


def _display(mention: PersonMention) -> str:
    """The form as written, else as the normalizer read it."""
    if mention.surface and mention.surface.strip():
        return " ".join(mention.surface.split())
    return " ".join(
        part.strip()
        for part in (mention.first_name, mention.patronymic, mention.last_name)
        if part and part.strip()
    )


_PATRONYMIC_ENDINGS = ("вич", "вна", "ична")


def given_name_first(name: str) -> str:
    """«Турбин Арсений» → «Арсений Турбин»: a model sometimes answers surname first.

    Only when the dictionary is sure: the last word (or the one before a patronymic) is a
    known given name and the first is not. A name it does not know stays as written."""
    words = name.split()
    if len(words) < 2 or any("." in word for word in words):
        return " ".join(words)
    has_patronymic = len(words) == 3 and words[2].lower().endswith(_PATRONYMIC_ENDINGS)
    given = words[1] if has_patronymic else words[-1]
    if lookup_gender(words[0]) is not None or lookup_gender(given) is None:
        return " ".join(words)
    if has_patronymic:
        return " ".join([words[1], words[2], words[0]])
    return " ".join([words[-1], *words[:-1]])


def name_key(name: str) -> str:
    """The key of a written name: its words folded, the patronymic kept — it tells
    namesakes apart, as the rules do."""
    return " ".join(_fold(word) for word in name.split())


def _without_patronymic(name: str) -> str:
    words = name.split()
    return " ".join([words[0], words[-1]]) if len(words) > 2 else name


def apply_names(entities: Sequence[Entity], names: Mapping[str, GivenName]) -> list[Entity]:
    """Rename the entities a model read, drop what is no person, merge the same names.

    Entities without an answer keep their rule name. Two entities a model named alike
    («Александра Моора» and «Александр Моор» both «Александр Моор») become one, under
    the key of that name; the most mentioned one lends its variants first."""
    merged: dict[str, Entity] = {}
    for entity in sorted(entities, key=lambda item: (-len(item.mention_ids), item.key)):
        given = names.get(entity.key)
        if given is not None and not given.is_person:
            continue
        # A card's region parts namesakes of one name: the new key keeps it.
        region = f"{REGION_SEP}{_fold(entity.region_tag)}" if entity.region_tag else ""
        if given is None or not given.nominative.strip():
            # The rules keep the order the text used; «Турбин Арсений» joins «Арсений Турбин».
            name = given_name_first(entity.name)
            key = entity.key if name == entity.name else name_key(name) + region
            gender, source = entity.gender, entity.name_source
        else:
            name = given_name_first(given.nominative)
            if entity.patronymic is None:
                # The text gave no patronymic: one from the model would join a namesake.
                name = _without_patronymic(name)
            elif len(name.split()) < 3 and not any("." in word for word in name.split()):
                # Nor may the model drop the one that keeps this person apart.
                words = name.split()
                middle = entity.name.split()[1:-1]
                name = " ".join([words[0], *middle, *words[1:]])
            key = name_key(name) + region
            gender = None if given.gender == "unknown" else given.gender
            source = given.source
        target = merged.get(key)
        if target is None:
            merged[key] = Entity(
                key=key,
                name=name,
                mention_ids=list(entity.mention_ids),
                variants=Counter(entity.variants),
                gender=gender,
                name_source=source,
                patronymic=entity.patronymic,
                regions=Counter(entity.regions),
                region_tag=entity.region_tag,
            )
            continue
        target.mention_ids += entity.mention_ids
        target.variants.update(entity.variants)
        target.regions.update(entity.regions)
    return sorted(merged.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


# «Жене Владимира» is «жена Владимира», no Женя: a kin word before a declined given name.
_KIN = frozenset(
    [
        "жена",
        "жены",
        "жене",
        "жену",
        "женой",
        "муж",
        "мужа",
        "мужу",
        "мужем",
        "вдова",
        "вдовы",
        "вдове",
        "вдову",
        "вдовой",
        "сын",
        "сына",
        "сыну",
        "сыном",
        "дочь",
        "дочери",
        "дочерью",
        "мать",
        "матери",
        "матерью",
        "мама",
        "мамы",
        "маме",
        "маму",
        "отец",
        "отца",
        "отцу",
        "отцом",
        "папа",
        "папы",
        "папе",
        "брат",
        "брата",
        "брату",
        "братом",
        "сестра",
        "сестры",
        "сестре",
        "сестру",
        "сестрой",
    ]
)


def _declined_given_name(word: str) -> bool:
    """«Владимира», «Наталье»: a known given name, declined."""
    stem = word[:-1]
    return any(
        base != word and lookup_gender(base) is not None
        for base in (stem, f"{stem}а", f"{stem}я", f"{stem}й", f"{stem}ь")
    )


def kinship(mention: PersonMention) -> bool:
    """A mention that names a relative, not a person: «Жене Владимира (Гульчака)»."""
    words = _display(mention).split()
    return len(words) >= 2 and _fold(words[0]) in _KIN and _declined_given_name(words[1])


def merge_swapped(entities: Sequence[Entity]) -> list[Entity]:
    """«Дрион Алексис» and «Алексис Дрион»: two words, the same, the order swapped — one
    person, when the dictionary knows neither word to tell the given name. The more
    mentioned one keeps its name."""
    merged: dict[str, Entity] = {}
    by_words: dict[tuple[frozenset[str], str], str] = {}
    for entity in sorted(entities, key=lambda item: (-len(item.mention_ids), item.key)):
        name_part, _, region = entity.key.partition(REGION_SEP)
        words = name_part.split()
        pair = len(words) == 2 and words[0] != words[1]
        target_key = by_words.get((frozenset(words), region)) if pair else None
        if target_key is None:
            merged[entity.key] = replace(
                entity,
                mention_ids=list(entity.mention_ids),
                variants=Counter(entity.variants),
                regions=Counter(entity.regions),
            )
            if pair:
                by_words[(frozenset(words), region)] = entity.key
            continue
        target = merged[target_key]
        target.mention_ids += entity.mention_ids
        target.variants.update(entity.variants)
        target.regions.update(entity.regions)
    return sorted(merged.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


def attach_bare(entities: Sequence[Entity], article_of: Mapping[int, int]) -> list[Entity]:
    """Entities named by a surname alone («Алексеев» of «Иноагент Алексеев», a title the
    extractor took for a given name) handed to the full name of that surname their
    article names, when it names one. What stays is a surname alone: nobody for certain.
    """
    full = [entity for entity in entities if len(entity.name.split()) >= 2]
    by_article: dict[tuple[int, str], set[str]] = defaultdict(set)
    for entity in full:
        surname = _fold(entity.name.split()[-1])
        for article in {article_of[m] for m in entity.mention_ids if m in article_of}:
            for base in _bases(surname):
                by_article[(article, base)].add(entity.key)
    merged = {
        entity.key: replace(entity, mention_ids=list(entity.mention_ids)) for entity in entities
    }
    for entity in entities:
        words = entity.name.split()
        if len(words) != 1:
            continue
        kept: list[int] = []
        for mention_id in entity.mention_ids:
            where = article_of.get(mention_id)
            candidates = (
                set().union(
                    *(by_article.get((where, base), set()) for base in _bases(_fold(words[0])))
                )
                if where is not None
                else set()
            )
            if len(candidates) == 1:
                merged[next(iter(candidates))].mention_ids.append(mention_id)
            else:
                kept.append(mention_id)
        if kept:
            merged[entity.key].mention_ids = kept
        else:
            del merged[entity.key]
    return sorted(merged.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


def attach_aliases(entities: Sequence[Entity], aliases: Iterable[tuple[int, int]]) -> list[Entity]:
    """A pseudonym written in brackets right after a name («блогера Дмитрия Пуркина (Дед
    Архимед)») is that person: its entity — every mention of it — joins the named one,
    whose name stays. `aliases` pairs the bracketed mention with the name's mention.
    """
    by_key = {
        entity.key: replace(
            entity, mention_ids=list(entity.mention_ids), variants=Counter(entity.variants)
        )
        for entity in entities
    }
    key_of = {m: entity.key for entity in entities for m in entity.mention_ids}
    for alias, named in aliases:
        source, target = key_of.get(alias), key_of.get(named)
        if source is None or target is None or source == target:
            continue
        moved = by_key.pop(source)
        by_key[target].mention_ids += moved.mention_ids
        by_key[target].variants.update(moved.variants)
        by_key[target].regions.update(moved.regions)
        for m in moved.mention_ids:
            key_of[m] = target
    return sorted(by_key.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


_PATRONYMIC_TAILS = ("вич", "вна", "ична")
_GENDERS = {"masc": "male", "femn": "female"}


def _ambiguous_given(given: str) -> bool:
    """«Александра», «Валентина», «Евгения»: a woman's name, and a man's in the genitive."""
    return lookup_gender(given) == "femn" and (
        lookup_gender(given[:-1]) == "masc" or lookup_gender(f"{given[:-1]}й") == "masc"
    )


def nominative_form(form: str) -> GivenName | None:
    """The name of a form already in the nominative, without asking a model; None when
    not sure.

    Sure: a given name the dictionary knows as such (it knows no oblique form of one) —
    so the phrase is in the nominative — first («Иван Петров», «Анна Олеговна Смирнова»)
    or after the surname before a patronymic, as registry cards write («Смирнова Анна
    Олеговна»). Not sure, left to the model: a given name that is also a man's genitive
    («Александра»), a plural («Валуевы»), a patronymic for a surname («Юрий Николаевич»),
    a man's surname in an oblique ending («Вячеслав Костина»). Measured: of 4 839 forms
    it named, 7 differed from the model's name, the rule right each time («Ким Игорь
    Васильевич» is Игорь Васильевич Ким)."""
    words = form.split()
    if len(words) not in (2, 3) or any("." in word for word in words):
        return None
    for given_at, surname_at in ((0, len(words) - 1), (1, 0)):
        if given_at == 1 and len(words) == 2:
            continue  # «Петров Иван»: which is which is the model's to say
        given, surname = words[given_at], words[surname_at]
        gender = lookup_gender(given)
        if gender is None or _ambiguous_given(given):
            continue
        rest = [word for index, word in enumerate(words) if index not in (given_at, surname_at)]
        if rest and not rest[0].lower().endswith(_PATRONYMIC_TAILS):
            continue
        folded = surname.lower()
        if folded.endswith((*_PATRONYMIC_TAILS, "ы", "ых", "их")):
            continue
        if gender == "masc" and folded.endswith(("а", "я", "у", "ю", "ом", "ым", "ой")):
            continue
        if gender == "femn" and folded.endswith(("ой", "ую", "у", "ю")):
            continue
        return GivenName(
            " ".join([given, *rest, surname]), _GENDERS.get(gender, "unknown"), True, "rules"
        )
    return None
