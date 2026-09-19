"""Semantic Retrieval v2 experiment: E5 vs BGE-M3 on the real-world golden corpus.

The modules are runnable stages, in order: `preflight` (the model fits the GPU and
pgvector), `stand` (build the disposable evaluation stand and index it per model),
`retrieval` (rank, pool candidates, judge, sweep thresholds), `downstream` (the
Research Workflow under each acceptance threshold), `perf` (cost) and `report`
(the committed comparison). `draft_golden` prefills DRAFT golden articles.

Committed inputs and outputs live outside the package:
`evaluation/semantic_v2/pool_judgments.json` and `reports/semantic_retrieval_v2/`.
"""
