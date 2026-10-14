"""One conservative spelling of Criminal Code charges: what a lead may point at and what
the extractor may afterwards confirm.

A charge is never read out of the bare number of an article. It has to be written as a
Criminal Code citation — «ч. 3 ст. 280 УК РФ» — and it has to be the *Russian* Criminal
Code: a case about Belarus, Ukraine, Kazakhstan or Armenia is a neighbouring country's
law, and calling it «ст. 342 УК РФ» would put a foreign article into a Russian dossier as
a fact. Every such citation is therefore refused rather than normalised.
"""

from __future__ import annotations

import re

# The country a Criminal Code belongs to, as it is written right after the code — and
# sometimes after the country the case itself is about («дело в Беларуси, ст. 342 УК РБ»).
# Nothing but spaces and an opening bracket may come between: a comma or a full stop ends
# the citation, so «ст. 280 УК РФ. В Беларуси…» stays Russian.
_FOREIGN_CODE = re.compile(
    r"^\s*(?:ук\s*)?\(?\s*(?:республик\w*\s*)?"
    r"(?:беларус\w*|украин\w*|казах\w*|армен\w*|армян\w*|грузин\w*|молдав\w*|латви\w*"
    r"|литв\w*|эстон\w*"
    r"|киргиз\w*|узбек\w*|таджик\w*|туркм\w*|азербайджан\w*|серби\w*|албан\w*|македон\w*"
    r"|рб|рк|кр)"
    r"\s*\)?",
    re.IGNORECASE,
)

CHARGE = re.compile(
    r"(?<![а-яёa-z])"
    r"(?:ч(?:асть|асти|\.)\s*(?P<part>\d+)(?:\s*(?:и|,)\s*ч(?:асть|асти|\.)\s*\d+)?\s*)?"
    r"ст(?:атья|атьи|атье|атью|атьей|атьёй|атьям|атьями|\.)\s*(?P<article>\d+(?:\.\d+)*)\s*"
    r"(?:ук(?:\s*рф)?|уголовного\s+кодекса(?:\s+(?:рф|российской\s+федерации))?)",
    re.IGNORECASE,
)


def russian_charge(match: re.Match[str], text: str) -> str | None:
    """The one spelling of this citation when it is a charge under the Russian Criminal
    Code, and None when it is somebody else's law.

    The country is written after the code («ст. 342 УК Беларуси»), never before it, so
    only what stands in the words that follow is read.
    """
    tail = text[match.end() : match.end() + 60].lstrip()
    if _FOREIGN_CODE.match(tail):
        return None
    part = match.group("part")
    prefix = f"ч. {part} " if part else ""
    return f"{prefix}ст. {match.group('article')} УК РФ"


def normalised_charges(text: str) -> set[str]:
    """Every charge of the Russian Criminal Code written in text, whoever it is about."""
    return {
        spelling
        for match in CHARGE.finditer(text)
        if (spelling := russian_charge(match, text)) is not None
    }


__all__ = ["CHARGE", "normalised_charges", "russian_charge"]
