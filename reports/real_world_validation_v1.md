# Real-World Validation v1

## Executive Summary

**Overall status: FAILED_GATES** (exit code 1)

- hard safety gates failed: contradicted_report_claims, gated_dangerous_failures
- quality targets missed: event_precision, event_recall, person_event_association_accuracy, political_recall, candidate_recall, supported_report_claims_rate
- not run: dangerous_silent_reinterpretation, rerun_duplicates, no_snapshot_absence_findings, semantic_recall_at_5
- PRELIMINARY: 0 VERIFIED golden articles (< 100 required) and 36 DRAFT articles evaluated; no production quality claim can be made
- corpus = 156 real articles; golden draft = 45; golden verified = 0
- hard gates: 8 pass, 2 fail, 3 not run
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
| golden dataset hash | 8628af56149456128f040503d045ab851ebcbfeee7b448df97db0cf3fc220c78 |

## Pipeline

real sources → discovery → ingestion → extraction → Person/Event → ER v2 → persecution classification → Rosfinmonitoring matching → semantic indexing → CandidateQuery → monitoring findings → research workflow → ResearchReport.

| component | version |
|---|---|
| entity_resolution | er-v2 |
| event_extractor | rule-based-event-extractor@1.5.0 |
| extractor | rule-based-entity-extractor@1.2.0 |
| normalizer | rule-based@1.1.0 |
| persecution_classifier | rule-based-persecution-classifier@1.3.0 |
| research_report | 2 |
| rosfinmonitoring_matcher | rule-based-rosfinmonitoring-matcher@1.3.0 |
| semantic_representation | person@2,event@1 |

git commit `f31e73303b8dabc6a98afd902973b470139b19d8-dirty`, split `all` (verified only: False), policy `real-world-policy-v1.1` (36044a1254c5), RF snapshot `rf-eval-v1` (e2615e8e2f66), embedding model `—`, generated 2026-09-16T07:34:57.571191+00:00.

## Metrics

| metric | value | target | status |
|---|---|---|---|
| person_extraction_precision | 0.9805 | >= 0.97 | PASS |
| person_extraction_recall | 0.9438 | >= 0.93 | PASS |
| event_precision | 0.797 | >= 0.9 | FAIL |
| event_recall | 0.7114 | >= 0.85 | FAIL |
| person_event_association_accuracy | 0.6415 | >= 0.95 | FAIL |
| er_candidate_recall_at_5 | 0.9931 | >= 0.98 | PASS |
| er_auto_link_precision | 1.0 | >= 0.995 | PASS |
| political_precision | 1.0 | >= 0.95 | PASS |
| political_recall | 0.2623 | >= 0.85 | FAIL |
| candidate_precision | 1.0 | >= 0.95 | PASS |
| candidate_recall | 0.25 | >= 0.8 | FAIL |
| candidate_with_evidence_rate | 1.0 | >= 1.0 | PASS |
| relevant_evidence_rate | 1.0 | >= 0.95 | PASS |
| semantic_recall_at_5 | — | >= 0.85 | NOT_RUN |
| supported_report_claims_rate | 0.8985 | >= 0.98 | FAIL |

## Safety Gates

| gate | kind | value | threshold | status | detail |
|---|---|---|---|---|---|
| false_person_auto_link | hard | 0 | <= 0.0 | PASS |  |
| namesake_different_person_auto_link | hard | 0 | <= 0.0 | PASS |  |
| false_rf_not_matched | hard | 0 | <= 0.0 | PASS |  |
| cross_person_persecution_attribution | hard | 0 | <= 0.0 | PASS |  |
| contradicted_report_claims | hard | 54 | <= 0.0 | FAIL |  |
| unsupported_rf_absence_claims | hard | 0 | <= 0.0 | PASS |  |
| dangerous_silent_reinterpretation | hard | — | <= 0.0 | NOT_RUN | natural-language intake not requested (--llm-intake) |
| duplicate_monitoring_findings | hard | 0 | <= 0.0 | PASS |  |
| rerun_duplicates | hard | — | <= 0.0 | NOT_RUN | repeated runs not executed |
| no_snapshot_absence_findings | hard | — | <= 0.0 | NOT_RUN | scenario no_rf_snapshot not run |
| rf_review_status_findings | hard | 0 | <= 0.0 | PASS |  |
| db_invariant_violations | hard | 0 | <= 0.0 | PASS |  |
| gated_dangerous_failures | hard | 27 | <= 0.0 | FAIL | unsupported_event_claim=27 |
| max_manual_reviews_per_100_articles | advisory | 189.74 | <= 20.0 | FAIL | advisory: never fails the evaluation |

## Extraction

Person mentions: tp=252, fp=5, fn=15, precision=0.9805, recall=0.9438, f1=0.9618

Events: tp=106, fp=27, fn=43, precision=0.797, recall=0.7114, f1=0.7518

Historical events (recall): tp=47, fp=0, fn=11, precision=1.0, recall=0.8103, f1=0.8952

Person-event association accuracy: 0.6415 (68/106 matched events; 0 links to a wrong golden person)

## Entity Resolution

| metric | value |
|---|---|
| mentions evaluated | 252 |
| mentions where a link was expected | 144 |
| candidate recall@1 | 0.9861 |
| candidate recall@5 | 0.9931 |
| AUTO_LINK (correct / all) | 124 / 124 |
| AUTO_LINK precision | 1.0 |
| AUTO_LINK recall | 0.8611 |
| REVIEW (count / rate) | 37 / 0.1468 |
| false identity links | 0 |
| false create-new | 1 |
| duplicate canonical persons | 1 |

Namesake benchmark: RUN
32 cases {'ambiguous': 3, 'indistinguishable': 3, 'negative': 8, 'positive': 18}; different-person AUTO_LINK = 0; cases=29, indistinguishable_cases=3, cases_with_true_person=18, auto_links=0, correct_auto_links=0, false_links=0, indistinguishable_namesake_links=0, false_link_rate=0.0, auto_link_precision=—, auto_link_recall=0.0, reviews=21, review_rate=0.7241, unnecessary_reviews=15, false_create_new=0, false_create_new_rate=0.0, missed_reviews=0

## Persecution

Evaluated persons: 100 (unmapped: 10), accuracy 0.55

POLITICAL: tp=16, fp=0, fn=45, precision=1.0, recall=0.2623, f1=0.4156

NON_POLITICAL: tp=19, fp=28, fn=10, precision=0.4043, recall=0.6552, f1=0.5

UNCERTAIN/NEEDS_REVIEW rate: 0.27; cross-person political attribution: 0

| expected \ actual | political | non_political | uncertain | needs_review | not_classified | unmapped_person |
|---|---|---|---|---|---|---|
| political | 16 | 23 | 20 | 0 | 0 | 2 |
| non_political | 0 | 19 | 5 | 0 | 0 | 5 |
| uncertain | 0 | 5 | 2 | 0 | 0 | 3 |

## Rosfinmonitoring

Snapshot `rf-eval-v1`; evaluated 100; accuracy 0.89; **false NOT_MATCHED = 0**; review status accepted instead of expected: 6

| expected \ actual | matched | not_matched | ambiguous | needs_review | insufficient_data | no_match_record | no_snapshot | unmapped_person |
|---|---|---|---|---|---|---|---|---|
| matched | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 2 |
| not_matched | 0 | 79 | 0 | 0 | 0 | 0 | 0 | 3 |
| ambiguous | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| needs_review | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| insufficient_data | 0 | 1 | 0 | 0 | 3 | 0 | 0 | 5 |

## Candidate Query

CandidateQueryService: tp=13, fp=0, fn=39, precision=1.0, recall=0.25, f1=0.4

Active monitoring findings: tp=13, fp=0, fn=39, precision=1.0, recall=0.25, f1=0.4

Evidence: 13 candidates checked, with evidence 1.0, relevant 1.0, supports classification 0.6914, traceable 1.0, offsets valid 1.0 (81 spans)

## Semantic Retrieval

Status PARTIAL (real embedding model not requested (--semantic-model)); model `—`; 40 queries (36 person, 4 event)

| backend | cases | Recall@5 | Recall@10 | MRR | nDCG@5 |
|---|---|---|---|---|---|
| lexical | 32 | 0.5938 | 0.6875 | 0.4859 | 0.4935 |


Query-level causes: relevant_absent_from_candidates=8, relevant_ranked_below_top5=24

| query | type | lexical | dense | hybrid | cause |
|---|---|---|---|---|---|
| rq-01 | person | — | — | — | relevant_absent_from_candidates |
| rq-02 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-03 | person | — | — | — | relevant_absent_from_candidates |
| rq-04 | person | 6 | — | — | relevant_ranked_below_top5 |
| rq-05 | person | 2 | — | — | relevant_ranked_below_top5 |
| rq-06 | person | — | — | — | relevant_absent_from_candidates |
| rq-07 | person | 7 | — | — | relevant_ranked_below_top5 |
| rq-08 | person | — | — | — | relevant_absent_from_candidates |
| rq-09 | person | 5 | — | — | relevant_ranked_below_top5 |
| rq-10 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-11 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-12 | person | 3 | — | — | relevant_ranked_below_top5 |
| rq-13 | person | — | — | — | relevant_absent_from_candidates |
| rq-14 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-15 | person | — | — | — | relevant_absent_from_candidates |
| rq-16 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-17 | person | 10 | — | — | relevant_ranked_below_top5 |
| rq-18 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-19 | person | 3 | — | — | relevant_ranked_below_top5 |
| rq-20 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-21 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-22 | person | 5 | — | — | relevant_ranked_below_top5 |
| rq-23 | event | 1 | — | — | relevant_ranked_below_top5 |
| rq-25 | event | 1 | — | — | relevant_ranked_below_top5 |
| rq-26 | person | — | — | — | relevant_absent_from_candidates |
| rq-27 | person | 2 | — | — | relevant_ranked_below_top5 |
| rq-28 | person | — | — | — | relevant_absent_from_candidates |
| rq-30 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-31 | person | 1 | — | — | relevant_ranked_below_top5 |
| rq-32 | person | 2 | — | — | relevant_ranked_below_top5 |
| rq-33 | person | 14 | — | — | relevant_ranked_below_top5 |
| rq-34 | event | 2 | — | — | relevant_ranked_below_top5 |

## Research Reports

30 queries {'main_candidate': 3, 'person_lookup': 5, 'ambiguous_person': 2, 'event': 4, 'date_range': 2, 'source_restriction': 3, 'persecution_status': 2, 'semantic_wording': 3, 'unsupported_criteria': 3, 'rf_status': 3}; workflow completed 23, clarification 0, failed 0
Intake: NOT_RUN (natural-language intake not requested (--llm-intake)); correct 0; clarification 0/0; silent reinterpretation 0

Persons: tp=36, fp=0, fn=45, precision=1.0, recall=0.4444, f1=0.6153

Claims: SUPPORTED=894, PARTIALLY_SUPPORTED=8, UNSUPPORTED=39, CONTRADICTED=54, NOT_EVALUATED=2430 (23 distinct contradicted facts); supported rate 0.8985; required missing 2; forbidden present 0

Dangerous claim kinds: false_person_link=0, false_political_classification=0, false_rf_not_matched=0, unsupported_absence_claim=0, cross_person_evidence=0, unsupported_event_claim=27, false_actionable_candidate=0

Claim failure causes (occurrences / distinct facts): CONTRADICTED:upstream_persecution_state=54/23, UNSUPPORTED:event_extraction_or_annotation_granularity=27/5, UNSUPPORTED:upstream_persecution_state=11/5, UNSUPPORTED:upstream_rf_state=1/1

## Monitoring E2E

Status PARTIAL (repeated runs and failure injection need --full)

| period | articles | runs | new documents | new persons | new events | new reviews | new classifications | new RF | new findings | semantic indexed | rerun new rows | seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T0 | 109 | ovd-info:completed, sota-vision:completed | 109 | 234 | 368 | 107 | 234 | 234 | 42 | 602 | not run | 20.283 |
| T1 | 16 | ovd-info:completed, sota-vision:completed | 16 | 54 | 68 | 19 | 54 | 54 | 10 | 122 | not run | 4.578 |
| T2 | 15 | ovd-info:completed, sota-vision:completed | 15 | 12 | 30 | 23 | 12 | 12 | 0 | 42 | not run | 2.363 |
| T3 | 16 | ovd-info:completed, sota-vision:completed | 16 | 18 | 25 | 28 | 18 | 18 | 3 | 43 | not run | 2.755 |

Rerun duplicates: —

Finding timing: on_time=13, late=0, before_evidence=0, missing=39

## Failure Injection

| scenario | status | detail |
|---|---|---|
| rf_review_statuses_not_findings | PASS | 20 persons with an RF review status; 0 reached findings/candidates |

## Performance

Status RUN; 156 articles, 318 persons in 30.102 s (310.94 articles/min, 633.84 persons/min)

| stage | seconds |
|---|---|
| classification | 3.433 |
| discovery | 0.115 |
| extraction | 3.765 |
| findings | 0.476 |
| ingestion | 1.884 |
| resolution | 15.606 |
| rf_matching | 3.331 |
| semantic_indexing | 0.859 |

## Manual Review Workload

| metric | value |
|---|---|
| articles | 156 |
| er_reviews | 177 |
| er_reviews_per_100_articles | 113.46 |
| persecution_reviews | 99 |
| persecution_reviews_per_100_articles | 63.46 |
| rf_reviews | 20 |
| rf_reviews_per_100_articles | 12.82 |
| total_reviews_per_100_articles | 189.74 |

DB invariants: orphan_person_mentions=0, broken_mention_offsets=0, broken_event_offsets=0, event_links_to_inactive_persons=0, mentions_linked_to_inactive_persons=0, invalid_merge_references=0, duplicate_active_findings=0, duplicate_extraction_identities=0, invalid_rf_snapshot_references=0, findings_with_mismatched_snapshot=0, active_findings_without_not_matched=0, active_findings_without_political=0

## Known Limitations

- Corpus size 156 < target 1000: the existing adapters list only ovd-info=144, sota-vision=12 publications between 2026-03-01 and 2026-08-31.
- Golden annotations are DRAFT until a human verifies them; DRAFT metrics are preliminary.
- The Rosfinmonitoring snapshot is a committed evaluation snapshot built for this corpus (listed names, name variants, ambiguous namesakes), not the real published list.
- Entity resolution metrics only see annotated articles: links into persons from non-annotated articles are evaluated only through the golden person's majority person.
- The persecution classifier and RF matcher are rule-based; birth dates are not extracted.
- Failure-injection scenarios inject controlled exceptions in-process; a killed process (SIGKILL) and a real PostgreSQL restart are not simulated.

## Critical Failures

Failures by component and severity: ENTITY_RESOLUTION: {'S2': 2}; EVENT_ASSOCIATION: {'S2': 38}; EXTRACTION: {'S2': 63}; PERSECUTION: {'S1': 28, 'S2': 56}; REPORT: {'S2': 45, 'S0': 81, 'S1': 12}; RETRIEVAL: {'S2': 20}; ROSFINMONITORING: {'S2': 10, 'S1': 1}

| severity | component | kind | case | person | gated | detail |
|---|---|---|---|---|---|---|
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-02: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-05: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-anisimov-dmitry | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
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
| S0 | REPORT | claim_contradicted |  | gp-nikulin-andrey | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kurochkina-inna | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-baryakina-elvira | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-snegova-maria | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-bushuev-vyacheslav | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-25: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-anisimov-dmitry | True | rs-27: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-ilichev-vladimir | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shoev-abdulmalik | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-yakusheva-larisa | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-darova-svetlana | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-tukova-anita | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-primak-olga | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-naptugov-aslan | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-bondarenko-oleg | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kambieva-yulia | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-paklin-roman | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-oleynik-nikita | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikulin-andrey | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kurochkina-inna | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-baryakina-elvira | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-27: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-bushuev-vyacheslav | True | rs-27: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-28: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_unsupported |  | gp-mamporia-oleg | True | rs-01: event: no annotated detention event at this span |
| S0 | REPORT | claim_unsupported |  | gp-kurtnezirov-remzi | True | rs-01: event: no annotated charge event at this span |
| S0 | REPORT | claim_unsupported |  | gp-mamporia-oleg | True | rs-05: event: no annotated detention event at this span |
| S0 | REPORT | claim_unsupported |  | gp-abdulgaziev-tofik | True | rs-05: event: no annotated case_opened event at this span |
| S0 | REPORT | claim_unsupported |  | gp-musaeva-zarema | True | rs-05: event: no annotated sentence event at this span |
| S0 | REPORT | claim_unsupported |  | gp-mamporia-oleg | True | rs-07: event: no annotated detention event at this span |
... 62 more in the JSON report

## Recommended Next Work

1. **REPORT: research reports state facts the sources contradict** — `contradicted_report_claims` = 54 (target <= 0.0); evidence: claim_contradicted=54, research_person_missing=45, claim_unsupported=39. Possible work: trace each contradicted claim to the component that produced the wrong fact.
2. **DATA_QUALITY: false statements about real persons remain after gating (see per-kind counts)** — `gated_dangerous_failures` = 27 (target <= 0.0); evidence: no itemized failures. Possible work: group the dangerous failures by kind and trace each kind to the component that produced it.
3. **PERSECUTION: politically persecuted persons are not classified political** — `political_recall` = 0.2623 (target >= 0.85); evidence: persecution_status=45, candidate_missed=39. Possible work: inspect missed political persons: missing evidence types, review outcomes, unmapped persons.
4. **PERSECUTION: expected main candidates are missing from the candidate query** — `candidate_recall` = 0.25 (target >= 0.8); evidence: persecution_status=45, candidate_missed=39. Possible work: trace each missed candidate to extraction, ER, classification or RF.
5. **EVENT_ASSOCIATION: events are linked to the wrong set of persons** — `person_event_association_accuracy` = 0.6415 (target >= 0.95); evidence: event_person_association=38. Possible work: inspect association errors in multi-person sentences and shared events.
