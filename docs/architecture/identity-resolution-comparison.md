# Identity resolution: ER v2 and entity grouping on the same mentions

Status: experiment, 2026-10-11. It informs "Post-extraction domain model decision" in
`modularization-plan.md`. It decides nothing and changes no pipeline. The canonical
identity is not chosen.

The question: can a `Person` be the canonical identity, with the case layer built over
it, or does entity grouping give the better identity?

- **A** is ER v2 (`persons/resolution`).
- **B** is entity grouping with its rules alone (`entities/grouping`, in the order of
  `EntityCollector.run`). It is not the production pipeline B: production also asks a
  model to name each entity in the nominative, and applies the operator's pair
  decisions, name corrections and removals. None of that ran here. No model was called.

Reproduce:

```bash
PYTHONPATH=src uv run python -m evaluation.identity_comparison \
  --corpus tests/fixtures/er_v2_corpus.json \
  --server-url postgresql+psycopg://…@127.0.0.1:5434/court_monitor_test \
  --real-url postgresql+psycopg://…@127.0.0.1:5434/junk_eval \
  --out var/identity_comparison
```

Code: `src/evaluation/identity_comparison/`. Numbers: `var/identity_comparison/results.json`.
The sample for a person to judge: `var/identity_comparison/disagreement_sample.json`. It
holds names from the real database and stays under the ignored `var/`.

## 1. Datasets

| | Synthetic | Real |
|---|---|---|
| What | `tests/fixtures/er_v2_corpus.json` | database `junk_eval` in the test container |
| Size | 36 persons, 59 cases | 38 996 person mentions, 20 996 articles |
| Ground truth | yes: who each incoming name is | none |
| Used for | quality | disagreement and operational properties only |
| How A ran | planned here, in a database made for the run and dropped | read as stored: 8 724 persons and the decisions ER v2 took earlier |
| How B ran | here, in memory | here, in memory; the database is opened read-only and nothing is written to it |

The comparison on the real database takes the mentions `EntityCollector` would take:
the person mentions of the latest successful extraction of the articles with a
criminal-case event, the authors' signatures left out. That is 21 778 mentions of
4 299 articles.

## 2. Ground truth

A case of the synthetic corpus is one incoming name against the 36 stored persons. It
says which person the name is, or that it is nobody stored. 58 cases can be told apart
from the data and 38 of them have a true person. One case, a namesake with the same
full name, cannot be told apart by any system without context; it is counted on its
own, as the corpus does.

The corpus was written while ER v2 was being built, to test its decisions. It may
favour A: its cases are the ones ER v2 was tuned on, and it has no article around a
name, which is where B joins a surname to a full name.

## 3. Method

Both answers are put in the same terms. For one incoming name a model may:

| Outcome | A | B |
|---|---|---|
| join it to a person without a human | `AUTO_LINK` | the name falls into the entity of that person's names |
| leave it to a human | `REVIEW` | a pair is offered in «Спорные случаи» (`disputes.find_pairs`) |
| keep it apart | `CREATE_NEW` | an entity of its own |
| make nothing of it | never | no entity: the rules make none of initials or a surname alone outside its article |

Counted over the 58 cases:

- pairwise precision: correct joins over all joins;
- pairwise recall: correct joins over the cases with a true person;
- false merge rate: joins to a wrong person, over all cases;
- false split rate: a true person existed, the name was kept apart and no human was
  asked, over the cases with a true person;
- accuracy on namesakes: the cases tagged as namesakes or near ones (10) without a
  false merge;
- coverage: the cases the model made anything of.

A review is not a join and not an error: it is counted on its own, with how often the
true person was among those shown.

A third column, **B repaired**, is not a model that runs anywhere. See section 5.

On the real database nothing is scored. The two groupings are compared over the
mentions both of them grouped, and the cases they group differently are listed and
sampled.

## 4. Results: ER v2

Synthetic corpus, 58 cases:

| | |
|---|---|
| correct joins | 15 |
| false merges | 0 |
| left to a human | 32, the true person shown in 23 |
| false splits | 0 |
| correctly kept apart | 11 |
| pairwise precision / recall / F1 | 1.00 / 0.39 / 0.57 |
| false merge rate / false split rate | 0.00 / 0.00 |
| review rate | 0.55 |
| coverage | 1.00 |
| accuracy on namesakes (10 cases) | 1.00 |
| the indistinguishable namesake | joined to the stored person |

ER v2 makes no wrong join and no wrong split. It decides little by itself: more than
half the cases go to a human.

Real database, every person mention (38 996), the latest decision of each:

| Action | Mentions | Share |
|---|---|---|
| review, pending | 19 576 | 50.2 % |
| auto link | 10 696 | 27.4 % |
| create new | 8 724 | 22.4 % |

What goes to review, by the shape of the stored name:

| Shape | Review | Auto link | Create new |
|---|---|---|---|
| given name and surname | 12 429 | 2 937 | 7 978 |
| surname alone | 6 827 | 7 626 | 161 |
| three words | 320 | 133 | 585 |

The reasons ER v2 recorded on the pending reviews (a decision carries several):
`name_only_evidence` 17 291, `medium_confidence_match` 7 310, `incomplete_name` 6 549,
`multiple_plausible_candidates` 2 708, `no_plausible_candidate` 1 105,
`conflicting_identity_data` 599, `case_context_match` 576, `initials_only` 517.

The automatic links: `same_article_surname_reference` 7 626, `strong_unique_match`
3 070, `case_context_match` 1 387.

So the review queue is not mainly hard cases. Its largest part is a given name and a
surname that match a stored person by name and nothing else: ER v2 will not link on a
name alone. Most of what it links by itself is a surname pointing back to a full name
in the same article.

## 5. Results: entity grouping, rules alone

Synthetic corpus, 58 cases:

| | B as stored | B repaired |
|---|---|---|
| correct joins | 10 | 25 |
| false merges | 0 | 2 |
| left to a human | 0 | 5, the true person shown in 3 |
| false splits | 6 | 6 |
| correctly kept apart | 11 | 13 |
| nothing made of the name | 31 | 7 |
| pairwise precision / recall / F1 | 1.00 / 0.26 / 0.42 | 0.93 / 0.66 / 0.77 |
| false merge rate / false split rate | 0.00 / 0.16 | 0.03 / 0.16 |
| review rate | 0.00 | 0.09 |
| coverage | 0.47 | 0.88 |
| accuracy on namesakes (10 cases) | 1.00 | 0.80 |
| the indistinguishable namesake | nothing made of it | joined to the stored person |

**Why "as stored" makes nothing of 31 names.** The rules read `first_name`, `last_name`
and `patronymic` of a mention. For a name of three words the extraction normalizer
stores the first word as both the given name and the surname, and the third as the
patronymic (`RuleBasedMentionNormalizer._normalize_person`, the lines that set
`first_name`, `last_name`, `patronymic`). «Анна Викторовна Новикова» is stored as given
name «Анна», surname «Анна», patronymic «Новикова». Grouping sees a given name equal to
the surname, takes it for an extraction error and keeps a bare surname, «Анна», of
which it makes no entity. `full_name` and `matching_key` are stored correctly, and
ER v2 reads those, so A is not affected. The 1.00 on namesakes in this column means
only that nothing was merged because nothing was placed.

Production does not depend on these fields alone: the model reads the name as the text
wrote it and names the entity. That step did not run here. On the real database 1 038
of 38 996 person mentions have a third word, so the defect touches few mentions there;
the synthetic corpus is full of three-word names, so it dominates the column.

**"B repaired"** answers what the rules do when the three parts are in place. The
benchmark splits a three-word name by where the patronymic stands and gives the rules
those parts. It is a counterfactual to separate the rules from the stored names, not a
proposal and not a measured pipeline.

Repaired, the rules join more than ER v2 does by itself (25 against 15) and pay for it:

- 2 false merges, both a three-word name equal to a stored person's where the corpus
  says it is another human. ER v2 sends both to a human.
- 6 false splits: 4 misspelt names, and 2 of the form «Зайцев Г.», a surname and an
  initial. ER v2 sends all 6 to a human.
- 7 names nothing is made of: initials, a surname alone, a name it cannot parse.

Real database: over the 21 778 mentions the rules made 4 513 entities in 0.6 s and
placed 20 597 mentions (94.6 %). 1 181 got no entity.

## 6. Disagreement analysis

### Synthetic

All three columns give the same outcome in 8 of 59 cases. A and B as stored differ in
51, almost all through the three-word names. A and B repaired differ in 27:

| A | B repaired | Cases |
|---|---|---|
| review | correct join | 10 |
| review | nothing made of the name | 7 |
| review | false split | 6 |
| review | false merge | 2 |
| review | correctly apart | 2 |
| review | review | 5 (agree) |
| correct join | correct join | 15 (agree) |
| correctly apart | correctly apart | 11 (agree) |
| false merge | false merge | 1 (agree: the indistinguishable namesake) |

Every disagreement has A asking a human. In 12 of the 27 the rules decided rightly
without one; in 8 they decided wrongly; in 7 they dropped the name silently.

### Real

Coverage of the 21 778 mentions:

| ER v2 | Rules | Mentions |
|---|---|---|
| linked to a person | in an entity | 12 130 |
| pending review | in an entity | 8 467 |
| linked to a person | no entity | 600 |
| pending review | no entity | 581 |

Over the 12 130 mentions both grouped (3 978 persons, 3 910 entities):

| | Pairs of mentions |
|---|---|
| joined by both | 45 476 |
| joined by ER v2 only | 49 |
| joined by the rules only | 548 |

Where both decide, they almost always decide alike. The differences:

| Kind | Cases |
|---|---|
| one entity of the rules holds several ER v2 persons, one of them with a three-word name | 29 |
| …, the persons share a given name and differ in the form of the surname | 29 |
| …, other | 15 |
| one ER v2 person is spread over several entities of the rules | 7 |
| ER v2 left the mention to a human, the rules placed it | 8 467 mentions |

Read by eye, without labels, in the sample: all 7 of the fourth kind and 14 of the
first three. The first kind is mostly a registry's «Фамилия Имя Отчество» that ER v2
stored as a person beside the news' «Имя Фамилия». The second is mostly a surname the
dictionary left in an oblique case, stored by ER v2 as a second person. In these the
rules look right and ER v2 has made a duplicate person. The third is mostly mentions
that are no name at all (a surname with a word that follows it in the text); there
either model may be wrong. This reading is an impression of 21 cases, not a
measurement.

The last row is the real difference between the models and the one this experiment
cannot settle. ER v2 holds back 8 467 mentions that match by name alone; the rules join
them to the entity of that name. Whether those joins are right is a question about
namesakes in this corpus, and it needs labels.

The sample holds 25 cases of each kind (15 and 7 where there are no more), 97 in all,
drawn with a fixed seed.

## 7. Operational comparison

| Property | ER v2 | Entity grouping | Source |
|---|---|---|---|
| Decides by itself | 49.8 % of mentions; the rest wait for a person | 94.6 % of the mentions it takes | measured, real |
| Identity | `persons.id`, assigned once | a key made of the name; `entity_groups.id` is new at every rebuild | code |
| A repeated run | reuses its decisions and links | identical entities and keys | A: code and its tests; B: measured |
| New mentions | resolved one at a time; an existing person is never merged with another | everything is regrouped | code |
| Keys after growth | not applicable | the rules over the oldest 80 % of the articles (3 439), then over all (4 299): all 3 834 keys kept, none split, none merged | measured, real, rules alone |
| Manual decisions | stored on the decision and the review record, by id | stored by entity key and found again through `KeyIndex` | code; not measured: the real database has no operator decisions |
| Model calls | none; an AI reviewer is optional (ADR 0020) and was not configured | none here; in production one call per 50 entities not answered before: 91 for a first build of these 4 513 entities | A: code; B: computed from `BATCH_SIZE` |
| Time | 600 articles in 36 s in one process, 16 s in four | 21 778 mentions in 0.6 s | A: the comment in `parallel_resolution.py`, not re-measured; B: measured |
| Audit | candidates, scores and reasons of every decision | none for a join made by the rules | code |

Two limits of the growth row: it is the rules alone, and a key also changes when the
model renames an entity or a rule is edited, neither of which this measures.

## 8. Conclusions

1. Where both models decide, they agree: 45 476 pairs joined by both against 597 joined
   by one. The disagreements are few and, by eye, more often a duplicate person of
   ER v2 than a wrong merge of the rules.
2. The models differ in what they are willing to decide. ER v2 does not join on a name
   alone and leaves half of all mentions to a person. The rules join on a name and
   leave almost nothing. This is a policy, not a difference in skill at matching.
3. ER v2 made no wrong join on the synthetic corpus. The rules, with the names
   repaired, made two, both on namesakes, and six wrong splits on misspelt names and
   initials, where they have no fuzzy match and ask nobody.
4. The rules depend on three stored fields that are wrong for every three-word name.
   Production hides this behind the model. ER v2 does not use those fields.
5. Identity by key held up under growth in the one test made of it. Identity by id
   needs no such test.

What these do and do not show:

- ER v2 is careful, and a review rate of 50.2 % makes it insufficient, as it runs, as
  a ready layer of canonical identity.
- Grouping by the rules alone covers far more, and there is no ground truth for the
  joins it makes beyond ER v2's.
- The synthetic corpus may favour ER v2.
- "B repaired" is a counterfactual of this benchmark, not a measured pipeline.
- Production B, with the model that names entities, is not measured.

## 9. Recommendation on the canonical identity

Not yet decidable, and this experiment says what is missing.

`Person` can be the canonical identity only together with an answer to the review
queue. As it runs today ER v2 gives a person to half the mentions; a case layer built
over it would see the other half as nobody. The answer is a policy for a name that
matches by name alone: link it at a lower confidence, as grouping in effect does; give
it to the AI reviewer of ADR 0020; or keep it for a human. Which of these is safe
depends on how often two people of this corpus share a given name and a surname, and
that is exactly the unlabelled 8 467.

So the layered hypothesis (persons as identity, the case over them) stands, with a
condition it did not have before: it is blocked by the review rate of ER v2, not by
the quality of its joins. Entity grouping is not shown to be the better identity: where
it decides more, nobody has checked that it decides rightly.

Next, in this order:

1. A person labels the sample of 97 cases. That gives the first real numbers: the
   share of right joins among what the rules join and ER v2 holds back, and the share
   of duplicate persons among what ER v2 keeps apart.
2. The comparison with the model, as agreed: the synthetic corpus first, then only the
   labelled sample, with no more text than a name's forms. It is warranted: the gap
   between B as stored and B repaired (recall 0.26 against 0.66) shows that
   normalization changes the result.
3. Only then the design of the link between a person and the case.

Separately, and not part of this experiment: the three-word names in the extraction
normalizer are a defect in stored data that both pipelines read. It was not fixed here.
Whether to fix it is its own decision; a fix changes what extraction stores.

### Preparing the sample for labelling

```bash
PYTHONPATH=src uv run python -m evaluation.identity_comparison.annotation \
  --real-url postgresql+psycopg://…@127.0.0.1:5434/junk_eval \
  --out var/identity_comparison
```

writes `var/identity_comparison/disagreement_annotation.json`: the same 97 cases, each
turned into pairs of sides with an empty label. A side is a group one of the models
made (an ER v2 person, an entity of the rules, or one mention), shown by up to three of
its mentions, each with the article's title, date, source, the events it takes part in,
both models' decisions, and about 300 characters of the article on either side. A
person sets each pair's label to `same_person`, `different_person` or
`insufficient_evidence`. Nothing is labelled by the command. The file holds names and
text of the real database and stays under the ignored `var/`; the database is only
read.

### Local manual labelling CLI

```bash
PYTHONPATH=src uv run python -m evaluation.identity_comparison.labelling
PYTHONPATH=src uv run python -m evaluation.identity_comparison.labelling --stats
PYTHONPATH=src uv run python -m evaluation.identity_comparison.labelling --edit <pair_id>
PYTHONPATH=src uv run python -m evaluation.identity_comparison.labelling \
  --merge-out var/identity_comparison/labelled_sample.json
```

The default source is `var/identity_comparison/disagreement_annotation.json`; answers
go to `var/identity_comparison/manual_annotations.json`. `--sample` and `--annotations`
can select other local files. Keep real data, including merge output, under `var/`.
The CLI does not import database or API clients and does not contact a model.

It displays `pair N/100`, both sides and all their stored mentions, with surface and
normalized names, article title, date, source and context. Model decisions are hidden
until `m`. `s`, `d` and `i` save `same_person`, `different_person` and
`insufficient_evidence`; `n`, `g` and `x` save `not_a_person`, `mixed_identity_group`
and `invalid_mention`. `k` saves a skip and advances; `q` quits. Ctrl-C and EOF stop
without losing answers already saved. Each answer writes a complete temporary file
in the destination directory, flushes and fsyncs it, replaces the destination
atomically, then fsyncs the directory. A failed replacement leaves the old file intact.
Use one labelling process per annotations file.

The separate file has this format (the id below is an invented example):

```json
{
  "version": 1,
  "annotations": {
    "case-invented:a-b": {
      "label": "same_person",
      "comment": null,
      "skipped": false
    }
  }
}
```

A skip has `label: null` and `skipped: true`. Resume starts at the first pair without
a label, including skipped pairs, in source order. `--edit <pair_id>` explicitly
opens one pair and replaces its label on `s/d/i`; skipping while editing keeps the
old label. Unknown pair ids and invalid stored labels are refused. Statistics report
total, labelled, each label, skipped and unlabelled; skipped is a subset of unlabelled.
Both v1 and v2 annotations are accepted. Saving a special label or flags upgrades v1
to v2. Existing flags, comment, index and other entry metadata survive editing.
`--edit <pair_id> --flags bad_span bad_normalization --comment "note"` replaces the
diagnostics and comment for that pair when an answer is saved. Empty `--flags` clears
flags. Flags and comments can only be changed with explicit `--edit`. Supported flags:
`bad_span`, `bad_normalization`, `ner_error`, `name_order`, `initial_expansion`.

`--merge-out` joins annotations by the existing deterministic `pair_id` into a copy
of the generated sample for subsequent evaluation. It keeps contexts, model decisions,
pair ids and unlabelled pairs, including the case with no pairs. It refuses to overwrite
the source or manual annotations. This supplies labelled ground truth for later
metrics; this command does not compute model quality or start labelling.

### Evaluation of the completed manual labels

The supplied `manual_annotations_extended.json` is the ground truth for this stage.
`tests/fixtures/identity_comparison/` keeps the three supplied files unchanged and a
decision-only projection of the generated sample (`disagreement_decisions.json`).
The projection preserves all 97 cases, source order, deterministic pair and side IDs,
strata, model decisions and the case with no pairs, without copying article text or
mention names. The supplied annotation comments are kept verbatim. `provenance.json`
records input hashes and the hash of the original generated artifact. The compact
sample is for evaluator replay; interactive labelling still uses the full `var/` sample.

Reproduce without a database, model or external API:

```bash
PYTHONPATH=src uv run python -m evaluation.identity_comparison.evaluate_manual \
  --sample tests/fixtures/identity_comparison/disagreement_decisions.json \
  --annotations tests/fixtures/identity_comparison/manual_annotations_extended.json \
  --reference tests/fixtures/identity_comparison/identity_disagreement_results.json \
  --out var/identity_comparison/manual_evaluation.json
```

The full generated sample can also be passed with `--sample`. The output JSON includes
false merges, false splits, ER abstentions and special/unlabelled rows with index,
pair ID, case ID, stratum, manual label, original model decisions, normalized
predictions, flags and comment. It contains no article text. `--reference` compares
the independently computed results with the preliminary calculation; it does not
participate in scoring. Unknown decisions, foreign pair IDs or inconsistent supplied
indices cause an error rather than being silently scored or repaired.

There are 100 labelled pairs: 82 `same_person`, 11 `different_person`, 1
`insufficient_evidence`, 1 `not_a_person`, 3 `mixed_identity_group`, 2
`invalid_mention`. Only the first two labels form the 93 evaluable identity pairs.
The other seven are excluded from identity accuracy for both systems. In particular,
insufficient evidence and mixed groups are never coerced to an identity verdict.

| Model | Automatic / evaluable | Coverage | Correct / automatic | Accuracy | False merges | False splits | Abstentions |
|---|---:|---:|---:|---:|---:|---:|---:|
| ER v2 | 71 / 93 | 76.3% | 12 / 71 | 16.9% | 3 | 56 | 22 |
| Rules grouping | 93 / 93 | 100% | 81 / 93 | 87.1% | 8 | 4 | 0 |

Coverage uses evaluable pairs; accuracy uses automatic decisions on those pairs.
ER's 12/93 (12.9%) is the fraction of evaluable pairs with a correct automatic
answer, not an accuracy that treats abstention as an error. Across all 100 pairs,
including non-evaluable labels, ER has 76 automatic decisions and 24 abstentions;
rules has 100 automatic decisions.

| Stratum | All pairs | Evaluable | Same / different | ER correct / wrong / abstain | Rules correct / wrong |
|---|---:|---:|---:|---:|---:|
| `er_joins_rules_split` | 7 | 7 | 4 / 3 | 4 / 3 / 0 | 3 / 4 |
| `er_review_rules_place` | 24 | 22 | 22 / 0 | 0 / 0 / 22 | 22 / 0 |
| `rules_join_er_splits` | 69 | 64 | 56 / 8 | 8 / 56 / 0 | 56 / 8 |

The preliminary 81/93, 12/71, 56 ER false splits and 8 rules false merges all agree,
as do the provided error pair IDs. Its per-stratum `pairs` field meant evaluable
pairs (7, 22, 64); this report distinguishes all pairs (7, 24, 69) from evaluable.

Diagnostic counts are per pair and may overlap: `bad_span` 12,
`bad_normalization` 28, `ner_error` 1, `name_order` 5, `initial_expansion` 2.
Special labels are counted separately as above. Among the 71 pairs where a system
makes an identity error, 33 have no extraction/normalization flag, 10 have `bad_span`
or `ner_error`, and 28 have `bad_normalization`. Absence of a flag is not proof that
extraction was sound; these categories describe annotations, not a causal diagnosis.
Per system: rules has 5 errors without those flags, 6 with span/NER flags and 1
with normalization flags; ER has 28, 4 and 27 respectively. Categories may overlap.
The 3 mixed groups and 2 invalid mentions stay outside the identity error lists.

Two manually verified namesake pairs are rules false merges: pair #68
(`case-745d2982ff94:a-b`, Andrey Lugovoy) and #100
(`case-8501beb5abd5:a-b`, Alexander Shevchuk). Both have ground truth
`different_person`, rules `one_entity`, and ER `different_persons`. They are not
explained by a recorded span or normalization flag. Four other rules false-merge
pairs have `bad_span`; their stored mentions cross entity boundaries. These remain
pairwise errors in the metrics but must be investigated as extraction-associated
failures rather than evidence of an identity-resolver defect alone.

These are accuracy and error counts within a deliberately selected disagreement
sample, not estimates of corpus-wide quality. The labels expose namesake joins,
conservative ER splitting/review and upstream extraction/normalization problems.
They do not settle the canonical identity model or justify changing production rules.

## 10. Limits of the experiment

- B is the rules alone. Production B adds a model and the operator's decisions; its
  quality is not measured here in either direction.
- The synthetic corpus is small (59 cases), was built for ER v2 and has no article
  context. B joins a surname to a full name only inside an article, so the corpus
  cannot show that; ER v2's use of the same context is also not exercised.
- "B repaired" is a counterfactual. Its split of a three-word name is the benchmark's
  own and is not the production model.
- The full real corpus has no ground truth. The initial qualitative reading covered
  21 cases; the completed manual evaluation above covers 100 sampled disagreement
  pairs. Its accuracy is conditional on that sample, not a corpus-wide quality score.
- ER v2 on the real database is what was stored by an earlier run (`er-v2`), at schema
  revision `r2s3t4u5v6w7`. It was not re-run. No AI reviewer was configured, and no
  human review had been done: the pending reviews are the untouched queue.
- The real database holds no entity groups and no operator decisions, so the survival
  of manual decisions across a rebuild is described from the code and not measured.
- ER v2 resolved every person mention; the rules take only those of articles with a
  criminal-case event. The comparison is over that subset.
- Time for ER v2 is quoted from a comment in the code, not measured here.
- Downstream facts (politics, the list, roles, news, sentences) are out of scope and
  were not compared.
