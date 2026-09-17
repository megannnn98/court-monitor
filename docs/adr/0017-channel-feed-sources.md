# ADR 0017: Sources and a queue for the customer's channel

## Status

Accepted, 2026-09-17. Adds two sources and a read-only view; changes no candidate
definition (ADR 0006, ADR 0007).

## Context

The customer fills the Telegram channel @enbv2022 («Если б не было войны») with one
persecuted person a day. The channel is the output of this work, never an input. From
2026-08-01 it named eight people; the pipeline had found one of them as a candidate.

| Person | In our sources | Why not found |
|---|---|---|
| Светлана Савельева | 5 articles | found |
| Андрей Смирнов, Сергей Ярош, Дмитрий Коротков | none | old cases or never in the news we read |
| Мамут Белялов | Mediazona's Telegram, without a name | the name was only on kommersant.ru |
| Иван Любшин, Арсений Турбин, Евгений Поливко | yes | on the Rosfinmonitoring list; «Евгения Поливко» read as a woman |

Five of the eight have a card in the figurant registry of «Поддержка политзаключённых.
Мемориал» (memopzk.org): 7 138 cards, 426 created since 2026-08-01 — the volume the
customer expects (300+ a month). Ярош's card was created at 01:03, the channel posted him
at 09:00 the same day. Half of the channel's people are on the Rosfinmonitoring list.

## Decision

### The Memorial registry is a source (`memopzk-figurants`)

The registry is a WordPress REST collection. A listing page carries every card field we
need (title, creation date, taxonomy classes), so one request reads a hundred people and
the site's `Crawl-delay: 10` is kept; a full first load is 72 requests.

A card is written as a short article from its taxonomy — full name, region, articles
(`uk-205-2-ch-2` → «ч. 2 ст. 205.2 УК РФ»), a charge or a verdict by the stage, measure,
category, list — in the shapes the rule extractors know. The card's title is its person's
nominative full name: for this source extraction takes the title as the person and the
normalizer keeps it as written (`NAMED_TITLE_SOURCES`). Over all cards 7 125 of 7 138
give exactly that person as the case's target; the rest name nobody (initials only).

### Kommersant's site is a source (`kommersant`)

Its RSS feed carries section, title and lead; items outside the world, sports and
business sections that mention a court, a detention or a case are kept (about 25 a day)
and only those pages are fetched.

### The queue (`/ui/channel`)

Politically persecuted people the channel has not published yet, with a draft post each,
whatever their Rosfinmonitoring status (shown in the draft). The channel's public preview
is read at most once an hour, only to leave out its people; a name key ignores word order,
patronymic and initials.

### Names for unnamed news

An unnamed persecution event is matched to a named event of the same type within three
days by the term, age, article and place its sentence states (digests compare only the
event's own sentence). Measured on the working corpus: 29 suggestions, about three in
four right. They are shown next to both news items and never linked automatically.

## Verification

A full rebuild of a copy of the working database with both sources (2026-09-17): 7 of the
channel's 8 people since August 1 are in the queue; Мамут Белялов is a person from the
Kommersant article but classified uncertain (0.60). Persons 8 724 → 14 951, political
528 → 6 996; candidates (`not_matched`) 190 → 2 585, 75 → 180 in the default 45-day view;
the queue shows 521 people for the same period.

## Consequences

- Candidates grow by the registry's people; the default 45-day view shows the cards
  created in that period.
- A registry card changed after ingestion is not re-read (the external id is the card id).
- Extraction and normalization versions change (see the extraction rules found on the
  registry's names): the working database needs one rebuild.
- The unnamed-name suggestions are a person's decision, not the pipeline's.
