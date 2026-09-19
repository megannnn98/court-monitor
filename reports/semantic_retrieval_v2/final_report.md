# Semantic Retrieval v2 — final report (PRELIMINARY)

Branch `feat/semantic-retrieval-v2`, 2026-09-19. Experiment code:
`src/evaluation/semantic_v2/`. Raw results: `reports/semantic_retrieval_v2/`;
metric tables: [`comparison.md`](comparison.md).

**Nothing in production was changed.** `DEFAULT_EMBEDDING_MODEL_ID`,
`DEFAULT_DENSE_MIN_SCORE`, the working semantic index, Qdrant and the semantic
document representation (`PERSON_REPRESENTATION_VERSION = 2`,
`EVENT_REPRESENTATION_VERSION = 1`) are untouched. The experiment ran on a
disposable database (`court_monitor_sv2_eval`) rebuilt by replaying the
real-world corpus.

**Status: PRELIMINARY.** Every golden article and every relevance grade is agent
DRAFT; none of it is human-verified. The test split was not run.

---

## 1. Corpus size

- Golden articles: **104** (of the 156 manifest articles), 788 person mentions,
  321 events.
- Retrieval queries: **176** in `evaluation/real_world/retrieval_queries.json`
  (149 PERSON, 27 EVENT).
- Relevance grades: 267 inline query judgments (210 grade 2, 20 grade 1,
  37 explicit grade 0) plus 12 149 pooled DRAFT grades in
  `evaluation/semantic_v2/pool_judgments.json` (10 grade 2, 38 grade 1,
  12 101 explicit grade 0), 13 cross-split grades excluded from metrics and
  2 retired queries.
- 159 queries have at least one relevant entity; 30 are multi-relevant.

## 2. Human-verified vs DRAFT

**0 VERIFIED, 104 DRAFT** (`annotation_status = DRAFT`,
`annotation_origin = agent_draft`, `verified_by = null` on every article). Pool
judgments carry `status: DRAFT`, `annotation_origin: agent_draft`.

Blind review sheets for a human are generated in
`var/real_world/review/semantic_v2/` (`sheet_priority.csv` — pairs inside some
system's top 5, `sheet_full.csv` — every judged pair, `keys.json`, `README.md`).
They show no model, rank, score or earlier grade.

## 3. Split distribution

| | dev | validation | test |
|---|---|---|---|
| golden articles | 60 | 19 | 25 |
| retrieval queries | 117 | 53 | 6 |

Of these, metrics use fewer: pool review retired 2 queries that turned out not to
be clean no-matches (`rq-108`, `rq-148`), and 3 dev queries (`rq-78`, `rq-104`,
`rq-105`) have no relevant entity the pipeline ever extracted. Ranking therefore
runs on 104 dev and 46 validation queries, acceptance additionally on 9 dev and
6 validation negative queries.

Isolation is enforced in code and tests: a query may not have a relevant entity
belonging to another split (`query_problems`), and entities reachable only from
other splits' articles are moved to `cross_split` (unjudged, never negative).
The test split was never loaded during calibration.

## 4. Query classes

| class | dev | validation | test | total |
|---|---|---|---|---|
| PERSON positives | 101 | 42 | 6 | 149 |
| EVENT positives | 16 | 11 | 0 | 27 |
| semantic-only (no shared stem) | 26 | 11 | 0 | 37 |
| group / multi-relevant | 12 | 4 | 2 | 18 |
| no-match (`expected_no_match`) | 10 | 7 | 0 | 17 |
| — of them hard in-domain negatives | 7 | 4 | 0 | 11 |
| — of them off-topic negatives | 3 | 3 | 0 | 6 |

Tags additionally mark `namesake_trap`, `initials`, `public_figure_trap`,
`family`, `journalist`, `inoagent`, `religious`, `death_in_custody`,
`repeated_arrests` and so on, so metrics can be sliced per class.

## 5. E5 ranking metrics

`intfloat/multilingual-e5-base`, dense, candidate pool 100, reranker OFF.

| split / slice | MRR | R@5 | R@10 | R@20 | R@100 | nDCG@5 | nDCG@10 |
|---|---|---|---|---|---|---|---|
| dev all (104) | 0.7766 | 0.8059 | 0.8623 | 0.9053 | 0.9687 | 0.7473 | 0.7672 |
| dev person (92) | 0.7693 | 0.8322 | 0.8797 | 0.9211 | 0.9682 | 0.7644 | 0.7811 |
| dev event (12) | 0.8333 | 0.6042 | 0.7292 | 0.7847 | 0.9722 | 0.6165 | 0.6604 |
| dev semantic-only (26) | 0.6986 | 0.8269 | 0.9167 | 0.9295 | 0.9936 | 0.7075 | 0.7351 |
| dev group (10) | 0.7769 | 0.6893 | 0.7679 | 0.7821 | 0.9078 | 0.6522 | 0.6819 |
| validation all (46) | 0.6932 | 0.7536 | 0.7859 | 0.8330 | 0.9690 | 0.6739 | 0.6824 |
| validation person (37) | 0.6809 | 0.7972 | 0.8149 | 0.8555 | 0.9705 | 0.6941 | 0.6952 |
| validation event (9) | 0.7441 | 0.5741 | 0.6667 | 0.7407 | 0.9630 | 0.5908 | 0.6300 |

Two conventions behind these numbers. `coverage@k`, reported next to every
ranking metric in `comparison.md`, is the **graded share of the top k**, not a
recall-style coverage of relevant items: it says how much of what a system
returned was actually judged (0.55–0.63 at k = 100, ~0.99 at k = 10). And nDCG
counts an unjudged entity as gain 0, so it is a lower bound; the reports also
carry `ndcg@k_condensed`, computed over judged entities only, and the gap is
negligible on this corpus — dev nDCG@10 0.7672 vs 0.7683 (E5) and 0.8112 vs
0.8126 (BGE-M3). Recall@k and precision never treat an unjudged entity as an
error.

Hybrid (RRF with the lexical retriever) is much worse than dense for both models
on this corpus — dev all MRR 0.6187 for E5 hybrid vs 0.7766 dense, and on
semantic-only queries hybrid collapses (MRR 0.1187) because the lexical arm
contributes only noise. Lexical alone: dev all MRR 0.5005, semantic-only 0.0109.

## 6. BGE-M3 ranking metrics

`BAAI/bge-m3`, dense only (no sparse, no ColBERT), its own preprocessing profile
(no `query:` / `passage:` prefixes, CLS pooling, `max_seq_length` 512, dimension
1024 read from the model).

| split / slice | MRR | R@5 | R@10 | R@20 | R@100 | nDCG@5 | nDCG@10 |
|---|---|---|---|---|---|---|---|
| dev all (104) | 0.8183 | 0.8481 | 0.8768 | 0.9192 | 0.9820 | 0.8000 | 0.8112 |
| dev person (92) | 0.8087 | 0.8627 | 0.8951 | 0.9385 | 0.9896 | 0.8096 | 0.8229 |
| dev event (12) | 0.8917 | 0.7361 | 0.7361 | 0.7708 | 0.9236 | 0.7262 | 0.7219 |
| dev semantic-only (26) | 0.8283 | 0.8846 | 0.9231 | 0.9808 | 0.9936 | 0.8269 | 0.8392 |
| dev group (10) | 0.7811 | 0.7286 | 0.7769 | 0.8094 | 0.9295 | 0.6399 | 0.6686 |
| validation all (46) | 0.6883 | 0.7166 | 0.7634 | 0.8178 | 0.9783 | 0.6760 | 0.6869 |
| validation person (37) | 0.7209 | 0.7918 | 0.8365 | 0.8636 | 1.0000 | 0.7243 | 0.7352 |
| validation event (9) | 0.5541 | 0.4074 | 0.4630 | 0.6296 | 0.8889 | 0.4776 | 0.4885 |

Paired bootstrap (95% CI of the mean per-query difference, BGE-M3 dense −
E5 dense):

| split | MRR | Recall@10 | nDCG@10 |
|---|---|---|---|
| dev (104) | +0.0416 [−0.0073, +0.0946] | +0.0145 [−0.0303, +0.0585] | +0.0440 [+0.0044, +0.0893] |
| validation (46) | −0.0049 [−0.0821, +0.0736] | −0.0225 [−0.0913, +0.0449] | +0.0045 [−0.0528, +0.0638] |

Only one interval excludes zero (dev nDCG@10, barely). On validation the models
are indistinguishable.

## 7. New E5 threshold

**0.80 — unchanged.** The selection rule from the task ("maximum recall while
precision, negative rejection and hard-negative false positives are no worse
than the current E5 @ 0.80 baseline on dev") returns the baseline itself for E5
by construction: lowering the threshold destroys precision, raising it costs
recall, so no other point is feasible under a rule anchored to E5 @ 0.80.

The dev sweep shows how sharp the useful band is:

| E5 threshold | recall (micro) | precision (judged) | accepted unjudged | negative rejection | hard-negative FP |
|---|---|---|---|---|---|
| 0.78 | 0.9162 | 0.0243 | 3198 | 0.2222 | 438 |
| **0.80 (production)** | 0.8503 | 0.0324 | 1608 | 0.5556 | 280 |
| 0.82 | 0.6707 | 0.1302 | 58 | 0.6667 | 26 |
| 0.84 | 0.3832 | 0.6038 | 2 | 0.8889 | 1 |
| 0.86 | 0.1976 | 0.9167 | 0 | 1.0 | 0 |

So a real recalibration decision for E5 is a *policy* choice between 0.80
(recall 0.85, almost no rejection) and 0.82 (recall 0.67, 4× precision,
hard-negative FP 280 → 26), not something the current rule can pick on its own.

## 8. New BGE-M3 threshold

**0.47**, selected on dev only and frozen before validation. BGE-M3 similarity
lives on a different scale, so 0.80 is meaningless for it (recall 0 above 0.7).

| BGE-M3 threshold | recall (micro) | precision (judged) | accepted unjudged | negative rejection | hard-negative FP |
|---|---|---|---|---|---|
| 0.40 | 0.9222 | 0.0334 | 2534 | 0.1111 | 236 |
| 0.45 | 0.8383 | 0.0554 | 208 | 0.3333 | 71 |
| **0.47 (selected)** | 0.7425 | 0.0870 | 78 | 0.5556 | 38 |
| 0.50 | 0.6527 | 0.2034 | 14 | 0.6667 | 18 |
| 0.55 | 0.4132 | 0.5897 | 0 | 1.0 | 0 |

## 9. Acceptance precision / recall

| split | configuration | recall micro | recall macro | precision (judged) | accepted unjudged | judged share of accepted |
|---|---|---|---|---|---|---|
| dev | E5 @ 0.80 (baseline = recalibrated) | 0.8503 | 0.9254 | 0.0324 | 1608 | 0.7316 |
| dev | BGE-M3 @ 0.47 | 0.7425 | 0.8351 | 0.0870 | 78 | 0.9481 |
| validation | E5 @ 0.80 dense | 0.8846 | 0.9230 | 0.0382 | 625 | 0.7431 |
| validation | BGE-M3 @ 0.47 dense | 0.6667 | 0.7603 | 0.0870 | 36 | 0.9432 |

Precision is judged-only: unjudged entities are never counted as errors, so
these values are a lower bound and they depend on how much of the accepted set
was graded.

**Two configurations are only comparable at a similar judged share.** On dev the
accepted sets are:

| configuration | accepted per query | accepted total | judged | unjudged | judged share | precision (judged) | recall micro |
|---|---|---|---|---|---|---|---|
| E5 @ 0.80 | 56.8 | 6082 | 4464 | 1618 | 0.7316 | 0.0324 | 0.8503 |
| BGE-M3 @ 0.47 | 14.2 | 1516 | 1438 | 78 | 0.9481 | 0.0870 | 0.7425 |
| E5 @ 0.82 | 8.7 | 926 | 868 | 58 | 0.9368 | 0.1302 | 0.6707 |

(Accepted counts are over the 107 dev queries that have a judgment; the metric
tables above count the 104 with a reachable relevant entity.)

So E5 @ 0.80 vs BGE-M3 @ 0.47 is **not** an apples-to-apples precision
comparison — a quarter of E5's accepted set is ungraded and could be either.
E5 @ 0.82 vs BGE-M3 @ 0.47 is a fair one (judged share 0.94 vs 0.95), and there
E5 has the higher precision at a comparable recall. This is why the
recommendation below rests on that pair and not on the raw 0.032 vs 0.087.

## 10. Negative rejection

Identical for both models at their selected thresholds: **dev 0.5556** (5 of 9
no-match queries return nothing), **validation 0.5000** (of 6 negative queries
usable after pooling). The remaining negative queries accept at least one entity
under both models.

## 11. Hard-negative false positives

Accepted entities on hard in-domain negatives, dev: **E5 @ 0.80 — 280**,
BGE-M3 @ 0.47 — **38**. Validation: E5 147, BGE-M3 8. Off-topic negatives:
**0 false positives for both models on both splits** — the off-topic case
(the old "задержание кометы телескопом" class) is solved by either model, the
hard in-domain case is not solved by either.

## 12. Research Workflow results

Deterministic `PreparedRequestParser` (no LLM), real `ResearchPlanner`,
retrieval, `DenseSimilarityRelevancePolicy`, `ResearchService` and report
builder, hybrid retrieval plus acceptance:

| split | configuration | recall micro | recall macro | precision (judged) | returned median | 0-result positive queries | negative persons returned | p50 latency |
|---|---|---|---|---|---|---|---|---|
| dev | E5 @ 0.80 | 0.8561 | 0.9327 | 0.0328 | 49.5 | 1 | 180 | 55.9 ms |
| dev | E5 recalibrated @ 0.80 | 0.8561 | 0.9327 | 0.0328 | 49.5 | 1 | 180 | 52.0 ms |
| dev | BGE-M3 @ 0.47 | 0.7576 | 0.8489 | 0.0860 | 8.0 | 1 | 20 | 41.8 ms |
| validation | E5 @ 0.80 | 0.8644 | 0.9250 | 0.0408 | 47 | 0 | 71 | 49.0 ms |
| validation | BGE-M3 @ 0.47 | 0.6780 | 0.7786 | 0.0840 | 11 | 1 | 4 | 41.2 ms |

Per-query comparison (queries whose relevant entities are reachable at all,
140 in total):

- **recall (every relevant person returned; a negative query returns nothing)** —
  the primary reading: E5 correct / BGE wrong **15**, BGE correct / E5 wrong
  **1**, both wrong **17**, both correct **107**. E5 answers 122 of 140 queries
  completely, BGE-M3 108;
- strict (recall **and** no judged non-relevant person returned): E5 correct /
  BGE wrong **0**, BGE correct / E5 wrong **10**, both wrong **120**, both
  correct **10**.

The strict rule is reported for completeness but must not be read as a ranking
verdict: it is dominated by the width of the result set, not by ranking quality.
E5 @ 0.80 returns a median of ~48 persons per query out of a corpus of 314, so
one of the 12 101 explicitly graded-0 entities is almost always among them and
the query is scored wrong even when every relevant person is at the top. A
configuration can improve this number by returning less, which is exactly what
BGE-M3 @ 0.47 does (median 8). The
same research benchmark (`research_benchmark_semantic_cases`) gives identical
person precision/recall for all three configurations (tp 4, fp 0, fn 2,
F1 0.80); BGE-M3 produces slightly fewer supported claims (440 vs 484) because
fewer persons reach the report.

## 13. Where BGE-M3 wins

- dev ranking overall: MRR +0.042, nDCG@10 +0.044 (the only CI that excludes
  zero);
- dev semantic-only queries: MRR 0.828 vs 0.699, nDCG@10 0.839 vs 0.735 — the
  class that motivated semantic retrieval in the first place;
- dev EVENT queries: MRR 0.892 vs 0.833;
- validation PERSON recall@100 1.000 vs 0.9705 (every relevant person is inside
  the candidate pool);
- acceptance: at the calibrated threshold it accepts ~20× fewer unjudged
  entities and 7× fewer hard-negative entities, and its downstream reports are
  short enough to read (median 8–11 persons vs 47–50).

## 14. Where E5 wins

- validation ranking: recall@5/@10 higher (0.7536/0.7859 vs 0.7166/0.7634), MRR
  a hair higher — the dev advantage of BGE-M3 does not reproduce;
- validation EVENT queries: MRR 0.744 vs 0.554, recall@10 0.667 vs 0.463;
- validation group queries: MRR 0.875 vs 0.646;
- acceptance recall at the calibrated thresholds: 0.885 vs 0.667 (validation);
- downstream recall: 0.864 vs 0.678 (validation), and 15 queries where only E5
  returns every relevant person (BGE-M3-only: 1);
- cost: 3× faster embedding, half the VRAM, 1.8× lower query latency, 25 %
  smaller vectors, and it is the model the production index is already built
  with.

## 15. Performance and VRAM (RTX 3060, same 805 documents)

| | E5 base | BGE-M3 |
|---|---|---|
| dimension | 768 | 1024 |
| model load | 13.25 s | 13.99 s (cold download 166 s) |
| corpus embedding | 3.15 s | 9.66 s |
| documents/s | 255.4 | 83.4 |
| full rebuild (embed + store) | 4.18 s | 11.04 s |
| incremental batch (16 docs) | 0.098 s | 0.241 s |
| query embedding p50/p95 | 7.55 / 9.10 ms | 13.72 / 16.28 ms |
| dense PERSON retrieval p50/p95 | 11.06 / 12.79 ms | 17.20 / 20.62 ms |
| dense EVENT retrieval p50/p95 | 11.95 / 13.18 ms | 18.36 / 21.05 ms |
| VRAM peak | 1605 MiB | 2886 MiB |
| RAM peak | 2518 MiB | 3625 MiB |
| vector bytes / vector | 3 080 B | 4 104 B |
| EVENT HNSW index | 2.07 MB | 4.10 MB |

pgvector preflight for 1024 dimensions: vectors stay inline
(`rows_out_of_line = 0` with `STORAGE PLAIN`), the partial HNSW index
`ix_semvec_hnsw_events_semantic_1024` is created and PERSON exact search and
EVENT HNSW both return a full top-100. No schema change is needed for 1024 d;
the row-size limit (~2000 d) is still the open item L-3 from ADR 0018.

## 16. Cost of a full rebuild

Extrapolated from the measured throughput to the working corpus of ADR 0018
(8 724 persons + 16 797 events = 25 521 documents):

| | E5 base | BGE-M3 |
|---|---|---|
| embedding | ~100 s | ~306 s |
| pgvector writes | ~66 s | ~88 s |
| total full rebuild | ~2.8 min | ~6.6 min |
| vector storage | ~79 MB | ~105 MB |

The write estimate for E5 is the ADR 0018 measurement on the working corpus;
BGE-M3's is that number scaled by the measured write ratio on the stand
(1.38 s vs 1.03 s for the same 805 documents, i.e. ×1.34), because its vectors
are a third larger.

Switching the model forces exactly one such full rebuild (an incremental run
after a model change is refused by `IndexModelMismatchError`), plus a second
one if the switch is reverted.

## 17. Problems found

1. **The threshold selection rule cannot recalibrate E5.** Anchored to E5 @ 0.80
   on dev, it can only return that point. The real E5 decision (0.80 vs 0.82) is
   a precision/recall policy choice and needs a stated target, not a rule that
   copies the baseline.
2. **Acceptance precision is dominated by unjudged candidates.** E5 @ 0.80
   accepts 6082 entities on dev (56.8 per query), 1618 of them ungraded;
   `precision_judged` = 0.032 is therefore computed on 73 % of the accepted set,
   and comparing it to BGE-M3's 0.087 (95 % graded) is not apples to apples.
   Only configurations with a similar judged share may be compared directly —
   E5 @ 0.82 (0.94) against BGE-M3 @ 0.47 (0.95).
3. **Hard in-domain negatives are not solved by either model.** Rejection rate
   0.56 dev / 0.50 validation for both; a cosine threshold on a dense vector
   cannot separate "a detained journalist who does not exist in the corpus" from
   the detained journalists who do.
4. **Hybrid (RRF) hurts on this corpus**, badly on semantic-only queries
   (MRR 0.12–0.16 vs 0.70–0.83 dense) — yet the Research Workflow uses hybrid.
   That is a bigger, cheaper win than changing the embedding model.
5. **The test split is nearly empty for retrieval**: 6 queries, no negatives, no
   EVENT and no semantic-only cases. The planned single final run on test would
   prove almost nothing; test queries must be added (they can be written without
   looking at any result).
6. **The dev advantage of BGE-M3 does not reproduce on validation**, which is
   what a 104-article DRAFT corpus is expected to do: the splits are small
   (46 usable validation queries) and the grades are unverified.
7. **Pooling bias, quantified.** Candidates come from the union of both models'
   dense and hybrid runs plus lexical, so no single model defines the pool, but
   an entity that no system ever retrieves is never judged. Across all queries
   799 distinct entity keys are graded, against 805 documents in the stand, so
   entity-level coverage is near complete; per query it is much thinner — the
   graded share of a top-100 is 0.55–0.63 (`coverage@100`). Recall@100 is
   therefore a lower bound, and a model that surfaces a more unusual set of
   entities is mildly favoured.
8. **All grades are agent DRAFT.** The agent graded candidates pooled from its
   own runs; every number above inherits that bias, which is exactly why the
   corpus is not "locked" yet.

## 18. Recommendation

**Keep E5 (`intfloat/multilingual-e5-base`) and keep `SEMANTIC_DENSE_MIN_SCORE`
at 0.80 for now. Do not switch to BGE-M3.**

Reasons: the BGE-M3 ranking advantage appears only on dev and disappears on
validation; the acceptance advantage is a threshold-scale artefact that the E5
sweep reproduces at 0.82 with *better* precision (0.130 vs 0.087) at a
comparable recall (0.671 vs 0.743) and at a comparable judged share (0.94 vs
0.95, so the two numbers may be compared); downstream, E5 answers 122 of 140
queries completely against BGE-M3's 108; and BGE-M3 costs 3× the embedding time,
1.8× the VRAM and 1.8× the query latency, plus a forced full rebuild.

The evidence does support a separate, cheaper line of work, in this order:

1. get the golden corpus and the pooled grades human-verified (the review sheets
   are ready) — everything else rests on it;
2. decide the acceptance policy for E5 explicitly: 0.80 (recall 0.85, ~48
   persons per report) or 0.82 (recall 0.67, 4× precision, 10× fewer
   hard-negative acceptances). This is a product decision about what a research
   report should contain;
3. fix the Research Workflow's use of hybrid retrieval — dense beats hybrid on
   every slice here;
4. only then reconsider BGE-M3, on the verified corpus, with a larger test
   split, and possibly with its sparse/ColBERT modes, which this experiment
   deliberately did not touch.

## 19. Confidence

**PRELIMINARY.** 0 of 104 golden articles and 0 of 12 416 grades are
human-verified; the test split was not run. The recommendation "keep E5" is the
conservative reading of the data and is additionally supported by cost, so it is
unlikely to flip; the *threshold* question (0.80 vs 0.82) genuinely depends on
verification, because it is decided by how many of the 1608 unjudged accepted
entities turn out to be relevant.

## 20. What production would need if a switch were approved later

Nothing here should be done now. If a separate GO is ever given for BGE-M3:

1. `src/semantic_retrieval/embeddings.py`: `DEFAULT_EMBEDDING_MODEL_ID =
   "BAAI/bge-m3"`, and its embedding profile (empty query/document prefixes,
   CLS pooling, `max_seq_length` 512) — already implemented and covered by tests
   in commit `ebac5d4`;
2. `DEFAULT_DENSE_MIN_SCORE` 0.80 → the calibrated BGE-M3 value (0.47 on this
   DRAFT corpus; it must be re-derived on the verified corpus before any switch);
3. `SEMANTIC_DENSE_MIN_SCORE` in `compose.yaml` / deployment env, since the
   default is model-specific and the settings layer requires it for a non-default
   model;
4. a full `rebuild-semantic-index --entity all` (~6.6 min for the working corpus):
   an incremental run is refused by `IndexModelMismatchError`. For pgvector the
   1024-dimension collections get their own partial HNSW index; `STORAGE PLAIN`
   keeps the rows inline, verified in preflight;
5. VRAM budget on the inference host: peak 2.9 GB vs 1.6 GB today;
6. `docs/wiki/Semantic-Retrieval.md` (model, threshold, latency table) and a new
   ADR recording the decision.

---

## Review notes

An independent static review of the branch was run after the first version of
this report. What it changed here:

- acceptance precision is now reported with the accepted-set sizes and judged
  shares (§9), and the recommendation explicitly rests on the pair with a
  comparable judged share (E5 @ 0.82 vs BGE-M3 @ 0.47), not on the raw
  0.032 vs 0.087;
- the downstream comparison now leads with the recall rule and states why the
  strict rule is dominated by result-set width (§12);
- the nDCG convention (unjudged counts as gain 0, condensed variant, gap
  ≤ 0.0016) and the meaning of `coverage@k` are spelled out (§5);
- the pooling bias is quantified: 799 distinct entity keys graded against 805
  stand documents, but only 0.55–0.63 of a top-100 graded per query (§17);
- the BGE-M3 rebuild cost was corrected — its pgvector writes are ×1.34 E5's,
  so a full rebuild is ~6.6 min, not ~6.2 (§16);
- `comparison.md` no longer prints the same acceptance result under two names.

Two review claims did not survive checking: the accepted-set sizes quoted there
(2194 for E5 @ 0.80, 903 for BGE-M3 @ 0.47 on dev) are wrong — the measured
values are 6082 and 1516; and the concern that `query_problems` mishandles a
person whose articles span several splits is moot, because this corpus has none
(and the check is deliberately conservative, since a leak is worse than a false
alarm). The suggestion to rename `coverage@k` was not taken: it would rewrite
every committed artefact for a cosmetic gain, so the metric is defined in §5
instead.

## Reproducing

```bash
# 0. disposable database on the pgvector container; never the working database
export SV2_DB=postgresql+psycopg://court_monitor:***@127.0.0.1:5434/court_monitor_sv2_eval

# 1. does the model fit the GPU and pgvector at its real dimension
PYTHONPATH=src uv run --group semantic python -m evaluation.semantic_v2.preflight \
    --database-url $SV2_DB --model BAAI/bge-m3

# 2. replay the corpus into the stand, index it once per model
PYTHONPATH=src uv run python -m evaluation.semantic_v2.stand replay --database-url $SV2_DB
PYTHONPATH=src uv run --group semantic python -m evaluation.semantic_v2.stand index \
    --database-url $SV2_DB --model intfloat/multilingual-e5-base
PYTHONPATH=src uv run --group semantic python -m evaluation.semantic_v2.stand index \
    --database-url $SV2_DB --model BAAI/bge-m3

# 3. rank, pool candidates for review, merge DRAFT marks, evaluate
PYTHONPATH=src uv run --group semantic python -m evaluation.semantic_v2.retrieval rank --database-url $SV2_DB
PYTHONPATH=src uv run python -m evaluation.semantic_v2.retrieval pool --database-url $SV2_DB --sheet pool_sheet.txt
PYTHONPATH=src uv run python -m evaluation.semantic_v2.retrieval judge --database-url $SV2_DB --marks marks.jsonl
PYTHONPATH=src uv run python -m evaluation.semantic_v2.retrieval evaluate --database-url $SV2_DB
PYTHONPATH=src uv run python -m evaluation.semantic_v2.retrieval review --database-url $SV2_DB

# 4. downstream workflow, cost, committed comparison
PYTHONPATH=src uv run --group semantic python -m evaluation.semantic_v2.downstream --database-url $SV2_DB
PYTHONPATH=src uv run --group semantic python -m evaluation.semantic_v2.perf --database-url $SV2_DB --model ...
PYTHONPATH=src uv run python -m evaluation.semantic_v2.report
```

Steps 3–4 (`evaluate`, `report`) are deterministic: given the ranked runs, they
reproduce `dev.json`, `validation.json`, both threshold sweeps,
`comparison.json` and `comparison.md` byte for byte (verified 2026-09-19).

The ranked runs themselves (`reports/semantic_retrieval_v2/runs/*.json`, 5.4 MB)
are not committed — the repository refuses files above 500 KB — so reproducing
the reports from scratch needs step 2 and `retrieval rank` on a GPU. Everything
derived from them is committed.
