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

PR 1 (`fix/person-extraction-and-safe-reprocessing`, three commits):

- `processing/extraction/extractors.py`: extractor 1.7.1 reads «Фамилия И.О.» («Бастрыкин А.И.»,
  «Иванов-Петров А.Б.», «Виталия Л.») and skips streets and institutions («ул. Ленина В.И.»).
- `persons/resolution/{decision,models,normalizer,ai_context}.py`: one test, `has_full_identity`
  (at least two full tokens). A name that fails it goes to REVIEW with or without candidates and never
  creates a canonical person. Within-article coreference and AUTO_LINK are untouched. The AI reviewer
  uses the same test; a manual `CREATE_NEW_PERSON` stays a human decision.
- `processing/extraction/pending.py`: the gate. Monitoring skips an article that has a succeeded run of the
  extractor in any version, or a run (any status) of the current versions. A version bump no longer queues
  the stored corpus; an article whose only run failed under an older version is retried by the new one.

PR 2 (two commits on the same branch, on top of PR 1):

- `processing/extraction/reprocessing.py`: stale-article selection and a read-only plan.
- `extract-entities --stale [--article-ids ..] [--limit N] [--workers N] [--dry-run]` in the existing CLI.
- `persons/read.py`: `_newest_linked_run()` in `event_evidence`, `person_stats`, `latest_events`.
- `persons/resolution/pending.py`: monitoring does not resolve a re-extraction of unchanged text.
- Tests: `tests/test_reprocessing.py`, two tests in `tests/monitoring/test_module_facades.py`.

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

1. Old succeeded runs, mentions and events are kept: `save` adds a run for a new key and returns
   `skipped_existing` for a succeeded one. It does delete the children of a FAILED run of the same key when
   that run is retried (`_delete_children`), and it does so without a lock.
2. `entity_group_mentions` point at the mentions of the latest run. After reprocessing an article, its
   group mentions sit on an older run until the entities are rebuilt. The rebuild (`collect`) asks a paid
   model for names, so it is not part of reprocessing.
3. The monitoring `resolve` stage takes runs of the current versions with an undecided person mention,
   except a re-extraction: a run is skipped when the nearest earlier succeeded run of the same article by the
   same extractor has the same `article_content_hash` (`persons/resolution/pending.py`). Text that came back
   after a different one, and the first run of an extractor, are resolved. New articles and articles refetched with
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
   unchanged text (see section 4, item 3), so `extract-entities --stale` alone creates no persons for them. The
   guarantee covers unchanged text only: an article first extracted by monitoring, or refetched with changed
   text, is resolved by the `resolve` stage as before. The risk returns if someone runs `resolve-people` over
   the reprocessed runs: do that after the check in step 5.
   Under the new policy none of them comes from an incomplete name (58 went to REVIEW instead).
2. **Groups lag**: 1928 group mentions point at older runs after 500 articles. Group readers see old data
   until `collect`, which uses a paid model. Do not run `collect` without a budget decision.
3. **Gate side effect**: after any later version bump, articles with a succeeded run are not re-extracted by
   monitoring. Reprocessing is always an explicit command. A FAILED run of the current versions is not
   retried by monitoring either (deterministic for that content and version).
4. **Shared test database**: `court_monitor_test` in `ebnv-pgvector-test` is migrated by another branch
   (`feat/criminal-case-resolution`, revision `r2e3d4f5a6b7`) and cannot be upgraded from here. Tests ran in
   a private container.
5. **Deploy order**: the live code is older than main (runs `1.5.0+1.12.0 / 1.7.0`), and main (9f5eb70e,
   extractor 1.6.0) has no gate: its pending query needs an exact version match, so deploying main alone queues
   the whole stored corpus for extraction. Deploy this branch as a whole (gate and option C) before the first
   monitoring run on the new code, and take monitoring off until then. Without option C, those new runs go to
   ER v2 and create persons in bulk (risk 1).
6. **`--stale` ignores the current text**: stale means no succeeded run of the current versions. If the stored
   text changed after such a run, the article is not stale; monitoring handles a refetch.
7. **Failed articles**: a failed run of the current versions stays stale, so the command exits with 1 after
   its report. A persistent failure with a small id holds the front of every `--limit` batch; fix the cause or
   go past it with `--article-ids`.
8. **Concurrent runs**: `save` is SELECT then INSERT, and the unique key turns a race into `IntegrityError`.
   That is an old property, not new here, but it aborts the chunk (the whole run with one worker). Do not run
   monitoring and `--stale`, or two `--stale` runs, at the same time. Making `save` safe is a separate task.
9. **`resolve-people --limit N`** takes the first N articles by id every time and does not advance through
   the pending runs: for a stepwise resolve use `--article-id` in a loop over chosen ids.

## 7. Tests

Run on 2026-10-23 in a private container with the migrated schema (`reprocess-pg`):

- `tests/test_reprocessing.py` (12), `tests/test_cli_batches.py`, `tests/persons`, `tests/monitoring`,
  `tests/extraction`, `tests/app`, `packages/court-processing/tests`: 1536 passed on the final branch.
- Mutations caught: removing `_newest_linked_run()` (double count); restoring the exact-version query in
  `processing/extraction/pending.py`; removing the failed/older-version rule there; comparing with any
  earlier hash instead of the nearest earlier run; dropping `status = 'succeeded'` in the stale query;
  dropping the exit code 1. The `latest_events` test only guards that a single old link stays visible: it
  cannot tell the filter from no filter.
- ruff clean. mypy on `packages/court-processing/src` and `apps/court-monitor/src/cli`: 3 `import-untyped`
  errors (libraries without stubs), none in changed code.
- Not run: the full suite (it has crashed the workstation before), `tests/entities`, `tests/evaluation`,
  the other test directories, and any test against the live schema. The race in `save` has no test.

## 8. Safe run (runbook)

0. Deploy this branch as a whole before anything else (risk 5). Stop monitoring and do not start a second
   `extract-entities` while a batch runs (risk 8).
1. Back up: `scripts/backup_db.sh` (the daily timer also does it) and check the file restores.
2. `uv run python -m main extract-entities --stale --dry-run --limit 100` and read `stale_articles_total`,
   `selected_without_text` (such articles get a run with no mentions), `warnings`. It writes nothing.
3. `uv run python -m main extract-entities --stale --limit 20`, then 100, then 500. One worker. Watch
   `event=reprocess_batch_done` in the log and `stale articles left`. A failed chunk stops the run; run again
   to continue. If the command exits with 1, an article failed: read `failures`, fix the cause, or go past
   it with `--article-ids` (risk 7). Keep monitoring off until the check in step 4 is done.
4. Check: `group_mentions_on_older_runs` rose as expected, person cards show one publication once, manual
   tables unchanged, no persons were created by the batch.
5. Resolving the reprocessed runs is a separate decision after step 4: `resolve-people --article-id N`
   per chosen article, or `--limit N` knowing it always starts from the lowest ids (risk 9). Then switch
   monitoring on.
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
