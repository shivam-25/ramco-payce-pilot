# Retrieval bake-off

35 golden questions · k=5 · scored with set arithmetic, no judge model.

Ranked on date correctness first, then hit rate, then MRR. `dated` is the share of the five date-dependent questions where every statutory rule retrieved was actually in force on the day asked about. A combination that scores well everywhere else and badly here will answer a June payslip question with the August rule — which is the error the previous vendor pilot shipped.

| # | Chunking | Retrieval | hit@k | recall | precision | MRR | dated | abstain |
|---|---|---|---|---|---|---|---|---|
| 1 | D_heading_context | R4_hybrid_dated | 0.97 | 0.97 | 0.46 | 0.87 | 1.00 | 1.00 |
| 2 | C_heading | R4_hybrid_dated | 0.97 | 0.97 | 0.41 | 0.83 | 1.00 | 1.00 |
| 3 | A_whole_doc | R4_hybrid_dated | 0.90 | 0.90 | 0.18 | 0.74 | 1.00 | 1.00 |
| 4 | B_fixed_512 | R4_hybrid_dated | 0.87 | 0.87 | 0.23 | 0.72 | 1.00 | 1.00 |
| 5 | D_heading_context | R3_hybrid | 0.97 | 0.97 | 0.50 | 0.92 | 0.40 | 1.00 |
| 6 | D_heading_context | R1_dense | 0.97 | 0.97 | 0.51 | 0.89 | 0.40 | 1.00 |
| 7 | C_heading | R1_dense | 0.97 | 0.97 | 0.45 | 0.85 | 0.40 | 0.75 |
| 8 | C_heading | R3_hybrid | 0.97 | 0.97 | 0.45 | 0.85 | 0.40 | 1.00 |
| 9 | A_whole_doc | R3_hybrid | 0.90 | 0.90 | 0.23 | 0.76 | 0.40 | 1.00 |
| 10 | B_fixed_512 | R3_hybrid | 0.87 | 0.87 | 0.27 | 0.74 | 0.40 | 1.00 |
| 11 | A_whole_doc | R1_dense | 0.87 | 0.87 | 0.22 | 0.73 | 0.40 | 0.75 |
| 12 | B_fixed_512 | R1_dense | 0.84 | 0.84 | 0.26 | 0.72 | 0.40 | 0.75 |
| 13 | A_whole_doc | R2_lexical | 0.32 | 0.32 | 0.31 | 0.32 | 0.00 | 1.00 |
| 14 | B_fixed_512 | R2_lexical | 0.29 | 0.29 | 0.27 | 0.29 | 0.00 | 1.00 |
| 15 | C_heading | R2_lexical | 0.23 | 0.23 | 0.21 | 0.23 | 0.00 | 1.00 |
| 16 | D_heading_context | R2_lexical | 0.23 | 0.23 | 0.21 | 0.23 | 0.00 | 1.00 |

**Winner: D_heading_context + R4_hybrid_dated** — hit@5 0.97, MRR 0.87, date correctness 1.00.
