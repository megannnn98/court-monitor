"""Person mentions gathered into entities: one name, however the news declined it.

Pure logic, no database. The extraction normalizer already puts names it knows into the
nominative; a surname its dictionary does not know stays declined («Моора», «Моору»).
So two surnames are one when one is the other plus an oblique case ending, and one
entity is one given name with one surname (the patronymic does not split it). A mention
of a surname alone, or with initials, joins an entity only through a full name of that
surname in the same article. Namesakes with the same given name become one entity: a
known simplification, not an identity claim.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from extraction.name_frequency import lookup_gender

# Oblique endings of a surname the dictionary left declined: «Моор» → «Моора», «Моору»,
# «Моором», «Мооре»; «Давидис» → «Давидиса»; «Курылев» → «Курылева».
CASE_ENDINGS = ("а", "я", "у", "ю", "е", "ом", "ем", "ым", "им", "ой", "ей", "ы", "и")
_VOWELS = frozenset("аеёиоуыэюя")
# A shorter stem is no surname: «Ли» must not become the base of «Лиа» and «Лию».
MIN_STEM = 3


@dataclass(frozen=True)
class PersonMention:
    mention_id: int
    article_id: int
    first_name: str | None
    last_name: str | None
    patronymic: str | None = None


@dataclass
class Entity:
    key: str
    name: str
    mention_ids: list[int] = field(default_factory=list)
    variants: Counter[str] = field(default_factory=Counter)
    gender: str | None = None
    # "rules" (this module) or "model" (a name a model gave, `apply_names`).
    name_source: str = "rules"


@dataclass(frozen=True)
class GivenName:
    """A model's reading of an entity: its nominative name, gender, and whether a person."""

    nominative: str
    gender: str
    is_person: bool


def _fold(word: str) -> str:
    return word.strip().lower().replace("ё", "е")


def _is_initial(first_name: str | None) -> bool:
    return first_name is None or len(first_name.rstrip(".")) <= 1


def _bases(surname: str) -> set[str]:
    """The surname and what it would be without each oblique ending it may carry."""
    bases = {surname}
    for ending in CASE_ENDINGS:
        stem = surname[: -len(ending)]
        if surname.endswith(ending) and len(stem) >= MIN_STEM:
            bases.add(stem)
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


def _cleaned(mention: PersonMention) -> PersonMention:
    """A «given name» that is the surname again («Горинов Горинов») is an extraction
    error: the mention is a bare surname."""
    first, last = mention.first_name, mention.last_name
    if first and last and _bases(_fold(first)) & _bases(_fold(last)):
        return PersonMention(mention.mention_id, mention.article_id, None, last, None)
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
    # Per article, the entities a bare surname there may refer to.
    in_article: dict[tuple[int, str], set[str]] = defaultdict(set)

    for mention in full:
        first = _fold(mention.first_name or "")
        surname = _fold(mention.last_name or "")
        root = roots[first][surname]
        key = f"{first} {root}"
        entity = entities.setdefault(key, Entity(key=key, name=""))
        entity.mention_ids.append(mention.mention_id)
        entity.variants[_display(mention)] += 1
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
            entity = entities[key]
            entity.mention_ids.append(mention.mention_id)
            entity.variants[_display(mention)] += 1
            # A bare surname is often the nominative the full names lacked («Моор»).
            surname_forms[key][mention.last_name.strip()] += 1

    for key, entity in entities.items():
        first = first_forms[key].most_common(1)[0][0]
        entity.name = f"{first} {_nominative_surname(first, key, surname_forms[key])}"
    return sorted(entities.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))


def _nominative_surname(first: str, key: str, forms: Counter[str]) -> str:
    """The shortest surname form the news wrote, the most frequent of those.

    For a man whose surname only appeared declined («Александра Моора», «Моору») it is
    the stem the forms share: a masculine surname ending in a consonant takes the case
    endings («Моор»). A feminine or unknown given name keeps the written form («Мария
    Иванова» is already nominative), and so does a surname written one way only
    («Бабуа»)."""
    surname = min(forms.items(), key=lambda item: (len(item[0]), -item[1], item[0]))[0]
    root = key.split(" ", 1)[1]
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
    return " ".join(
        part.strip()
        for part in (mention.first_name, mention.patronymic, mention.last_name)
        if part and part.strip()
    )


def name_key(name: str) -> str:
    """The key of a written name: given name and surname folded, the patronymic dropped.

    «Роман Андреевич Попков» and «Роман Попков» are one person: the rules never split by
    patronymic either."""
    words = [_fold(word) for word in name.split()]
    return " ".join(words[:1] + words[-1:]) if len(words) > 2 else " ".join(words)


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
        if given is None or not given.nominative.strip():
            key, name, gender, source = entity.key, entity.name, entity.gender, entity.name_source
        else:
            key, name = name_key(given.nominative), given.nominative.strip()
            gender = None if given.gender == "unknown" else given.gender
            source = "model"
        target = merged.get(key)
        if target is None:
            merged[key] = Entity(
                key=key,
                name=name,
                mention_ids=list(entity.mention_ids),
                variants=Counter(entity.variants),
                gender=gender,
                name_source=source,
            )
            continue
        target.mention_ids += entity.mention_ids
        target.variants.update(entity.variants)
    return sorted(merged.values(), key=lambda entity: (-len(entity.mention_ids), entity.key))
