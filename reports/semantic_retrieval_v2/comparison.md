# Semantic Retrieval v2 — E5 vs BGE-M3 (PRELIMINARY)

Golden articles and relevance judgments are agent DRAFT; nothing here is human-verified.
Unjudged entities are never counted as not relevant: recall@100 is a lower bound,
coverage@k is the judged share of the top k. The test split was not run.

## Ranking — dev

### all

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 104 | 0.8183 | 0.8481 | 0.8768 | 0.9192 | 0.982 | 0.8 | 0.8112 | 0.9962 | 0.5539 |
| bge_m3_hybrid | 104 | 0.6344 | 0.6656 | 0.7281 | 0.8587 | 0.9742 | 0.5977 | 0.6181 | 0.9962 | 0.5871 |
| e5_dense | 104 | 0.7766 | 0.8059 | 0.8623 | 0.9053 | 0.9687 | 0.7473 | 0.7672 | 0.9971 | 0.6274 |
| e5_hybrid | 104 | 0.6187 | 0.6422 | 0.7375 | 0.8463 | 0.9742 | 0.585 | 0.615 | 0.9962 | 0.6237 |
| lexical | 104 | 0.5005 | 0.5068 | 0.5228 | 0.5821 | 0.7121 | 0.4728 | 0.4786 | 0.9962 | 0.7552 |

### person

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 92 | 0.8087 | 0.8627 | 0.8951 | 0.9385 | 0.9896 | 0.8096 | 0.8229 | 0.9957 | 0.5462 |
| bge_m3_hybrid | 92 | 0.597 | 0.6636 | 0.7342 | 0.8728 | 0.9762 | 0.5823 | 0.6059 | 0.9957 | 0.578 |
| e5_dense | 92 | 0.7693 | 0.8322 | 0.8797 | 0.9211 | 0.9682 | 0.7644 | 0.7811 | 0.9967 | 0.618 |
| e5_hybrid | 92 | 0.5944 | 0.6517 | 0.7431 | 0.8615 | 0.9781 | 0.5796 | 0.6102 | 0.9957 | 0.6193 |
| lexical | 92 | 0.4671 | 0.5031 | 0.5194 | 0.5765 | 0.6945 | 0.4564 | 0.4626 | 0.9957 | 0.767 |

### event

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 12 | 0.8917 | 0.7361 | 0.7361 | 0.7708 | 0.9236 | 0.7262 | 0.7219 | 1.0 | 0.6133 |
| bge_m3_hybrid | 12 | 0.9211 | 0.6806 | 0.6806 | 0.75 | 0.9583 | 0.7157 | 0.7112 | 1.0 | 0.6567 |
| e5_dense | 12 | 0.8333 | 0.6042 | 0.7292 | 0.7847 | 0.9722 | 0.6165 | 0.6604 | 1.0 | 0.6992 |
| e5_hybrid | 12 | 0.8053 | 0.5694 | 0.6944 | 0.7292 | 0.9444 | 0.6262 | 0.6525 | 1.0 | 0.6575 |
| lexical | 12 | 0.7569 | 0.5347 | 0.5486 | 0.625 | 0.8472 | 0.5987 | 0.6009 | 1.0 | 0.6649 |

### semantic_only

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 26 | 0.8283 | 0.8846 | 0.9231 | 0.9808 | 0.9936 | 0.8269 | 0.8392 | 1.0 | 0.4912 |
| bge_m3_hybrid | 26 | 0.1586 | 0.2179 | 0.3333 | 0.7244 | 1.0 | 0.1092 | 0.1447 | 1.0 | 0.5307 |
| e5_dense | 26 | 0.6986 | 0.8269 | 0.9167 | 0.9295 | 0.9936 | 0.7075 | 0.7351 | 1.0 | 0.5375 |
| e5_hybrid | 26 | 0.1187 | 0.1538 | 0.3974 | 0.7308 | 0.9936 | 0.0749 | 0.1561 | 1.0 | 0.5891 |
| lexical | 26 | 0.0109 | 0.0128 | 0.0192 | 0.0192 | 0.0321 | 0.0064 | 0.0085 | 1.0 | 0.9144 |

### group

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 10 | 0.7811 | 0.7286 | 0.7769 | 0.8094 | 0.9295 | 0.6399 | 0.6686 | 0.98 | 0.5902 |
| bge_m3_hybrid | 10 | 0.7803 | 0.6468 | 0.6468 | 0.7718 | 0.8812 | 0.6295 | 0.621 | 0.98 | 0.625 |
| e5_dense | 10 | 0.7769 | 0.6893 | 0.7679 | 0.7821 | 0.9078 | 0.6522 | 0.6819 | 0.99 | 0.7352 |
| e5_hybrid | 10 | 0.7324 | 0.6036 | 0.6286 | 0.7179 | 0.8487 | 0.5958 | 0.6044 | 0.98 | 0.6611 |
| lexical | 10 | 0.6699 | 0.5536 | 0.5536 | 0.5786 | 0.7558 | 0.5692 | 0.5632 | 0.98 | 0.7552 |

### hard

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 6 | 0.7738 | 0.8333 | 1.0 | 1.0 | 1.0 | 0.7718 | 0.8274 | 1.0 | 0.4252 |
| bge_m3_hybrid | 6 | 0.7156 | 0.8333 | 0.8333 | 0.8333 | 1.0 | 0.7384 | 0.7384 | 1.0 | 0.4456 |
| e5_dense | 6 | 0.7254 | 0.8333 | 0.8333 | 0.8333 | 1.0 | 0.75 | 0.75 | 1.0 | 0.4629 |
| e5_hybrid | 6 | 0.6966 | 0.6667 | 0.8333 | 0.8333 | 1.0 | 0.6667 | 0.726 | 1.0 | 0.4879 |
| lexical | 6 | 0.6739 | 0.6667 | 0.6667 | 0.6667 | 0.8333 | 0.6667 | 0.6667 | 1.0 | 0.8482 |

### Paired bootstrap (95% CI of the mean per-query difference) — dev

| pair | metric | mean diff | CI low | CI high | queries |
|---|---|---|---|---|---|
| bge_m3_dense - e5_dense | mrr | 0.0416 | -0.0073 | 0.0946 | 104 |
| bge_m3_dense - e5_dense | recall@10 | 0.0145 | -0.0303 | 0.0585 | 104 |
| bge_m3_dense - e5_dense | ndcg@10 | 0.044 | 0.0044 | 0.0893 | 104 |
| bge_m3_hybrid - e5_hybrid | mrr | 0.0157 | -0.0118 | 0.0457 | 104 |
| bge_m3_hybrid - e5_hybrid | recall@10 | -0.0095 | -0.0353 | 0.0114 | 104 |
| bge_m3_hybrid - e5_hybrid | ndcg@10 | 0.003 | -0.0164 | 0.023 | 104 |

## Ranking — validation

### all

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 46 | 0.6883 | 0.7166 | 0.7634 | 0.8178 | 0.9783 | 0.676 | 0.6869 | 0.9935 | 0.532 |
| bge_m3_hybrid | 46 | 0.5616 | 0.5437 | 0.6538 | 0.8142 | 0.9331 | 0.5087 | 0.5515 | 0.9913 | 0.5686 |
| e5_dense | 46 | 0.6932 | 0.7536 | 0.7859 | 0.833 | 0.969 | 0.6739 | 0.6824 | 0.9935 | 0.6123 |
| e5_hybrid | 46 | 0.5533 | 0.5283 | 0.6883 | 0.8287 | 0.9676 | 0.498 | 0.5541 | 0.987 | 0.6032 |
| lexical | 46 | 0.3879 | 0.4022 | 0.4567 | 0.5183 | 0.6403 | 0.3684 | 0.3864 | 0.9935 | 0.7244 |

### person

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 37 | 0.7209 | 0.7918 | 0.8365 | 0.8636 | 1.0 | 0.7243 | 0.7352 | 0.9919 | 0.5279 |
| bge_m3_hybrid | 37 | 0.5306 | 0.5633 | 0.6867 | 0.8365 | 0.9439 | 0.5014 | 0.548 | 0.9892 | 0.5664 |
| e5_dense | 37 | 0.6809 | 0.7972 | 0.8149 | 0.8555 | 0.9705 | 0.6941 | 0.6952 | 0.9919 | 0.5955 |
| e5_hybrid | 37 | 0.5226 | 0.5171 | 0.6891 | 0.8365 | 0.9732 | 0.4795 | 0.5392 | 0.9838 | 0.5935 |
| lexical | 37 | 0.3872 | 0.4459 | 0.4552 | 0.5093 | 0.5933 | 0.3914 | 0.3936 | 0.9919 | 0.7692 |

### event

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 9 | 0.5541 | 0.4074 | 0.463 | 0.6296 | 0.8889 | 0.4776 | 0.4885 | 1.0 | 0.5489 |
| bge_m3_hybrid | 9 | 0.6891 | 0.463 | 0.5185 | 0.7222 | 0.8889 | 0.5386 | 0.5662 | 1.0 | 0.5778 |
| e5_dense | 9 | 0.7441 | 0.5741 | 0.6667 | 0.7407 | 0.963 | 0.5908 | 0.63 | 1.0 | 0.6811 |
| e5_hybrid | 9 | 0.6796 | 0.5741 | 0.6852 | 0.7963 | 0.9444 | 0.5737 | 0.6156 | 1.0 | 0.6433 |
| lexical | 9 | 0.3911 | 0.2222 | 0.463 | 0.5556 | 0.8333 | 0.2738 | 0.357 | 1.0 | 0.5402 |

### semantic_only

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 11 | 0.6546 | 0.9091 | 0.9091 | 0.9091 | 1.0 | 0.7175 | 0.7175 | 1.0 | 0.456 |
| bge_m3_hybrid | 11 | 0.1325 | 0.2727 | 0.5455 | 0.8182 | 1.0 | 0.1198 | 0.2058 | 1.0 | 0.4943 |
| e5_dense | 11 | 0.6744 | 0.9091 | 0.9091 | 0.9091 | 1.0 | 0.7305 | 0.7305 | 1.0 | 0.5249 |
| e5_hybrid | 11 | 0.1074 | 0.0909 | 0.5455 | 0.8182 | 1.0 | 0.0352 | 0.1873 | 1.0 | 0.5569 |
| lexical | 11 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 | 0.9773 |

### group

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 4 | 0.6458 | 0.2827 | 0.4463 | 0.4463 | 1.0 | 0.4525 | 0.4642 | 0.975 | 0.4937 |
| bge_m3_hybrid | 4 | 0.3417 | 0.169 | 0.31 | 0.5297 | 0.8558 | 0.1241 | 0.235 | 0.95 | 0.5325 |
| e5_dense | 4 | 0.875 | 0.3327 | 0.4963 | 0.5797 | 0.8939 | 0.5432 | 0.5529 | 0.975 | 0.5436 |
| e5_hybrid | 4 | 0.55 | 0.2418 | 0.3327 | 0.5297 | 0.8773 | 0.2671 | 0.325 | 0.925 | 0.6014 |
| lexical | 4 | 0.1974 | 0.0833 | 0.2524 | 0.2524 | 0.613 | 0.0877 | 0.1225 | 0.95 | 0.4908 |

### hard

| system | queries | mrr | recall@5 | recall@10 | recall@20 | recall@100 | ndcg@5 | ndcg@10 | coverage@10 | coverage@100 |
|---|---|---|---|---|---|---|---|---|---|---|
| bge_m3_dense | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.4444 |
| bge_m3_hybrid | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.4848 |
| e5_dense | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.3737 |
| e5_hybrid | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0.4646 |
| lexical | 1 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

### Paired bootstrap (95% CI of the mean per-query difference) — validation

| pair | metric | mean diff | CI low | CI high | queries |
|---|---|---|---|---|---|
| bge_m3_dense - e5_dense | mrr | -0.0049 | -0.0821 | 0.0736 | 46 |
| bge_m3_dense - e5_dense | recall@10 | -0.0225 | -0.0913 | 0.0449 | 46 |
| bge_m3_dense - e5_dense | ndcg@10 | 0.0045 | -0.0528 | 0.0638 | 46 |
| bge_m3_hybrid - e5_hybrid | mrr | 0.0083 | -0.0333 | 0.0541 | 46 |
| bge_m3_hybrid - e5_hybrid | recall@10 | -0.0346 | -0.1196 | 0.0395 | 46 |
| bge_m3_hybrid - e5_hybrid | ndcg@10 | -0.0026 | -0.0355 | 0.0301 | 46 |

## Acceptance (dense similarity threshold)

Dev baseline E5@0.80 and the Pareto selection: {'bge_m3': 0.47, 'e5': 0.8}.

| split | configuration | recall_micro | recall_macro | precision_judged | accepted_unjudged | judged_share_of_accepted | rejection_rate | hard_negative_fp | offtopic_fp |
|---|---|---|---|---|---|---|---|---|---|
| dev | e5@0.8 (baseline) | 0.8503 | 0.9254 | 0.0324 | 1608 | 0.7316 | 0.5556 | 280 | 0 |
| dev | e5@0.8 (selected) | 0.8503 | 0.9254 | 0.0324 | 1608 | 0.7316 | 0.5556 | 280 | 0 |
| dev | bge_m3@0.47 (selected) | 0.7425 | 0.8351 | 0.087 | 78 | 0.9481 | 0.5556 | 38 | 0 |
| validation | e5@0.8 dense | 0.8846 | 0.923 | 0.0382 | 625 | 0.7431 | 0.5 | 147 | 0 |
| validation | e5@0.8 hybrid | 0.8462 | 0.9034 | 0.0408 | 465 | 0.7765 | 0.5 | 133 | 0 |
| validation | bge_m3@0.47 (dev-selected) dense | 0.6667 | 0.7603 | 0.087 | 36 | 0.9432 | 0.5 | 8 | 0 |
| validation | bge_m3@0.47 (dev-selected) hybrid | 0.6667 | 0.7603 | 0.087 | 36 | 0.9432 | 0.5 | 8 | 0 |
| validation | e5@0.8 (dev-selected) dense | 0.8846 | 0.923 | 0.0382 | 625 | 0.7431 | 0.5 | 147 | 0 |
| validation | e5@0.8 (dev-selected) hybrid | 0.8462 | 0.9034 | 0.0408 | 465 | 0.7765 | 0.5 | 133 | 0 |

## Research Workflow (deterministic parser, hybrid + acceptance)

| split | configuration | recall_micro | recall_macro | precision_judged | returned_unjudged | returned_median | empty_result_positive_queries | negative_rejection_rate | negative_persons_returned | latency p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| dev | e5@0.8 | 0.8561 | 0.9327 | 0.0328 | 1151 | 49.5 | 1 | 0.5714 | 180 | 55.9 |
| validation | e5@0.8 | 0.8644 | 0.925 | 0.0408 | 366 | 47 | 0 | 0.5 | 71 | 49.0 |
| dev | e5_recalibrated@0.8 | 0.8561 | 0.9327 | 0.0328 | 1151 | 49.5 | 1 | 0.5714 | 180 | 52.0 |
| validation | e5_recalibrated@0.8 | 0.8644 | 0.925 | 0.0408 | 366 | 47 | 0 | 0.5 | 71 | 49.9 |
| dev | bge_m3_calibrated@0.47 | 0.7576 | 0.8489 | 0.086 | 73 | 8.0 | 1 | 0.5714 | 20 | 41.8 |
| validation | bge_m3_calibrated@0.47 | 0.678 | 0.7786 | 0.084 | 36 | 11 | 1 | 0.5 | 4 | 41.2 |

Research benchmark semantic cases (claims judged against the golden set):

- e5@0.8: persons {'tp': 4, 'fp': 0, 'fn': 2, 'precision': 1.0, 'recall': 0.6667, 'f1': 0.8}, claims {'SUPPORTED': 484, 'PARTIALLY_SUPPORTED': 34, 'UNSUPPORTED': 55, 'CONTRADICTED': 32, 'NOT_EVALUATED': 134}
- e5_recalibrated@0.8: persons {'tp': 4, 'fp': 0, 'fn': 2, 'precision': 1.0, 'recall': 0.6667, 'f1': 0.8}, claims {'SUPPORTED': 484, 'PARTIALLY_SUPPORTED': 34, 'UNSUPPORTED': 55, 'CONTRADICTED': 32, 'NOT_EVALUATED': 134}
- bge_m3_calibrated@0.47: persons {'tp': 4, 'fp': 0, 'fn': 2, 'precision': 1.0, 'recall': 0.6667, 'f1': 0.8}, claims {'SUPPORTED': 440, 'PARTIALLY_SUPPORTED': 40, 'UNSUPPORTED': 53, 'CONTRADICTED': 18, 'NOT_EVALUATED': 112}

Per-query outcome, E5@0.80 vs BGE-M3 calibrated (correct = all relevant returned and no judged non-relevant (negative: nothing)): {'e5_correct_bge_wrong': 0, 'bge_correct_e5_wrong': 10, 'both_wrong': 120, 'both_correct': 10}

Recall only (correct = all relevant returned (negative: nothing returned)): {'e5_correct_bge_wrong': 15, 'bge_correct_e5_wrong': 1, 'both_wrong': 17, 'both_correct': 107}

## Performance (RTX 3060, same 805 documents)

| model | dimension | model_load_seconds | full_corpus_embedding_seconds | documents_per_second | full_rebuild_seconds | vram_peak_mib | ram_peak_mib | event_hnsw_index_bytes | query emb p50/p95 ms | dense PERSON p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| e5 | 768 | 13.25 | 3.15 | 255.4 | 4.18 | 1605.1 | 2518.1 | 2072576 | 7.55/9.1 | 11.06/12.79 |
| bge_m3 | 1024 | 13.99 | 9.66 | 83.4 | 11.04 | 2886.3 | 3624.7 | 4104192 | 13.72/16.28 | 17.2/20.62 |
