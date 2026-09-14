# ADR 0012: Entity Resolution v2

## Status

Accepted. Extends ADR 0005 (exact `matching_key` baseline stays the fast path)
and ADR 0004 (canonical Person model).

## Context

ADR 0005 resolves a person mention only when its normalized name produces the
identical `matching_key`. Reordered names ("Иванов Иван" / "Иван Иванов"),
initials, known aliases and small typos create duplicate canonical persons.
Semantic retrieval (ADR 0011) can find similar persons, but a similar person is
not the same person: a false link attaches someone's detentions and sentences
to another human being, which is far worse than an extra human review.

## Decision

A mention is resolved in separate, individually testable steps
(`src/persons/resolution/`):

```text
mention → PersonIdentityInput → PersonNameNormalizer
       → exact matching_key (fast path: AUTO_LINK, nothing else runs)
       → candidate generation (alias key, pg_trgm, optional semantic)
       → PersonResolutionFeatureExtractor → PersonResolutionScorer
       → PersonResolutionDecisionPolicy → AUTO_LINK | REVIEW | CREATE_NEW
```

Invariant: **ER v2 may link a new mention to an existing Person or create a
Person; it never merges two existing canonical Persons.** A merge only happens
on an explicit reviewer action (`MERGE_PERSONS`), audited by the existing
`PersonMergeRecord`.

### Exact fast path

`RuleBasedPersonResolver` and `uq_persons_matching_key_active` (with its
conflict/retry behaviour) are unchanged. An exact key hit is AUTO_LINK without
candidate generation; the existing alias behaviour of that path is kept (its
alias carries the same key and adds no new matching power).

### Normalization

`PersonNameNormalizer` works on the name as extraction stores it
(`normalized_data.full_name`): NFKC, case folding, `ё→е`, punctuation and dots
split words (`И.И.Иванов` → `и и иванов`), hyphenated surnames stay one token.
Roles are **not assigned from suffixes**. Every admissible order becomes a
`NameVariant`: one token — surname or given name; two — G S, S G, G P; three —
G P S, S G P; initials — `И. [И.] Фамилия`, `Фамилия И. [И.]`,
`Иван И. Иванов`, `Иванов Иван И.`. Patronymic (`-ич`, `-вна`, ...) and surname
(`-ов`, `-ин`, `-ский`, ...) suffixes are weak hints that only order variants:
"Шостакович" is a surname, "Джон Смит" has no suffix at all. Four or more
tokens are not parsed (compared as a whole only).

### Candidate generation (recall-oriented, bounded)

- `ExactKeyCandidateGenerator`: persons whose name or known alias has the
  incoming key (the fast path only checks `persons.matching_key`).
- `TrigramCandidateGenerator`: pg_trgm GIN indexes on `persons.normalized_name`
  and `person_aliases.normalized_text`; whole-name `%` or per-token `<%`
  (the surname block that catches reordering, initials, single-token typos).
- `SemanticCandidateGenerator` (opt-in, `ER_SEMANTIC_CANDIDATES=1`): dense Person
  neighbours of the name with its own `ER_SEMANTIC_CANDIDATE_MIN_SCORE`. The
  research relevance threshold and `DenseSimilarityRelevancePolicy` are not
  used: research relevance and identity are different questions.

`CompositeCandidateGenerator` merges by person, orders deterministically and
keeps `ER_CANDIDATE_LIMIT` (default 30). Nothing scans the Person table in
Python. An unavailable semantic source is reported (`semantic_source =
unavailable`) and lexical generation continues.

### Features and conflicts

For the best-aligned pair of variants (incoming × candidate name or alias):
per-component match (`exact`, `typo` — Levenshtein within 1 edit when the longer form has 5+
letters, 2 for 10+, via RapidFuzz; `initial_compatible`; `missing`;
`mismatch`), similarities, `order_differs` over shared roles, `exact_name`,
`exact_alias`, `exact_matching_key`, `initials_only`, `incomplete_name`,
`semantic_similarity` (diagnostic) and explicit conflicts
(`surname_mismatch`, `given_name_mismatch`, `patronymic_mismatch`). The
alignment ranks by matches and reading plausibility, not by "fewest conflicts":
an implausible reading must not hide a conflict. There is no birth date
extraction, so no birth date feature.

RapidFuzz (MIT, C extension, no transitive dependencies) replaces hand-written
edit distance; `difflib` is not a Levenshtein distance and misjudges short names.

### Scoring

`resolution_score` is a rule-based ordinal score, **not a probability**:
surname exact 0.45 / typo 0.30; given name exact 0.30 / typo 0.20 / initial
0.08; patronymic exact 0.20 / typo 0.12 / initial 0.05 / missing 0.05; exact
form +0.05; different order −0.05. Any conflict caps the score at 0.25 instead
of averaging it away. Semantic similarity adds nothing. Evidence tiers:

- strong — exact full name or known alias (1.0; 0.85 for a two-part alias);
- medium — reordered full name (0.90), 2-part reordered (0.75), a typo in one
  component (≤ 0.80), missing patronymic (0.80);
- weak — initials (≤ 0.58), surname alone (0.50), semantic similarity.

### Decision policy

1. No candidates → CREATE_NEW (`no_candidate`).
2. Plausible = no conflict and score ≥ `ER_REVIEW_MIN_SCORE`.
3. AUTO_LINK only if the top plausible ≥ `ER_AUTO_LINK_MIN_SCORE`, the margin
   `top1 − top2` over plausible candidates is above `ER_MIN_MARGIN`, there is a
   single strong candidate (otherwise `possible_duplicate_persons`), and the
   incoming name is neither initials-only nor incomplete. Otherwise REVIEW with
   every blocking reason.
4. Top plausible below the auto-link minimum → REVIEW (`medium_confidence_match`).
5. Nothing plausible → CREATE_NEW (`no_plausible_candidate`,
   `conflicting_identity_data` when conflicts were the reason). Exception: the
   semantic source was enabled but unavailable and a non-conflicting candidate
   shares the surname — linking vs creating stays open without it → REVIEW
   (`semantic_source_unavailable`). A safe AUTO_LINK or a clear CREATE_NEW
   proceeds during an outage.

A conflicting identity (different full patronymic or given name) is treated as
a different person: CREATE_NEW, never AUTO_LINK.

### Application, provenance, review

`PersonResolutionService` applies the decision in the caller's transaction and
records `person_resolution_decisions` (unique per mention and resolver version):
method (`exact_matching_key` / `er_v2`), action, status (`applied`,
`pending_review`, `reviewed`), score, margin, reason codes, identity, candidate
and feature snapshot, semantic source status, `resolver_version = er-v2`,
timestamps.

- AUTO_LINK links the mention. `AliasPromotionPolicy` adds the surface form as
  an alias only for clean full forms (no initials, typos or incomplete names).
- CREATE_NEW creates the Person through the existing racing-safe resolver.
- REVIEW leaves `entity_mentions.person_id` NULL (already nullable; research,
  classification and person-event links only use linked mentions) and creates a
  pending `review_records` row (`subject_type = person_resolution`). No
  placeholder Person.

Reviewers see a structured comparison per candidate (`GET
/person-resolution/reviews/{id}`, `person-resolution-reviews show`) and apply
`link_to_person`, `create_new_person`, `merge_persons` or `keep_separate`;
linking also creates the mention's person-event links.

Re-running resolution reuses the recorded decision; mentions linked before ER
v2 are not re-resolved. Re-resolution under a new algorithm version is a
separate explicit operation (not implemented).

### Concurrency

On top of the unique index, `ExtractionResolutionService` takes
`pg_advisory_xact_lock` on the identity-block keys of all mentions of a run,
sorted, before resolving. Block keys are the sorted full name tokens, equal for
reordered forms, so "Иван Иванов" and "Иванов Иван" in two workers are
serialized and the second sees the first Person.

### Evaluation and calibration

`tests/fixtures/er_v2_corpus.json`: 30 persons, 52 cases — 34 positive (exact,
reordered, case, whitespace, `ё`, declension, alias, typos, initials, foreign
and suffix-trap surnames, missing patronymic), 12 hard negatives (same surname,
same initial, same first/last with a different patronymic, similar spelling, a
semantic trap) and 6 ambiguous cases (initials with several namesakes, two
Ivan Ivanovs, surname only, duplicate persons with a typo). Names pass through
the extraction normalizer on both sides, as in production.

`evaluate-er` reports candidate recall@k per generator set and decision
metrics: auto-link precision/recall, false links, false create-new, review rate,
unnecessary and missed reviews, plus a threshold sweep.

| generator | recall@1 | recall@5 | recall@10 |
|---|---|---|---|
| exact (fast path + alias key) | 0.38 | 0.38 | 0.38 |
| trigram | 0.88 | 1.00 | 1.00 |
| semantic (E5, no threshold) | 0.91 | 1.00 | 1.00 |
| combined | 0.94 (0.97 with semantic) | 1.00 | 1.00 |

Decisions at the defaults: auto-link precision 1.00, recall 0.53, false links
0, false create-new 0, review rate 0.44, 1 unnecessary review (a similar-spelling
negative), 0 missed reviews. Sweep: `ER_AUTO_LINK_MIN_SCORE = 0.85` is the
lowest value without a false link (0.80 auto-links "Илья Петрович Иванов" to
"Илья Иванов"); `ER_REVIEW_MIN_SCORE = 0.40` is the highest without a missed
link (0.50 creates a new person for "А. В. Новикова"). `ER_MIN_MARGIN = 0.10` is
not discriminated by this corpus. Semantic candidates changed no decision and
no recall@5, so they stay opt-in.

## Consequences

- Reordered names, aliases and exact 3-part forms link automatically; typos,
  initials, incomplete names and namesakes go to review instead of silently
  creating duplicates.
- The review queue grows (44% of corpus cases); that is the chosen price for
  zero false links.
- Semantic similarity can surface a candidate but can never link one.
- Linking/creating a Person changes its semantic document; the index is
  refreshed by the manual `rebuild-semantic-index --incremental` (content hash),
  automatic refresh is left to monitoring.

## Known limitations

- The corpus is small and synthetic, and thresholds are tuned on it.
- The extraction normalizer cuts final letters (`Анна Новикова` → `Анн
  Новиков`), losing gender and mangling given names; ER v2 compares those forms
  on both sides and initials forms (not normalized by extraction) score lower.
- No transliteration, diminutive (`Маша`/`Мария` without an alias) or phonetic
  matching; no birth dates or context features (none are extracted).
- The exact fast path still links on an equal `matching_key`, so it cannot
  detect duplicates or namesakes sharing that key.
- Advisory locks include given-name tokens and are held for a whole extraction
  run: correct, but concurrent runs with common names serialize.
