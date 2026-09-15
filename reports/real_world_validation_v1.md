# Real-World Validation v1

## Executive Summary

**Overall status: FAILED_GATES** (exit code 1)

- hard safety gates failed: namesake_different_person_auto_link, cross_person_persecution_attribution, contradicted_report_claims, gated_dangerous_failures
- quality targets missed: person_extraction_precision, person_extraction_recall, event_precision, event_recall, person_event_association_accuracy, er_candidate_recall_at_5, political_precision, political_recall, candidate_precision, candidate_recall, semantic_recall_at_5, supported_report_claims_rate
- not run: dangerous_silent_reinterpretation
- PRELIMINARY: 0 VERIFIED golden articles (< 100 required) and 36 DRAFT articles evaluated; no production quality claim can be made
- corpus = 156 real articles; golden draft = 45; golden verified = 0
- hard gates: 8 pass, 4 fail, 1 not run
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
| event_extractor | rule-based-event-extractor@1.0.0 |
| extractor | rule-based-entity-extractor@1.0.0 |
| normalizer | rule-based@1.0.0 |
| persecution_classifier | rule-based-persecution-classifier@1.2.0 |
| research_report | 1 |
| rosfinmonitoring_matcher | rule-based-rosfinmonitoring-matcher@1.2.0 |
| semantic_representation | person@1,event@1 |

git commit `16b6172e93383d2128ac2735591354523a874250-dirty`, split `all` (verified only: False), policy `real-world-policy-v1.1` (36044a1254c5), RF snapshot `rf-eval-v1` (e2615e8e2f66), embedding model `intfloat/multilingual-e5-base`, generated 2026-09-15T08:24:59.881250+00:00.

## Metrics

| metric | value | target | status |
|---|---|---|---|
| person_extraction_precision | 0.6298 | >= 0.97 | FAIL |
| person_extraction_recall | 0.427 | >= 0.93 | FAIL |
| event_precision | 0.5638 | >= 0.9 | FAIL |
| event_recall | 0.6043 | >= 0.85 | FAIL |
| person_event_association_accuracy | 0.369 | >= 0.95 | FAIL |
| er_candidate_recall_at_5 | 0.8846 | >= 0.98 | FAIL |
| er_auto_link_precision | 1.0 | >= 0.995 | PASS |
| political_precision | 0.8065 | >= 0.95 | FAIL |
| political_recall | 0.4098 | >= 0.85 | FAIL |
| candidate_precision | 0.7407 | >= 0.95 | FAIL |
| candidate_recall | 0.3846 | >= 0.8 | FAIL |
| candidate_with_evidence_rate | 1.0 | >= 1.0 | PASS |
| relevant_evidence_rate | 1.0 | >= 0.95 | PASS |
| semantic_recall_at_5 | 0.3871 | >= 0.85 | FAIL |
| supported_report_claims_rate | 0.8498 | >= 0.98 | FAIL |

## Safety Gates

| gate | kind | value | threshold | status | detail |
|---|---|---|---|---|---|
| false_person_auto_link | hard | 0 | <= 0.0 | PASS |  |
| namesake_different_person_auto_link | hard | 2 | <= 0.0 | FAIL |  |
| false_rf_not_matched | hard | 0 | <= 0.0 | PASS |  |
| cross_person_persecution_attribution | hard | 5 | <= 0.0 | FAIL |  |
| contradicted_report_claims | hard | 122 | <= 0.0 | FAIL |  |
| unsupported_rf_absence_claims | hard | 0 | <= 0.0 | PASS |  |
| dangerous_silent_reinterpretation | hard | — | <= 0.0 | NOT_RUN | natural-language intake not requested (--llm-intake) |
| duplicate_monitoring_findings | hard | 0 | <= 0.0 | PASS |  |
| rerun_duplicates | hard | 0 | <= 0.0 | PASS |  |
| no_snapshot_absence_findings | hard | 0 | <= 0.0 | PASS |  |
| rf_review_status_findings | hard | 0 | <= 0.0 | PASS |  |
| db_invariant_violations | hard | 0 | <= 0.0 | PASS |  |
| gated_dangerous_failures | hard | 74 | <= 0.0 | FAIL | cross_person_evidence=7, false_actionable_candidate=7, false_political_classification=40, unsupported_event_claim=20 |
| max_manual_reviews_per_100_articles | advisory | 151.92 | <= 20.0 | FAIL | advisory: never fails the evaluation |

## Extraction

Person mentions: tp=114, fp=67, fn=153, precision=0.6298, recall=0.427, f1=0.5089

Events: tp=84, fp=65, fn=55, precision=0.5638, recall=0.6043, f1=0.5833

Historical events (recall): tp=31, fp=0, fn=20, precision=1.0, recall=0.6078, f1=0.7561

Person-event association accuracy: 0.369 (31/84 matched events; 1 links to a wrong golden person)

## Entity Resolution

| metric | value |
|---|---|
| mentions evaluated | 114 |
| mentions where a link was expected | 26 |
| candidate recall@1 | 0.6923 |
| candidate recall@5 | 0.8846 |
| AUTO_LINK (correct / all) | 11 / 11 |
| AUTO_LINK precision | 1.0 |
| AUTO_LINK recall | 0.4231 |
| REVIEW (count / rate) | 18 / 0.1579 |
| false identity links | 0 |
| false create-new | 7 |
| duplicate canonical persons | 7 |

Namesake benchmark: RUN
32 cases {'ambiguous': 3, 'indistinguishable': 3, 'negative': 8, 'positive': 18}; different-person AUTO_LINK = 2; cases=29, indistinguishable_cases=3, cases_with_true_person=18, auto_links=5, correct_auto_links=5, false_links=0, indistinguishable_namesake_links=2, false_link_rate=0.0, auto_link_precision=1.0, auto_link_recall=0.2778, reviews=15, review_rate=0.5172, unnecessary_reviews=9, false_create_new=1, false_create_new_rate=0.0556, missed_reviews=0

## Persecution

Evaluated persons: 100 (unmapped: 19), accuracy 0.46

POLITICAL: tp=25, fp=6, fn=36, precision=0.8065, recall=0.4098, f1=0.5435

NON_POLITICAL: tp=13, fp=27, fn=16, precision=0.325, recall=0.4483, f1=0.3768

UNCERTAIN/NEEDS_REVIEW rate: 0.1; cross-person political attribution: 5

| expected \ actual | political | non_political | uncertain | needs_review | not_classified | unmapped_person |
|---|---|---|---|---|---|---|
| political | 25 | 25 | 6 | 0 | 0 | 5 |
| non_political | 5 | 13 | 3 | 0 | 0 | 8 |
| uncertain | 1 | 2 | 1 | 0 | 0 | 6 |

## Rosfinmonitoring

Snapshot `rf-eval-v1`; evaluated 100; accuracy 0.79; **false NOT_MATCHED = 0**; review status accepted instead of expected: 8

| expected \ actual | matched | not_matched | ambiguous | needs_review | insufficient_data | no_match_record | no_snapshot | unmapped_person |
|---|---|---|---|---|---|---|---|---|
| matched | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 |
| not_matched | 0 | 70 | 0 | 0 | 0 | 0 | 0 | 12 |
| ambiguous | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| needs_review | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| insufficient_data | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 7 |

## Candidate Query

CandidateQueryService: tp=20, fp=7, fn=32, precision=0.7407, recall=0.3846, f1=0.5063

Active monitoring findings: tp=20, fp=7, fn=32, precision=0.7407, recall=0.3846, f1=0.5063

Evidence: 27 candidates checked, with evidence 1.0, relevant 1.0, supports classification 0.5641, traceable 1.0, offsets valid 1.0 (48 spans)

## Semantic Retrieval

Status RUN; model `intfloat/multilingual-e5-base`; 40 queries (36 person, 4 event)

| backend | cases | Recall@5 | Recall@10 | MRR | nDCG@5 |
|---|---|---|---|---|---|
| lexical | 31 | 0.3226 | 0.3548 | 0.212 | 0.2362 |
| dense | 31 | 0.3871 | 0.433 | 0.2723 | 0.2874 |
| hybrid | 31 | 0.3548 | 0.4516 | 0.3175 | 0.3107 |

## Research Reports

30 queries {'main_candidate': 3, 'person_lookup': 5, 'ambiguous_person': 2, 'event': 4, 'date_range': 2, 'source_restriction': 3, 'persecution_status': 2, 'semantic_wording': 3, 'unsupported_criteria': 3, 'rf_status': 3}; workflow completed 26, clarification 0, failed 0
Intake: NOT_RUN (natural-language intake not requested (--llm-intake)); correct 0; clarification 0/0; silent reinterpretation 0

Persons: tp=29, fp=0, fn=58, precision=1.0, recall=0.3333, f1=0.5

Claims: SUPPORTED=911, PARTIALLY_SUPPORTED=6, UNSUPPORTED=33, CONTRADICTED=122, NOT_EVALUATED=3987 (32 distinct contradicted facts); supported rate 0.8498; required missing 2; forbidden present 1

Dangerous claim kinds: false_person_link=0, false_political_classification=40, false_rf_not_matched=0, unsupported_absence_claim=0, cross_person_evidence=2, unsupported_event_claim=20, false_actionable_candidate=0

## Monitoring E2E

Status RUN

| period | articles | runs | new documents | new persons | new events | new reviews | new classifications | new RF | new findings | semantic indexed | rerun new rows | seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T0 | 109 | ovd-info:completed, sota-vision:completed | 109 | 326 | 395 | 70 | 326 | 326 | 88 | 721 | 0 | 83.614 |
| T1 | 16 | ovd-info:completed, sota-vision:completed | 16 | 66 | 82 | 12 | 66 | 66 | 20 | 148 | 0 | 28.067 |
| T2 | 15 | ovd-info:completed, sota-vision:completed | 15 | 27 | 35 | 14 | 27 | 27 | 1 | 62 | 0 | 10.584 |
| T3 | 16 | ovd-info:completed, sota-vision:completed | 16 | 30 | 33 | 11 | 30 | 30 | 6 | 63 | 0 | 14.794 |

Rerun duplicates: aliases=0, classifications=0, events=0, extraction_runs=0, findings=0, mentions=0, person_event_links=0, persons=0, resolution_decisions=0, rf_results=0, semantic_documents=0, source_documents=0, static_aliases=0, static_classifications=0, static_events=0, static_findings=0, static_mentions=0, static_persons=0, static_resolution_decisions=0, static_rf_results=0, static_semantic_documents=0, static_source_documents=0

Finding timing: on_time=20, late=0, before_evidence=0, missing=32

## Failure Injection

| scenario | status | detail |
|---|---|---|
| rf_review_statuses_not_findings | PASS | 17 persons with an RF review status; 0 reached findings/candidates |
| manual_review_continuation | PASS | decision 3 ('Фемактивистк Дарью Серенко') → create_new_person; derived run completed: linked=1, classified=1, rf=1, indexed=1, re-ingestion=False |
| together_ai_failure | PASS | workflow failed (llm_unavailable); domain state changed: False |
| crash_after_ingestion | PASS | restart reproduces the uninterrupted state |
| crash_after_extraction | PASS | restart reproduces the uninterrupted state |
| crash_after_entity_resolution | PASS | restart reproduces the uninterrupted state |
| crash_after_classification | PASS | restart reproduces the uninterrupted state |
| crash_after_semantic_indexing | PASS | restart reproduces the uninterrupted state |
| qdrant_outage | PASS | outage runs ['completed_with_errors'], domain rows kept=True, retryable items=2; derived retry completed: indexed 72/72, re-ingestion=False |
| source_failures | PASS | timeout/HTTP 500/malformed: 3 failed, 13 of 13 others ingested, runs ['completed_with_errors']; after the source recovered 16/16 documents (completed,completed) |
| postgres_interruption | PASS | failed article left 0 extraction rows, retryable items=1, 15/15 others extracted, after retry 16/16 (completed_with_errors,completed,completed,completed) |
| no_rf_snapshot | PASS | 4 periods without a snapshot: findings=0, rf matches=0, absence statements=0 |

## Performance

Status RUN; 156 articles, 449 persons in 139.236 s (67.22 articles/min, 193.48 persons/min)

| stage | seconds |
|---|---|
| classification | 5.734 |
| discovery | 0.198 |
| extraction | 2.618 |
| findings | 1.956 |
| ingestion | 1.924 |
| resolution | 16.401 |
| rf_matching | 4.857 |
| semantic_indexing | 104.066 |

## Manual Review Workload

| metric | value |
|---|---|
| articles | 156 |
| er_reviews | 107 |
| er_reviews_per_100_articles | 68.59 |
| persecution_reviews | 113 |
| persecution_reviews_per_100_articles | 72.44 |
| rf_reviews | 17 |
| rf_reviews_per_100_articles | 10.9 |
| total_reviews_per_100_articles | 151.92 |

DB invariants: orphan_person_mentions=0, broken_mention_offsets=0, broken_event_offsets=0, event_links_to_inactive_persons=0, mentions_linked_to_inactive_persons=0, invalid_merge_references=0, duplicate_active_findings=0, duplicate_extraction_identities=0, invalid_rf_snapshot_references=0, findings_with_mismatched_snapshot=0, active_findings_without_not_matched=0, active_findings_without_political=0

## Known Limitations

- Corpus size 156 < target 1000: the existing adapters list only ovd-info=144, sota-vision=12 publications between 2026-03-01 and 2026-08-31.
- Golden annotations are DRAFT until a human verifies them; DRAFT metrics are preliminary.
- The Rosfinmonitoring snapshot is a committed evaluation snapshot built for this corpus (listed names, name variants, ambiguous namesakes), not the real published list.
- Entity resolution metrics only see annotated articles: links into persons from non-annotated articles are evaluated only through the golden person's majority person.
- The persecution classifier and RF matcher are rule-based; birth dates are not extracted.
- Failure-injection scenarios inject controlled exceptions in-process; a killed process (SIGKILL) and a real PostgreSQL restart are not simulated.

## Critical Failures

Failures by component and severity: ENTITY_RESOLUTION: {'S2': 14}; EVENT_ASSOCIATION: {'S2': 52, 'S1': 1}; EXTRACTION: {'S2': 275}; MONITORING: {'S0': 7}; PERSECUTION: {'S1': 34, 'S2': 54, 'S0': 5}; REPORT: {'S2': 58, 'S0': 143, 'S1': 13}; RETRIEVAL: {'S2': 69}; ROSFINMONITORING: {'S2': 19, 'S1': 2}

| severity | component | kind | case | person | gated | detail |
|---|---|---|---|---|---|---|
| S0 | MONITORING | false_actionable_finding | rw-20260301-00 | gp-olga-ovd-defender | True | active monitoring finding for a person who is not a main candidate |
| S0 | MONITORING | false_actionable_finding | rw-20260302-01 | gp-nefedov-oleg | True | active monitoring finding for a person who is not a main candidate |
| S0 | MONITORING | false_actionable_finding | rw-20260312-08 | gp-tazheeva-anna | True | active monitoring finding for a person who is not a main candidate |
| S0 | MONITORING | false_actionable_finding | rw-20260320-11 | gp-kuzhev-oleko | True | active monitoring finding for a person who is not a main candidate |
| S0 | MONITORING | false_actionable_finding | rw-20260428-24 | gp-putin-vladimir | True | active monitoring finding for a person who is not a main candidate |
| S0 | MONITORING | false_actionable_finding | rw-20260521-35 | gp-kuryanov-petr | True | active monitoring finding for a person who is not a main candidate |
| S0 | MONITORING | false_actionable_finding | rw-20260521-35 | gp-vitaly-l | True | active monitoring finding for a person who is not a main candidate |
| S0 | PERSECUTION | cross_person_political_attribution | rw-20260301-00 | gp-olga-ovd-defender | True | classified political, expected ['non_political']; political neighbours ['gp-abrosimov-alexey', 'gp-oshchepkov-sergey'] |
| S0 | PERSECUTION | cross_person_political_attribution | rw-20260302-01 | gp-nefedov-oleg | True | classified political, expected ['non_political']; political neighbours ['gp-alekhina-maria', 'gp-aleksashenko-sergey', 'gp-chichvarkin-evgeny', 'gp-gudkov-dmitr |
| S0 | PERSECUTION | cross_person_political_attribution | rw-20260312-08 | gp-tazheeva-anna | True | classified political, expected ['non_political']; political neighbours ['gp-frolov-ivan'] |
| S0 | PERSECUTION | cross_person_political_attribution | rw-20260428-24 | gp-putin-vladimir | True | classified political, expected ['non_political']; political neighbours ['gp-vasiliev-ilya', 'gp-yarotsky-vladimir'] |
| S0 | PERSECUTION | cross_person_political_attribution | rw-20260521-35 | gp-kuryanov-petr | True | classified political, expected ['non_political']; political neighbours ['gp-arseniev-alexander', 'gp-kurtnezirov-remzi', 'gp-sokolov-alexey', 'gp-tereshin-anato |
| S0 | REPORT | claim_contradicted |  | gp-putin-vladimir | True | rs-01: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-tazheeva-anna | True | rs-01: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-nefedov-oleg | True | rs-01: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-olga-ovd-defender | True | rs-01: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-kuryanov-petr | True | rs-01: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-02: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-02: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-vasiliev-ilya | True | rs-05: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-vasiliev-ilya | True | rs-05: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-05: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-05: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-dymov-andrey | True | rs-05: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-putin-vladimir | True | rs-07: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-tazheeva-anna | True | rs-07: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-nefedov-oleg | True | rs-07: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-olga-ovd-defender | True | rs-07: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-kuryanov-petr | True | rs-07: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-putin-vladimir | True | rs-08: persecution_classification: political claimed, annotated ['non_political'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kirman-viktor | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-badmaev-alexey | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-yarotsky-vladimir | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-miftakhov-azat | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-zhlobitsky-mikhail | True | rs-09: event: the annotated event is about other persons |
| S0 | REPORT | claim_contradicted |  | gp-lyubshin-ivan | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-vasiliev-ilya | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-vasiliev-ilya | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-abdulgaziev-tofik | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-akuzin-andrey | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
| S0 | REPORT | claim_contradicted |  | gp-nikandrov-marat | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-ilichev-vladimir | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-shoev-abdulmalik | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-yakusheva-larisa | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-darova-svetlana | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-tukova-anita | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-primak-olga | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-naptugov-aslan | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nagoev-ibragim | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-paklin-roman | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-musaeva-zarema | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-nikulin-andrey | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-kurochkina-inna | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-baryakina-elvira | True | rs-09: persecution_classification: non_political claimed, annotated ['needs_review', 'political', 'uncertain'] |
| S0 | REPORT | claim_contradicted |  | gp-stovbun-gleb | True | rs-09: persecution_classification: non_political claimed, annotated ['political'] |
... 145 more in the JSON report

## Recommended Next Work

1. **REPORT: research reports state facts the sources contradict** — `contradicted_report_claims` = 122 (target <= 0.0); evidence: claim_contradicted=122, research_person_missing=58, claim_unsupported=33. Possible work: trace each contradicted claim to the component that produced the wrong fact.
2. **DATA_QUALITY: false statements about real persons remain after gating (see per-kind counts)** — `gated_dangerous_failures` = 74 (target <= 0.0); evidence: no itemized failures. Possible work: group the dangerous failures by kind and trace each kind to the component that produced it.
3. **PERSECUTION: political evidence about one person is attributed to another** — `cross_person_persecution_attribution` = 5 (target <= 0.0); evidence: persecution_status=49, candidate_missed=32, candidate_false_positive=7. Possible work: inspect evidence windows in multi-person sentences and articles.
4. **ENTITY_RESOLUTION: namesakes (same or near-same name, different person) are auto-linked** — `namesake_different_person_auto_link` = 2 (target <= 0.0); evidence: false_create_new=7, duplicate_canonical_person=7. Possible work: decide which evidence may separate namesakes and when a namesake must go to review.
5. **EVENT_ASSOCIATION: events are linked to the wrong set of persons** — `person_event_association_accuracy` = 0.369 (target >= 0.95); evidence: event_person_association=53. Possible work: inspect association errors in multi-person sentences and shared events.
