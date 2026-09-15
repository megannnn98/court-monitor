# Real-World Validation v1

## Executive Summary

**Overall status: FAILED_GATES** (exit code 1)

- hard safety gates failed: contradicted_report_claims, gated_dangerous_failures
- quality targets missed: person_extraction_precision, person_extraction_recall, event_precision, event_recall, person_event_association_accuracy, er_candidate_recall_at_5, er_auto_link_precision, political_recall, candidate_recall, semantic_recall_at_5, supported_report_claims_rate
- not run: dangerous_silent_reinterpretation
- PRELIMINARY: 0 VERIFIED golden articles (< 100 required) and 36 DRAFT articles evaluated; no production quality claim can be made
- corpus = 156 real articles; golden draft = 45; golden verified = 0
- hard gates: 10 pass, 2 fail, 1 not run
- DRAFT annotations were written by an AI agent and are not human-verified ground truth.

## Dataset

| item | value |
|---|---|
| corpus period | 2026-03-01 .. 2026-08-31 |
| published range | 2026-03-01 .. 2026-08-31 |
| corpus articles | 156 |
| by source | ovd-info=144, sota-vision=12 |
| by temporal period | T0=109, T1=16, T2=15, T3=16 |
| source status | ovd-info=ok, sota-vision=ok |
| evaluation sample | 156 |
| golden articles (draft / verified) | 45 (45 / 0) |
| golden persons | 129 |
| golden by split (draft/verified) | dev=27/0, validation=9/0, test=9/0 |
| evaluated articles / persons | 36 / 100 |
| namesake cases | 32 |
| retrieval queries | 40 |
| research queries | 30 |
| corpus manifest hash | 05e49c18d2856c71dd975358295a6e07eb0d5890ad418e7d46313e2565e34fc6 |
| golden dataset hash | 8753c6ae0543e1c74ad54b7cece39fbc7ef4932f986f54f327efbb57053314f0 |

## Pipeline

real sources → discovery → ingestion → extraction → Person/Event → ER v2 → persecution classification → Rosfinmonitoring matching → semantic indexing → CandidateQuery → monitoring findings → research workflow → ResearchReport.

| component | version |
|---|---|
| entity_resolution | er-v2 |
| event_extractor | rule-based-event-extractor@1.3.0 |
| extractor | rule-based-entity-extractor@1.1.1 |
| normalizer | rule-based@1.0.0 |
| persecution_classifier | rule-based-persecution-classifier@1.3.0 |
| research_report | 2 |
| rosfinmonitoring_matcher | rule-based-rosfinmonitoring-matcher@1.3.0 |
| semantic_representation | person@2,event@1 |

git commit `31e5508439606caef2e5687e3190331d32d318c1`, split `all` (verified only: False), policy `real-world-policy-v1.1` (36044a1254c5), RF snapshot `rf-eval-v1` (e2615e8e2f66), embedding model `intfloat/multilingual-e5-base`, generated 2026-09-15T10:26:48.524282+00:00.

## Metrics

| metric | value | target | status |
|---|---|---|---|
| person_extraction_precision | 0.8419 | >= 0.97 | FAIL |
| person_extraction_recall | 0.9176 | >= 0.93 | FAIL |
| event_precision | 0.6978 | >= 0.9 | FAIL |
| event_recall | 0.6978 | >= 0.85 | FAIL |
| person_event_association_accuracy | 0.5876 | >= 0.95 | FAIL |
| er_candidate_recall_at_5 | 0.9608 | >= 0.98 | FAIL |
| er_auto_link_precision | 0.9915 | >= 0.995 | FAIL |
| political_precision | 1.0 | >= 0.95 | PASS |
| political_recall | 0.2623 | >= 0.85 | FAIL |
| candidate_precision | 1.0 | >= 0.95 | PASS |
| candidate_recall | 0.2308 | >= 0.8 | FAIL |
| candidate_with_evidence_rate | 1.0 | >= 1.0 | PASS |
| relevant_evidence_rate | 1.0 | >= 0.95 | PASS |
| semantic_recall_at_5 | 0.6719 | >= 0.85 | FAIL |
| supported_report_claims_rate | 0.8805 | >= 0.98 | FAIL |

## Safety Gates

| gate | kind | value | threshold | status | detail |
|---|---|---|---|---|---|
| false_person_auto_link | hard | 0 | <= 0.0 | PASS |  |
| namesake_different_person_auto_link | hard | 0 | <= 0.0 | PASS |  |
| false_rf_not_matched | hard | 0 | <= 0.0 | PASS |  |
| cross_person_persecution_attribution | hard | 0 | <= 0.0 | PASS |  |
| contradicted_report_claims | hard | 76 | <= 0.0 | FAIL |  |
| unsupported_rf_absence_claims | hard | 0 | <= 0.0 | PASS |  |
| dangerous_silent_reinterpretation | hard | — | <= 0.0 | NOT_RUN | natural-language intake not requested (--llm-intake) |
| duplicate_monitoring_findings | hard | 0 | <= 0.0 | PASS |  |
| rerun_duplicates | hard | 0 | <= 0.0 | PASS |  |
| no_snapshot_absence_findings | hard | 0 | <= 0.0 | PASS |  |
| rf_review_status_findings | hard | 0 | <= 0.0 | PASS |  |
| db_invariant_violations | hard | 0 | <= 0.0 | PASS |  |
| gated_dangerous_failures | hard | 38 | <= 0.0 | FAIL | unsupported_event_claim=38 |
| max_manual_reviews_per_100_articles | advisory | 252.56 | <= 20.0 | FAIL | advisory: never fails the evaluation |

## Extraction

Person mentions: tp=245, fp=46, fn=22, precision=0.8419, recall=0.9176, f1=0.8781

Events: tp=97, fp=42, fn=42, precision=0.6978, recall=0.6978, f1=0.6978

Historical events (recall): tp=41, fp=0, fn=10, precision=1.0, recall=0.8039, f1=0.8913

Person-event association accuracy: 0.5876 (57/97 matched events; 0 links to a wrong golden person)

## Entity Resolution

| metric | value |
|---|---|
| mentions evaluated | 245 |
| mentions where a link was expected | 153 |
| candidate recall@1 | 0.8954 |
| candidate recall@5 | 0.9608 |
| AUTO_LINK (correct / all) | 116 / 117 |
| AUTO_LINK precision | 0.9915 |
| AUTO_LINK recall | 0.7582 |
| REVIEW (count / rate) | 35 / 0.1429 |
| false identity links | 0 |
| false create-new | 5 |
| duplicate canonical persons | 5 |

Namesake benchmark: RUN
32 cases {'ambiguous': 3, 'indistinguishable': 3, 'negative': 8, 'positive': 18}; different-person AUTO_LINK = 0; cases=29, indistinguishable_cases=3, cases_with_true_person=18, auto_links=0, correct_auto_links=0, false_links=0, indistinguishable_namesake_links=0, false_link_rate=0.0, auto_link_precision=—, auto_link_recall=0.0, reviews=20, review_rate=0.6897, unnecessary_reviews=14, false_create_new=1, false_create_new_rate=0.0556, missed_reviews=0

## Persecution

Evaluated persons: 100 (unmapped: 12), accuracy 0.54

POLITICAL: tp=16, fp=0, fn=45, precision=1.0, recall=0.2623, f1=0.4156

NON_POLITICAL: tp=18, fp=27, fn=11, precision=0.4, recall=0.6207, f1=0.4865

UNCERTAIN/NEEDS_REVIEW rate: 0.27; cross-person political attribution: 0

| expected \ actual | political | non_political | uncertain | needs_review | not_classified | unmapped_person |
|---|---|---|---|---|---|---|
| political | 16 | 23 | 20 | 0 | 0 | 2 |
| non_political | 0 | 18 | 5 | 0 | 0 | 6 |
| uncertain | 0 | 4 | 2 | 0 | 0 | 4 |

## Rosfinmonitoring

Snapshot `rf-eval-v1`; evaluated 100; accuracy 0.87; **false NOT_MATCHED = 0**; review status accepted instead of expected: 6

| expected \ actual | matched | not_matched | ambiguous | needs_review | insufficient_data | no_match_record | no_snapshot | unmapped_person |
|---|---|---|---|---|---|---|---|---|
| matched | 1 | 0 | 0 | 5 | 0 | 0 | 0 | 1 |
| not_matched | 0 | 78 | 0 | 0 | 0 | 0 | 0 | 4 |
| ambiguous | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| needs_review | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| insufficient_data | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 7 |

## Candidate Query

CandidateQueryService: tp=12, fp=0, fn=40, precision=1.0, recall=0.2308, f1=0.375

Active monitoring findings: tp=12, fp=0, fn=40, precision=1.0, recall=0.2308, f1=0.375

Evidence: 12 candidates checked, with evidence 1.0, relevant 1.0, supports classification 0.6479, traceable 1.0, offsets valid 1.0 (71 spans)

## Semantic Retrieval

Status RUN; model `intfloat/multilingual-e5-base`; 40 queries (36 person, 4 event)

| backend | cases | Recall@5 | Recall@10 | MRR | nDCG@5 |
|---|---|---|---|---|---|
| lexical | 32 | 0.4688 | 0.5938 | 0.3507 | 0.3609 |
| dense | 32 | 0.6719 | 0.7469 | 0.6001 | 0.5902 |
| hybrid | 32 | 0.6594 | 0.7 | 0.5671 | 0.5668 |


Query-level causes: dense_top5_pushed_out_by_hybrid=2, found_in_top5=16, lexical_rescues_dense=2, lexical_worsens_dense_rank=5, relevant_absent_from_candidates=3, relevant_ranked_below_top5=4

| query | type | lexical | dense | hybrid | cause |
|---|---|---|---|---|---|
| rq-01 | person | — | 2 | 7 | dense_top5_pushed_out_by_hybrid |
| rq-02 | person | — | 9 | 19 | relevant_ranked_below_top5 |
| rq-03 | person | — | 1 | 1 | found_in_top5 |
| rq-04 | person | 7 | — | 13 | relevant_ranked_below_top5 |
| rq-05 | person | 2 | 1 | 1 | found_in_top5 |
| rq-06 | person | — | — | — | relevant_absent_from_candidates |
| rq-07 | person | 7 | 11 | 3 | lexical_rescues_dense |
| rq-08 | person | — | — | — | relevant_absent_from_candidates |
| rq-09 | person | 12 | 2 | 1 | found_in_top5 |
| rq-10 | person | 2 | 1 | 2 | lexical_worsens_dense_rank |
| rq-11 | person | 1 | 1 | 1 | found_in_top5 |
| rq-12 | person | 4 | 1 | 2 | lexical_worsens_dense_rank |
| rq-13 | person | — | 12 | 11 | relevant_ranked_below_top5 |
| rq-14 | person | 1 | 1 | 1 | found_in_top5 |
| rq-15 | person | — | 1 | 2 | lexical_worsens_dense_rank |
| rq-17 | person | 8 | 1 | 1 | found_in_top5 |
| rq-18 | person | 2 | 1 | 1 | found_in_top5 |
| rq-19 | person | 4 | 6 | 4 | lexical_rescues_dense |
| rq-20 | person | 2 | 1 | 1 | found_in_top5 |
| rq-21 | person | 7 | 1 | 2 | lexical_worsens_dense_rank |
| rq-22 | person | 5 | 3 | 2 | found_in_top5 |
| rq-23 | event | 1 | 2 | 1 | found_in_top5 |
| rq-25 | event | 1 | 1 | 1 | found_in_top5 |
| rq-26 | person | — | 1 | 5 | lexical_worsens_dense_rank |
| rq-27 | person | 2 | 3 | 1 | found_in_top5 |
| rq-28 | person | — | 4 | — | dense_top5_pushed_out_by_hybrid |
| rq-29 | person | — | — | — | relevant_absent_from_candidates |
| rq-30 | person | 1 | 1 | 1 | found_in_top5 |
| rq-31 | person | 1 | 3 | 2 | found_in_top5 |
| rq-32 | person | 1 | 1 | 1 | found_in_top5 |
| rq-33 | person | 19 | — | — | relevant_ranked_below_top5 |
| rq-34 | event | 3 | 1 | 1 | found_in_top5 |

## Research Reports

30 queries {'main_candidate': 3, 'person_lookup': 5, 'ambiguous_person': 2, 'event': 4, 'date_range': 2, 'source_restriction': 3, 'persecution_status': 2, 'semantic_wording': 3, 'unsupported_criteria': 3, 'rf_status': 3}; workflow completed 26, clarification 0, failed 0
Intake: NOT_RUN (natural-language intake not requested (--llm-intake)); correct 0; clarification 0/0; silent reinterpretation 0

Persons: tp=41, fp=0, fn=46, precision=1.0, recall=0.4713, f1=0.6407

Claims: SUPPORTED=1017, PARTIALLY_SUPPORTED=11, UNSUPPORTED=51, CONTRADICTED=76, NOT_EVALUATED=3304 (24 distinct contradicted facts); supported rate 0.8805; required missing 2; forbidden present 0

Dangerous claim kinds: false_person_link=0, false_political_classification=0, false_rf_not_matched=0, unsupported_absence_claim=0, cross_person_evidence=0, unsupported_event_claim=38, false_actionable_candidate=0

Claim failure causes (occurrences / distinct facts): CONTRADICTED:upstream_persecution_state=76/24, UNSUPPORTED:event_extraction_or_annotation_granularity=38/5, UNSUPPORTED:upstream_persecution_state=12/4, UNSUPPORTED:upstream_rf_state=1/1

## Monitoring E2E

Status RUN

| period | articles | runs | new documents | new persons | new events | new reviews | new classifications | new RF | new findings | semantic indexed | rerun new rows | seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T0 | 109 | ovd-info:completed, sota-vision:completed | 109 | 296 | 385 | 167 | 296 | 296 | 41 | 681 | 0 | 30.063 |
| T1 | 16 | ovd-info:completed, sota-vision:completed | 16 | 62 | 70 | 23 | 62 | 62 | 9 | 132 | 0 | 6.954 |
| T2 | 15 | ovd-info:completed, sota-vision:completed | 15 | 23 | 31 | 26 | 23 | 23 | 0 | 54 | 0 | 3.598 |
| T3 | 16 | ovd-info:completed, sota-vision:completed | 16 | 21 | 25 | 39 | 21 | 21 | 3 | 46 | 0 | 3.69 |

Rerun duplicates: aliases=0, classifications=0, events=0, extraction_runs=0, findings=0, mentions=0, person_event_links=0, persons=0, resolution_decisions=0, rf_results=0, semantic_documents=0, source_documents=0, static_aliases=0, static_classifications=0, static_events=0, static_findings=0, static_mentions=0, static_persons=0, static_resolution_decisions=0, static_rf_results=0, static_semantic_documents=0, static_source_documents=0

Finding timing: on_time=12, late=0, before_evidence=0, missing=40

## Failure Injection

| scenario | status | detail |
|---|---|---|
| rf_review_statuses_not_findings | PASS | 17 persons with an RF review status; 0 reached findings/candidates |
| manual_review_continuation | PASS | decision 6 ('Кирман') → create_new_person; derived run completed: linked=1, classified=1, rf=1, indexed=1, re-ingestion=False |
| together_ai_failure | PASS | workflow failed (llm_unavailable); domain state changed: False |
| crash_after_ingestion | PASS | restart reproduces the uninterrupted state |
| crash_after_extraction | PASS | restart reproduces the uninterrupted state |
| crash_after_entity_resolution | PASS | restart reproduces the uninterrupted state |
| crash_after_classification | PASS | restart reproduces the uninterrupted state |
| crash_after_semantic_indexing | PASS | restart reproduces the uninterrupted state |
| qdrant_outage | PASS | outage runs ['completed_with_errors'], domain rows kept=True, retryable items=2; derived retry completed: indexed 58/58, re-ingestion=False |
| source_failures | PASS | timeout/HTTP 500/malformed: 3 failed, 13 of 13 others ingested, runs ['completed_with_errors']; after the source recovered 16/16 documents (completed,completed) |
| postgres_interruption | PASS | failed article left 0 extraction rows, retryable items=1, 15/15 others extracted, after retry 16/16 (completed_with_errors,completed,completed,completed) |
| no_rf_snapshot | PASS | 4 periods without a snapshot: findings=0, rf matches=0, absence statements=0 |

## Performance

Status RUN; 156 articles, 402 persons in 46.064 s (203.2 articles/min, 523.62 persons/min)

| stage | seconds |
|---|---|
| classification | 4.423 |
| discovery | 0.202 |
| extraction | 3.131 |
| findings | 1.039 |
| ingestion | 2.185 |
| resolution | 21.267 |
| rf_matching | 4.629 |
| semantic_indexing | 7.657 |

## Manual Review Workload

| metric | value |
|---|---|
| articles | 156 |
| er_reviews | 255 |
| er_reviews_per_100_articles | 163.46 |
| persecution_reviews | 122 |
| persecution_reviews_per_100_articles | 78.21 |
| rf_reviews | 17 |
| rf_reviews_per_100_articles | 10.9 |
| total_reviews_per_100_articles | 252.56 |

DB invariants: orphan_person_mentions=0, broken_mention_offsets=0, broken_event_offsets=0, event_links_to_inactive_persons=0, mentions_linked_to_inactive_persons=0, invalid_merge_references=0, duplicate_active_findings=0, duplicate_extraction_identities=0, invalid_rf_snapshot_references=0, findings_with_mismatched_snapshot=0, active_findings_without_not_matched=0, active_findings_without_political=0

## Known Limitations

- Corpus size 156 < target 1000: the existing adapters list only ovd-info=144, sota-vision=12 publications between 2026-03-01 and 2026-08-31.
- Golden annotations are DRAFT until a human verifies them; DRAFT metrics are preliminary.
- The Rosfinmonitoring snapshot is a committed evaluation snapshot built for this corpus (listed names, name variants, ambiguous namesakes), not the real published list.
- Entity resolution metrics only see annotated articles: links into persons from non-annotated articles are evaluated only through the golden person's majority person.
- The persecution classifier and RF matcher are rule-based; birth dates are not extracted.
- Failure-injection scenarios inject controlled exceptions in-process; a killed process (SIGKILL) and a real PostgreSQL restart are not simulated.

## Critical Failures

Failures by component and severity: ENTITY_RESOLUTION: {'S2': 10}; EVENT_ASSOCIATION: {'S2': 40}; EXTRACTION: {'S2': 110}; PERSECUTION: {'S1': 27, 'S2': 59}; REPORT: {'S2': 46, 'S0': 114, 'S1': 13}; RETRIEVAL: {'S2': 42}; ROSFINMONITORING: {'S2': 12, 'S1': 1}

| severity | component | kind | case | person | gated | detail |
|---|---|---|---|---|---|---|
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-02: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-02: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-05: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-yarotsky-vladimir | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-ilichev-vladimir | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shoev-abdulmalik | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-yakusheva-larisa | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-darova-svetlana | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-tukova-anita | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-primak-olga | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-naptugov-aslan | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kambieva-yulia | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-paklin-roman | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-oleynik-nikita | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikulin-andrey | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kurochkina-inna | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-baryakina-elvira | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-snegova-maria | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-bushuev-vyacheslav | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shabanov-andrey | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-snegova-maria | True | rs-10: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-tukova-anita | True | rs-10: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shabanov-andrey | True | rs-10: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-10: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-11: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shabanov-andrey | True | rs-15: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-23: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-23: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-snegova-maria | True | rs-23: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-kambieva-yulia | True | rs-23: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shabanov-andrey | True | rs-23: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-23: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-25: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-yarotsky-vladimir | True | rs-27: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-ilichev-vladimir | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shoev-abdulmalik | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-yakusheva-larisa | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-darova-svetlana | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-tukova-anita | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
... 95 more in the JSON report

## Recommended Next Work

1. **REPORT: research reports state facts the sources contradict** — `contradicted_report_claims` = 76 (target <= 0.0); evidence: claim_contradicted=76, claim_unsupported=51, research_person_missing=46. Possible work: trace each contradicted claim to the component that produced the wrong fact.
2. **DATA_QUALITY: false statements about real persons remain after gating (see per-kind counts)** — `gated_dangerous_failures` = 38 (target <= 0.0); evidence: no itemized failures. Possible work: group the dangerous failures by kind and trace each kind to the component that produced it.
3. **PERSECUTION: expected main candidates are missing from the candidate query** — `candidate_recall` = 0.2308 (target >= 0.8); evidence: persecution_status=46, candidate_missed=40. Possible work: trace each missed candidate to extraction, ER, classification or RF.
4. **PERSECUTION: politically persecuted persons are not classified political** — `political_recall` = 0.2623 (target >= 0.85); evidence: persecution_status=46, candidate_missed=40. Possible work: inspect missed political persons: missing evidence types, review outcomes, unmapped persons.
5. **EVENT_ASSOCIATION: events are linked to the wrong set of persons** — `person_event_association_accuracy` = 0.5876 (target >= 0.95); evidence: event_person_association=40. Possible work: inspect association errors in multi-person sentences and shared events.
