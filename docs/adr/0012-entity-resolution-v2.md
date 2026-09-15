# ADR 0012: Entity Resolution v2

## Status

Accepted; amended 2026-09-16 ("matching_key is a candidate lookup key, not an
identity key", below). Supersedes ADR 0005; extends ADR 0004 (canonical Person
model).

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
       → identity-block advisory lock
       → candidate generation (exact matching_key, alias key, pg_trgm, optional semantic)
       → PersonResolutionFeatureExtractor → PersonResolutionScorer
       → PersonResolutionDecisionPolicy → AUTO_LINK | REVIEW | CREATE_NEW
```

Invariant: **ER v2 may link a new mention to an existing Person or create a
Person; it never merges two existing canonical Persons.** A merge only happens
on an explicit reviewer action (`MERGE_PERSONS`), audited by the existing
`PersonMergeRecord`.

### Amendment 2026-09-16: `matching_key` is a candidate lookup key, not an identity key

The original decision kept an exact fast path: an active person with the
incoming `matching_key` was linked without scoring, and
`uq_persons_matching_key_active` allowed one active person per key. Two real
people named "Алексей Сергеевич Иванов" therefore could not both exist: the
second mention was silently linked to the first. Same name is not same person,
and a false link is worse than a review.

- The unique index is replaced by the non-unique partial index
  `ix_persons_matching_key_active` (migration `o9p0q1r2s3t4`; existing rows and
  links are untouched; downgrade refuses while active namesakes share a key).
- There is no fast path and no `RuleBasedPersonResolver`. Every mention goes
  through candidates → features → score → policy. `ExactKeyCandidateGenerator`
  returns zero, one or all active namesakes (source `exact_key`, or `alias` for
  an alias key), name-key matches first, then by id; `exact_matching_key` is a
  feature only. `ER_CANDIDATE_LIMIT` is at least 2, so two namesakes always reach
  the policy. `resolve_mention` locks one mention; a caller resolving several
  mentions in one transaction must lock all their blocks first.
- One exact candidate without conflicts still auto-links (score 1.0, margin
  over any weaker competitor). Two or more active persons with the incoming key
  add `multiple_exact_name_matches`, which blocks AUTO_LINK whatever the scores:
  namesakes are never picked by id, by candidate order or by semantic similarity
  (which adds nothing to the score). No structured context (court, region)
  exists to tell namesakes apart, so they go to review.
- An exact complete form (the same name or known alias, no initials, not a
  single token) scores at least 0.85 whatever role reading wins: suffix hints
  may read "Дмитрий Шостакович" as given name + patronymic, and a name of four
  or more tokens has no reading at all.
- Concurrency without the unique index: `PersonResolutionService.resolve_mention`
  takes the transaction-scoped advisory locks of its identity block before it
  reads candidates (re-entrant; `ExtractionResolutionService` still takes all of
  a run's blocks up front, sorted). Candidates and the decision are computed
  under the lock, so a worker that waited sees the Person committed by the other
  one and links to it; CREATE_NEW inserts only after that re-read.
- A reviewer may create a namesake with the same key (`create_new_person`). A
  reviewer's `keep_separate` records `distinct_from_person_id` on the decision;
  later decisions with both persons among the strong candidates report
  `known_distinct_persons` instead of `possible_duplicate_persons` (still REVIEW:
  knowing two people differ does not tell which one a new mention is). Merge
  stays an explicit reviewer action.
- `resolver_version` stays `er-v2`: stored decisions (including legacy
  `method = exact_matching_key`) are reused, nothing is re-resolved.

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

- `ExactKeyCandidateGenerator`: every active person whose name (`exact_key`) or
  known alias (`alias`) has the incoming key — namesakes included.
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
`exact_alias`, `exact_matching_key` (a feature, never a decision), `initials_only`, `incomplete_name`,
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
form +0.05; different order −0.05; an exact complete form scores at least
0.85 (amendment). Any conflict caps the score at 0.25 instead of averaging it
away. Semantic similarity adds nothing. Evidence tiers:

- strong — exact full name or known alias (1.0; 0.85 for a two-part alias);
- medium — reordered full name (0.90), 2-part reordered (0.75), a typo in one
  component (≤ 0.80), missing patronymic (0.80);
- weak — initials (≤ 0.58), surname alone (0.50), semantic similarity.

### Decision policy

1. No candidates → CREATE_NEW (`no_candidate`).
2. Plausible = no conflict and score ≥ `ER_REVIEW_MIN_SCORE`.
3. AUTO_LINK only if the top plausible ≥ `ER_AUTO_LINK_MIN_SCORE`, the margin
   `top1 − top2` over plausible candidates is above `ER_MIN_MARGIN`, there is a
   single strong candidate (otherwise `possible_duplicate_persons`, or
   `known_distinct_persons` after a reviewer's keep-separate), at most one
   plausible candidate has the incoming `matching_key` (otherwise
   `multiple_exact_name_matches`), and the incoming name is neither
   initials-only nor incomplete. Otherwise REVIEW with every blocking reason.
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
method (`er_v2`; `exact_matching_key` only on decisions of the removed fast path), action, status (`applied`,
`pending_review`, `reviewed`), score, margin, reason codes, identity, candidate
and feature snapshot, semantic source status, `resolver_version = er-v2`,
timestamps.

- AUTO_LINK links the mention. `AliasPromotionPolicy` adds the surface form as
  an alias only for clean full forms (no initials, typos or incomplete names).
- CREATE_NEW creates the Person (and its first alias) under the identity-block lock.
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

`pg_advisory_xact_lock` on the identity-block keys is the only guard against
concurrent duplicates (there is no unique key). `ExtractionResolutionService`
takes the keys of all mentions of a run, sorted, before resolving, and
`resolve_mention` takes its own (a no-op when already held). Block keys are the
sorted full name tokens, equal for reordered forms, so "Иван Иванов" and
"Иванов Иван" (or the same name twice) in two workers are serialized and the
second sees the first Person. A reviewer's `create_new_person` takes the same
locks, so a worker resolving that name meanwhile waits and then sees the new
namesake.

### Evaluation and calibration

`tests/fixtures/er_v2_corpus.json`: 36 persons, 59 cases — 38 positive (exact,
reordered, case, whitespace, `ё`, declension, alias, typos, initials, foreign
and suffix-trap surnames, missing patronymic, a 4-token name, an exact name with
a weaker competitor), 12 hard negatives (same surname, same initial, same
first/last with a different patronymic, similar spelling, a semantic trap), 8
ambiguous cases (initials with several namesakes, two Ivan Ivanovs, surname
only, duplicate persons with a typo, two active namesakes with an exact and a
reordered name, one of them reviewer-created) and 1 indistinguishable case (the
only existing person with that full name, but a different human). Names pass
through the extraction normalizer on both sides, as in production.
Indistinguishable cases are reported as `indistinguishable_namesake_links`, not
as false links: nothing in the data can separate them.

`evaluate-er` reports candidate recall@k per generator set and decision
metrics: auto-link precision/recall, false links, false create-new, review rate,
unnecessary and missed reviews, plus a threshold sweep.

| generator | recall@1 | recall@5 | recall@10 |
|---|---|---|---|
| exact (name + alias key) | 0.42 | 0.42 | 0.42 |
| trigram | 0.82 | 1.00 | 1.00 |
| semantic (E5, no threshold) | 0.92 | 1.00 | 1.00 |
| combined | 0.92 (0.97 with semantic) | 1.00 | 1.00 |

Decisions at the defaults (after the amendment, 2026-09-16): auto-link precision
1.00, recall 0.58, false links 0 (none on the namesake cases), 1
indistinguishable namesake link, false create-new 0, review rate 0.43, 1
unnecessary review (a similar-spelling negative), 0 missed reviews. Sweep: `ER_AUTO_LINK_MIN_SCORE = 0.85` is the
lowest value without a false link (0.80 auto-links "Илья Петрович Иванов" to
"Илья Иванов"); `ER_REVIEW_MIN_SCORE = 0.40` is the highest without a missed
link (0.50 creates a new person for "А. В. Новикова"). `ER_MIN_MARGIN = 0.10` is
not discriminated by this corpus. Semantic candidates changed no decision and
no recall@5, so they stay opt-in.

## Consequences

- Reordered names, aliases and exact 3-part forms link automatically; typos,
  initials, incomplete names and namesakes go to review instead of silently
  creating duplicates.
- The review queue grows (43% of corpus cases); that is the chosen price for
  zero false links. Every further mention of a name shared by several active
  persons is reviewed.
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
- A single existing person with the incoming full name is linked: a different
  human with that name is indistinguishable without context (no court, region
  or birth date is extracted). A second namesake appears only through a
  reviewer's `create_new_person`.
- Without a unique key, duplicates are prevented only for mentions that share
  an identity block (a full name token); names without a shared full token
  (typo in every token) can still race into two persons, as before.
- Advisory locks include given-name tokens and are held for a whole extraction
  run: correct, but concurrent runs with common names serialize.

## Amendment (2026-09-15): name-only evidence

Real-world validation v1 found that a name without patronymic auto-linked
across articles: a different journalist «Иван Фролов» and a different
«Николай Маркин» were linked to the only existing person with that name
(score 0.85 via `exact_complete_form`, patronymic missing). On the real
corpus every AUTO_LINK (110) rested on such a name match.

Decision: AUTO_LINK additionally requires identity evidence beyond the name —
surname, given name and patronymic all equal (`full_identity_match`), or the
candidate already holding a mention of the same article
(`same_article_mention`, within-article coreference). Otherwise the decision
is REVIEW with `name_only_evidence`. Known aliases without patronymic are
names too. Scores and thresholds are unchanged.

Consequences, measured on the real-world corpus (156 articles): namesake
different-person AUTO_LINK 2 → 0, false links stay 0, ER AUTO_LINK recall on
golden mentions 0.42 → 0.25, ER reviews per 100 articles 69 → 114. Repeated
names inside one article still link automatically. A single existing person
with the same full name *and* patronymic is still linked (limitation above).
`RESOLVER_VERSION` stays `er-v2`: the rule applies to new decisions; links made
before it are not revisited automatically (ER never unlinks on its own).
