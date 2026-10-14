# The manual evaluation set: what it is, and what is not in the repository

`manual_golden.json` next to this file is the **shape** of the set the benchmark runs on,
filled with cases whose subjects are invented. It is what a fresh clone gets, so that
`python -m evaluation.investigation manual --modes local web` runs without copying
anything by hand, and so that the harness itself — the loader, the scoring, the limits,
the report — is exercised by something anyone can run.

The set that is actually **measured** is annotated from open sources and is **not in the
repository**. It lives in `var/investigation_eval/`, which `.gitignore` keeps out, and it
names real people together with the articles they are charged under.

That is a decision, not an omission. This repository is public. A file of real names with
the Criminal Code article of each is not a test fixture; it is a searchable index of who
is prosecuted and for what, and publishing it would put a list of that kind where nobody
agreed to put one. The previous branch made the same call — its PR description says the
annotated set lives in `var/` and «не в репозитории» — and this change keeps it.

So: the harness is reproducible, the measurement is not, on purpose. Point the benchmark
at the real set with `--manual`, and every number in the PR description comes from there.

## What is in the committed file

| | |
|---|---|
| `cases` | 24 person cases. Invented names, invented cases. |
| `unnamed_cases` | 8 cases of a publication that leaves its figurant unnamed. The article numbers are real; the people are not, and the correct answer for most of them is «не установлено». |
| `namesake_pairs` | 4 pairs of invented people sharing a surname. |

## The shape of a person case

```jsonc
{
  "case_id": "manual|иванов",
  "query": "Иван Иванов",          // what is asked of the run
  "group": "web_only_fact",         // what kind of case this is, for grouping in a report
  "in_db_person": false,            // the pipeline's own answer, as the annotator saw it
  "note": "why this case is here",
  "expected": [
    {
      "fact": "charges",            // a key of the scenario's facts
      "value": "ст. 276 УК",        // the value as the law writes it
      "parts": [["ст. 276", "статьи 276", "276"]],  // ways of writing it; any one counts
      "common": "шпионаж",           // the loose form; counted apart, never as the fact
      "source": "https://…",        // where the expectation comes from
      "as_written": "обвинили в шпионаже (ст. 276 УК)"
    },
    {
      "kind": "not_established",    // instead of a fact: what the right answer is
      "value": "не установлено"
    }
  ]
}
```

`parts` is a list of alternatives, and all of them must stand in one text: «18 лет колонии
строгого режима» is a verdict, «18 лет» alone is a bus ticket. `common` is counted
separately so a report can say how much the loose form would have claimed for itself.

## The two questions a fact is asked two ways

The benchmark never calls «a page held the expected text» a confirmed fact. It asks:

- **`evidence_contains_expected_value`** — did some evidence hold the whole value, in a
  text about that person? This is retrieval. It is not a finding.
- **`confirmed_finding`** — did the expected value appear in `InvestigationResult.findings`
  as a `Finding` citing evidence? This is what a reader is told. It is the product metric.

A retrieval hit that never became a finding is counted, named as such, and is a gap in
the extractor rather than a success.
