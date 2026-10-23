# Person extraction and safe reprocessing: audit and experiment

Date: 2026-10-23. Budget $0: no paid LLM, OpenRouter, DeepSeek or JEV call was made.
The live database was only read. Every write test ran on a copy.

## 1. Versions

| Component | main before | after PR 1 |
|---|---|---|
| `RuleBasedEntityExtractor` | 1.6.0 | 1.7.1 |
| `RuleBasedMentionNormalizer` | 1.8.0 (from #84) | 1.8.0, not touched |
| event extractor | 1.12.0 | 1.12.0 |
| Run identity (`extractor_version+events / normalizer`) | `1.6.0+1.12.0 / 1.8.0` | `1.7.1+1.12.0 / 1.8.0` |

The running code behind the live database is older than main: the latest run of 4616 of 4617 articles is
`1.5.0+1.12.0 / 1.7.0`.

## 2. What changes

PR 1 (`fix/person-extraction-and-safe-reprocessing`, two commits):

- `processing/extraction/extractors.py`: extractor 1.7.1 reads «Фамилия И.О.» («Бастрыкин А.И.»,
  «Иванов-Петров А.Б.», «Виталия Л.») and skips streets and institutions («ул. Ленина В.И.»).
- `persons/resolution/{decision,models,normalizer,ai_context}.py`: one test, `has_full_identity`
  (at least two full tokens). A name that fails it goes to REVIEW with or without candidates and never
  creates a canonical person. Within-article coreference and AUTO_LINK are untouched. The AI reviewer
  uses the same test; a manual `CREATE_NEW_PERSON` stays a human decision.
- `processing/extraction/pending.py`: the gate. Monitoring extracts an article only if no run of the
  extractor exists in any version. A version bump no longer queues the stored corpus.

PR 2 (separate branch from main, not yet cut):

- `processing/extraction/reprocessing.py`: stale-article selection and a read-only plan.
- `extract-entities --stale [--article-ids ..] [--limit N] [--workers N] [--dry-run]` in the existing CLI.
- `persons/read.py`: `_newest_linked_run()` in `event_evidence`, `person_stats`, `latest_events`.
- Tests: `tests/test_reprocessing.py`, one test in `tests/monitoring/test_module_facades.py`.

## 3. The live database before the change (read-only)

| | |
|---|---|
| Articles | 4617 (4582 with text of 200+ characters, 35 shorter, 0 without a run) |
| Extraction runs | 12529, all succeeded; every article has 2 to 6 |
| Latest run version | 4616 × `1.5.0+1.12.0 / 1.7.0`, 1 × `1.6.0+1.12.0 / 1.7.0` |
| Person mentions (all runs / latest runs) | 38675 / 14277 |
| «Фамилия И.О.»-like mentions in latest runs | 127 |
| Persons / ER v2 decisions | 18 / 78 (18 auto_link, 17 create_new, 43 review) |
| Persons from an incomplete name | 1 (`8921 «Махмуд М.»`, created by CREATE_NEW) |
| Person → event links | 11 (10 distinct person-article pairs) |
| Manual data | excluded_persons 96, entity_pair_decisions 1236, entity_name_overrides 3 |
| Groups | entity_groups 2505, group mentions 12467 (all on the latest run), charges 782 |

No manual person decision exists in the live database: every ER v2 decision is automatic or pending.
Manual data is keyed by name keys, not by mention ids, so a new run does not orphan it.

## 4. Consequences of a new run (from the code)

1. Old runs, mentions and events are kept: `save` adds a run and never deletes.
2. `entity_group_mentions` point at the mentions of the latest run. After reprocessing an article, its
   group mentions sit on an older run until the entities are rebuilt. The rebuild (`collect`) asks a paid
   model for names, so it is not part of reprocessing.
3. The monitoring `resolve` stage takes runs of the current versions with an undecided person mention,
   except a re-extraction: a run is skipped when an earlier succeeded run of the same article has the same
   `article_content_hash` (`persons/resolution/pending.py`). New articles and articles refetched with
   changed text resolve as before. Resolving re-extracted runs is an explicit step (`resolve-people`,
   section 8) after a quality check.
4. Person cards read events through every run. Without a filter, a person linked in both the old and the
   new run counts a publication twice. `_newest_linked_run()` keeps, per person and article, the newest run
   that has a link of the person, so an old link stays visible until resolution links the new run.

## 5. Experiment on a copy

`pg_dump` of the live database (read-only transaction, 55 MB) restored into `reprocess_exp` in a separate
container (`reprocess-pg`, PostgreSQL 18.6, port 5437). Sample of 500 articles, nested (the first 20 are
inside the first 100): all 16 articles with person links, 84 with «Фамилия И.О.»-like names, 107 with 8+
person mentions, 386 with criminal-case groups, the rest random. One worker.

| Batch | Articles | Time | Max RSS | Mentions | Events | Failed |
|---|---:|---:|---:|---:|---:|---:|
| 20 (smoke) | 20 | 3.3 s | 290 MB | 114 | 56 | 0 |
| 100 | 80 new | 4.4 s | 290 MB | 536 | 165 | 0 |
| 500 | 400 new | 10.2 s | 294 MB | 2637 | 945 | 0 |
| Repeat of the first 20 | 0 | 2.7 s | 282 MB | 0 | 0 | 0 |

The repeat selected nothing: idempotent and resumable. Rough size of a full pass: 4617 articles in
under two minutes and about 300 MB (CPU only). ER v2 over the 500 new runs: 62 s, 79 MB.

### Before and after

«Before» is the copy as dumped; «after» is the copy after the 500 articles and ER v2 on their new runs.

| Metric | Before | After |
|---|---:|---:|
| Runs | 12529 | 13029 |
| Person mentions in latest runs (whole corpus) | 14277 | 14296 |
| «Фамилия И.О.»-like mentions in latest runs | 127 | 157 |
| …of them with the surname in `last_name` | 0 | 100 |
| …the other 57 | – | «Имя Ф.» («Елена Ц.»): name in `first_name`, initial in `last_name`, by design |
| Events in latest runs | 9701 | 9701 |
| Events in all runs | 27328 | 28494 |
| AUTO_LINK decisions | 18 | 1045 |
| REVIEW decisions | 43 | 629 |
| CREATE_NEW decisions | 17 | 668 |
| CREATE_NEW from an incomplete name | 1 | 1 (the old `Махмуд М.`; none among the 651 new) |
| Would be CREATE_NEW under the old policy (REVIEW, no candidate, initials) | – | 58 prevented |
| AUTO_LINK of a name with initials | 0 | 0 |
| Persons | 18 | 669 |
| Person → event links | 11 | 524 |
| …without double counting (newest-run filter) | 11 | 515 (9 would be counted twice) |
| The 11 old links in the filtered view | 11 | 2 kept, 9 replaced by the same article's new-run links, none lost |
| Group mentions on a non-latest run | 0 | 1928 |
| Manual data (excluded, pair decisions, overrides) | 96 / 1236 / 3 | 96 / 1236 / 3 |
| Lost manual decisions | – | 0 |

Not audited: whether each of the 1045 automatic links is the right person. This is an automatic check, not
human GOLD labelling.

## 6. Risks

1. **ER v2 on reprocessed runs creates persons in bulk**: 651 new persons from 500 articles. The live
   database has 18 because ER v2 was never run over the corpus. Mitigated: monitoring skips re-extractions of
   unchanged text (see section 4, item 3), so `extract-entities --stale` alone creates no persons. The risk
   returns only if someone runs `resolve-people` over the reprocessed runs: do that after the check in step 5.
   Under the new policy none of them comes from an incomplete name (58 went to REVIEW instead).
2. **Groups lag**: 1928 group mentions point at older runs after 500 articles. Group readers see old data
   until `collect`, which uses a paid model. Do not run `collect` without a budget decision.
3. **Gate side effect**: after any later version bump, old articles are not re-extracted by monitoring.
   Reprocessing is always an explicit command.
4. **Shared test database**: `court_monitor_test` in `ebnv-pgvector-test` is migrated by another branch
   (`feat/criminal-case-resolution`, revision `r2e3d4f5a6b7`) and cannot be upgraded from here. Tests ran in
   a private container.
5. **Deploy order**: the live code is older than main. Deploying main alone starts nothing on the old corpus
   (gate), but it changes versions, so every new run is under `1.7.1+1.12.0 / 1.8.0`.

## 7. Tests

- `court-processing/tests`: 325 passed. With the database (`tests/persons`, `tests/extraction`,
  `tests/monitoring`, `tests/test_cli_batches.py`, `court-processing/tests`): 816 passed.
- New: `tests/test_reprocessing.py` (6), `test_module_facades.py` (+1), `test_incomplete_person_names.py`,
  `test_surname_with_initials.py`.
- Mutations caught: removing `_newest_linked_run()` fails the double-count test; restoring the version
  query in `pending.py` fails the gate test.
- ruff clean; mypy clean on `court-processing/src`; on `apps/` and tests the same six pre-existing
  stub errors (`observability`, `application`, `cli_batches`) as on main.
- The full suite was not run (it has crashed the workstation before).

## 8. Safe run (runbook)

1. Back up: `scripts/backup_db.sh` (the daily timer also does it) and check the file restores.
2. `uv run python -m main extract-entities --stale --dry-run --limit 100` and read `stale_articles_total`,
   `selected_without_text`, `warnings`. It writes nothing.
3. Nothing to stop: monitoring does not resolve re-extractions of unchanged text (risk 1). Resolving them
   is a separate decision after step 5 (`resolve-people --limit N`, one worker).
4. `uv run python -m main extract-entities --stale --limit 20`, then 100, then 500. One worker. Watch
   `event=reprocess_batch_done` in the log and `stale articles left`. A failed chunk stops the run; run again
   to continue.
5. Check: `group_mentions_on_older_runs` rose as expected, person cards show one publication once, manual
   tables unchanged.
6. Do not run `collect`, the AI reviewer or any paid stage without separate approval.

## 9. Rollback

Runs are only added, so the data of the old state stays. To undo a batch: delete the runs of the new version
(`DELETE FROM article_extraction_runs WHERE extractor_version = '1.7.1+1.12.0' AND normalizer_version =
'1.8.0' AND id > <last id of the dump>`; mentions and events cascade), and the decisions and persons created
from them. The simplest rollback is restoring the backup from step 1. Neither was needed on the copy.

## 10. Needs separate approval

- Any write to the live database (reprocessing, the ER v2 pass, a deploy).
- Rebuilding entities (`collect`), the AI reviewer, any paid model.
- Pushing the branches, opening the PRs, merging.
- Deleting the container `reprocess-pg`, the dump file and the copy `reprocess_exp`.
