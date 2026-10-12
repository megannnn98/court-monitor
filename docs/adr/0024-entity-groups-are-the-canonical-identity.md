# ADR 0024: Entity groups are the identity the product works on; ER v2 is not extended

## Status

Accepted, 2026-10-11, as an operational choice. Measured in
`docs/architecture/identity-resolution-comparison.md`. It closes the open question in
"Post-extraction domain model decision" of `docs/architecture/modularization-plan.md`
for now; "What this does not show" below says what would reopen it.

## Context

The code holds two answers to "which mentions are one human":

- **ER v2** (`persons/resolution`, ADR 0012): a `persons` row with a stable id, one
  mention at a time, AUTO_LINK / REVIEW / CREATE_NEW.
- **Entity groups** (`entities/collector.py`, `entities/grouping.py`): every person
  mention of the articles with a criminal-case event grouped at once by rules, each
  group named by a model, then a person's decisions applied; identified by a key made
  of the name and rebuilt whole.

What runs was established from the entry points and from a snapshot of the production
database taken on 2026-09-29:

| Entry point | Identity it runs |
|---|---|
| Operator console, «Рабочий цикл» (`operator_console.py`, `web/ui/pipeline.py`): `monitor --catch-up --load-only`, `purge-junk`, `collect-entities`, `find-figurants`, `find-political` | entity groups; person resolution has no step |
| Dagster assets (`monitoring/dagster/assets.py`), `monitor` without `--load-only`, `monitor-resolve`, `resolve-people`, the console operation `resolve-people` | ER v2 |
| Web console pages, exports, «Спросить», the dossier | entity groups |
| `/api/v1/persons`, `/api/v1/candidates`, `/api/v1/monitoring/findings` and their pages | persons |

The snapshot holds 684 entity groups (667 named by the model), 37 411 cached name
answers and 1 177 pair decisions, against 18 persons and 78 ER v2 decisions for 7 985
person mentions. The Dagster schedules start stopped and are turned on by hand.

```text
ER v2 runtime status: active_optional
ER v2 exists and is reachable from production entry points, but it is not part of the
operator cycle, and the production snapshot shows it was all but never run.
```

**What the model does.** DeepSeek (`deepseek/deepseek-v4.1-flash` through OpenRouter,
`entities/llm.py`) is asked by `entities/normalizer.py` for one thing about a group
the rules already made: its name in the nominative, its gender, and whether it is a
person at all. That is normalization. It decides no identity directly; but the name it
gives becomes the group's key, and groups it names alike are merged
(`grouping.apply_names`), so its answers do move mentions between groups. Entity
resolution itself is the rules of `entities.grouping` (surname roots, patronymics, a
registry card's region, a bare surname inside its article), the operator's pair
decisions (`entities.disputes`), and the automatic merge of pairs the Rosfinmonitoring
list confirms (`merge_clear_pairs`). The same model also reads roles, politics, news,
sentences, unnamed figurants and junk holds in later steps; none of that is identity.

**Measured.** 100 pairs where the stored ER v2 decisions and the rules disagree were
labelled by hand (`tests/fixtures/identity_comparison/`); 93 are a plain same or
different person. The grouping steps of the operator cycle were replayed over the same
mentions with the model answers production had stored
(`evaluation.identity_comparison.production`).

| System | Decides alone | Correct | Accuracy | False merges | False splits | Joined in part | Offered to a human | No entity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ER v2, as stored | 71 | 12 | 16.9 % | 3 | 56 | 0 | 22 | 0 |
| Rules alone | 93 | 81 | 87.1 % | 8 | 4 | 0 | 0 | 0 |
| Rules + model names | 92 | 83 | 90.2 % | 8 | 1 | 0 | 0 | 1 |
| Rules + model names + operator decisions | 92 | 83 | 90.2 % | 8 | 1 | 0 | 0 | 1 |
| The same, name parts of normalizer 1.7.0 | 87 | 81 | 93.1 % | 2 | 1 | 3 | 3 | 3 |

How to read it:

- "Decides alone" leaves out pairs offered to a human and pairs of which no entity was
  made. Accuracy is over what is decided alone, so it is a selective accuracy and has
  to be read with the three columns on the right.
- "Joined in part": the sides share a group and also hold others. Of one person that is
  counted wrong, not correct.
- "Offered to a human" for the groups means the pair is kept apart and shown in
  «Спорные случаи»; until someone decides, what is stored is a split.
- "No entity": the pipeline made nobody of a side (the model said «not a person», or a
  bare surname fits two people of its article). Nobody is asked.

**The defect behind six false merges.** A name of three words was stored with its first
word as both given name and surname, so a span that took a neighbour's surname in
(«адвоката Ивановой Петра Сидорова») was keyed by that surname. For the six pairs the
rules alone go from one group to two when only the name parts change, with no model
and no operator decision involved. Fixed in normalizer 1.7.0. The price: with their
parts in place, a name with a patronymic and the same name without one are apart
outside one article, which the old parts hid; on the sample that is three pairs joined
only in part, three offered to a human, and two fewer correct.

## Decision

1. **The product's identity is the entity group.** It is what the operator works on
   and what every downstream step reads; new work that needs "one human" reads
   `entity_groups`.
2. **ER v2 is not extended.** It stays as it is, reachable where it is reachable today,
   so that the monitoring run, its findings and the pipeline A endpoints keep working.
   It gets no module API and no link to entity groups. Removing it is a separate
   decision that needs the answer to who reads those endpoints.
3. **The model's role in identity stays normalization.** It is not made the resolver.
4. **No layer of persons under entity groups is built now.**

## What this does not show

- **Not corpus accuracy.** The sample is pairs on which the two disagreed. It says
  nothing of pairs they agree on, nor of errors a change may bring there.
- **Not a comparison of two algorithms on equal input.** ER v2 is scored as stored, on
  names the old normalizer left declined; the groups are scored with the model's names
  and a person's decisions. 27 of ER v2's 56 false splits carry the flag
  `bad_normalization`: that points at its input, it does not prove the cause of each.
  ER v2 on normalized input was not measured.
- **Not that a stable id is unneeded.** Matching quality and the stability of an
  identifier are different properties. Whether a group's key and the operator's
  decisions survive renames and rebuilds was not measured here.
- **Not the whole cycle.** The replay leaves out the unnamed figurants a person
  identified and a new pass of `merge_clear_pairs` after the rebuild, which may merge
  pairs the table counts as offered. The snapshot holds no removals.
- **"After" is a re-reading of stored names** with the current `name_parts`, not a new
  extraction of the articles.

Any of these, measured, may reopen points 2 and 4.

## Consequences

- One identity flow is the reference; the other is frozen, not deleted. The two still
  write separate tables and can still disagree; nothing reads both.
- **Namesakes are not solved.** A group's key is its name, so two people of one given
  name and surname, with no patronymic and no registry region, are one group. The two
  false merges that remain are exactly this (pairs #68 and #100 of the sample). They
  are strict expected failures in `tests/evaluation/test_identity_production.py`: the
  suite is green because the defect is expected, not because it is gone. Telling them
  apart needs evidence the pipeline does not extract (a place, an age, a case) and a
  key that can carry it.
- **Name parts have limits the words alone do not lift** (`name_parts`): two given
  names before a surname in «-вич» read as «Фамилия Имя Отчество»; two given names in
  a row keep their order. Both are pinned by tests. No word of a name is dropped (a
  word that is neither given name nor surname is stored in the third field), except
  the first word of a span that shows it took a neighbour's surname in; so these
  limits can keep one person apart from their own shorter name, not merge two people.
- **Normalizer 1.7.0 was changed three times on this branch** before any push. No
  database outside the disposable test one was extracted with an earlier form of it,
  so the version was not raised again. If this branch is ever deployed from an
  intermediate commit, raise the version before the next change of `name_parts`.
- **Stored data and re-extraction.** Mentions extracted before normalizer 1.7.0 keep
  the old name parts (`entity_mentions.normalized_data`); 399 of 21 778 mentions of the
  evaluation database differ. Nothing rewrites them in place. The version bump makes a
  source's stored articles pending for extraction again, and extraction writes a new
  run with **new mention rows that have no person**. So:
  - the operator cycle's load step (`monitor --catch-up --load-only`) re-extracts and
    stops there; the next `collect-entities` reads the new parts;
  - a full monitoring run (Dagster, or `monitor` without `--load-only`) goes on to
    resolve the new mentions with ER v2: new decisions and reviews, and possibly new
    persons and aliases. `full_name` and `matching_key` being unchanged does not prevent
    that. Where pipeline A's data matters, re-extract with `--load-only` first and
    decide on resolution separately.
- `monitoring/junk_purge.py` still deletes the persons that only purged articles named.
  That is consistent with deleting the articles, is covered by
  `tests/monitoring/test_junk_purge.py` (a person named elsewhere stays), and is left
  as it is.
- Extraction still glues an initial to another person's surname («у знакомого А.
  Грейси похитил»). Grouping makes no new person of such a span; the span itself is
  extraction's to fix.
