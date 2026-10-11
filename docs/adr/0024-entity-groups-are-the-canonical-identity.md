# ADR 0024: Entity groups are the canonical identity; ER v2 is not

## Status

Accepted, 2026-10-11. Measured in `docs/architecture/identity-resolution-comparison.md`;
supersedes the open question in "Post-extraction domain model decision" of
`docs/architecture/modularization-plan.md`.

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

**Measured.** 100 pairs where ER v2 and the rules disagree were labelled by hand
(`tests/fixtures/identity_comparison/`); 93 are a plain same or different person. The
operator cycle was replayed over the same mentions with the model answers production
had stored (`evaluation.identity_comparison.production`).

| System | Decides alone | Correct | Accuracy | False merges | False splits | Left to a human |
|---|---|---|---|---|---|---|
| ER v2 | 71 | 12 | 16.9 % | 3 | 56 | 22 |
| Rules alone | 93 | 81 | 87.1 % | 8 | 4 | 0 |
| Rules + model names | 92 | 83 | 90.2 % | 8 | 1 | 1 |
| Rules + model names + operator decisions (production) | 92 | 83 | 90.2 % | 8 | 1 | 1 |
| Production, normalizer 1.7.0 | 87 | 84 | 96.6 % | 2 | 1 | 6 |

These are rates on a sample chosen for disagreement, not on the corpus.

ER v2's 56 false splits are not a matching policy: 27 carry the flag
`bad_normalization`. The extraction normalizer leaves a surname its dictionary does not
know in an oblique case («Сергей Вихоря»), ER v2 compares the stored name, and stores a
second person. Grouping joins the same forms by the root of the surname. ER v2's 22
reviews were all the same person where the label says who it is (18 of them with the
reason `name_only_evidence`).

Six of the eight false merges of the rules came from one defect: a name of three words
was stored with its first word as both given name and surname, so a span that took a
neighbour's surname in was keyed by that surname. Fixed in normalizer 1.7.0.

## Decision

1. **Entity groups are the canonical identity** of the product. New work that needs
   "one human" reads `entity_groups`.
2. **ER v2 is not canonical and is not extended.** It stays as it is, reachable where
   it is reachable today, so that the monitoring run, its findings and the pipeline A
   endpoints keep working. It gets no module API and no link to entity groups. Removing
   it is a separate decision that needs the answer to who reads those endpoints.
3. **The model's role in identity is normalization**, as today. It is not made the
   resolver.
4. **No layer of persons under entity groups is built now.** The hypothesis of
   `Person` as identity under a case aggregate is not pursued: on the labelled sample
   the stable id did not come with better identity, and the layer would re-key every
   decision table for a gain nobody measured. It can be reopened if ER v2 is ever
   measured to join better than grouping once its input is normalized.

## Consequences

- One identity flow is the reference; the other is frozen, not deleted. The two still
  write separate tables and can still disagree; nothing reads both.
- **Namesakes are not solved.** A group's key is its name, so two people of one given
  name and surname, with no patronymic and no registry region, are one group. The two
  false merges that remain are exactly this (pairs #68 and #100 of the sample) and are
  kept as strict expected failures in `tests/evaluation/test_identity_production.py`.
  Telling them apart needs evidence the pipeline does not extract (a place, an age, a
  case) and a key that can carry it; both are future work, to be measured on this
  sample before they are accepted.
- **Three-word names are kept apart more.** With its parts in place, «Фамилия Имя
  Отчество» of a registry no longer falls into the group of «Имя Фамилия» by accident;
  outside one article the two stay apart and are offered to the operator as a pair of
  «Спорные случаи». On the sample that is three more pairs left to a human, for six
  false merges fewer.
- **Stored data.** Mentions extracted before normalizer 1.7.0 keep the old name parts
  (`entity_mentions.normalized_data`: `first_name`, `last_name`, `patronymic`); 419 of
  21 778 mentions of the evaluation database differ. Nothing rewrites them in place.
  The version bump makes every stored article pending for extraction again
  (`articles_pending_extraction` compares the normalizer version), so the next load
  step of each source extracts it anew and the next `collect-entities` reads the new
  parts. That first load is long. `full_name` and `matching_key` do not change, so
  persons and ER v2 decisions are not affected.
- `monitoring/junk_purge.py` still deletes the persons that only purged articles named.
  That is consistent with deleting the articles, is covered by
  `tests/monitoring/test_junk_purge.py` (a person named elsewhere stays), and is left
  as it is.
- Extraction still glues an initial to another person's surname («у знакомого А.
  Грейси похитил»). Grouping makes no new person of such a span; the span itself is
  extraction's to fix.
