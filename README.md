# Ramco Payce support triage pilot

A six-week pilot that answers the cheap, repetitive half of a payroll
provider's support queue and hands the rest to a person, with the reasons
written down.

The repository holds the pipeline, the infrastructure that runs it, the
golden datasets, and the seven gates that decide whether it is allowed to
ship. **No case data is tracked here** — the ticket export, the masked
copies and the model's drafts about them all stay on the machine that ran
them, and `.gitignore` enforces it.

## What the pipeline does

```
ticket ─▶ PII gate ─▶ normalise ─▶ lookup ─▶ classify ─▶ dedupe ─▶ router
                                                                     │
                            ┌────────────────────────────────────────┤
                            ▼                                        ▼
                  answer with citations                       a human, with
                  (retrieve ─▶ draft ─▶ output check)         the reason why
```

Two decisions shaped everything else.

**Rules in code, judgement in the model.** Anything with a right answer —
whether a ticket mentions money, whether it is payroll week, which SLA a
tier carries, whether a draft cites its sources — is a function, not a
sentence in a prompt. A prompt can be argued with; a function cannot.

**Dates are columns, not embeddings.** The statutory store keeps one row per
value period, so "what was the PF ceiling in June" and "what is it now"
retrieve different rows and get different answers. The vendor pilot this
replaces got that wrong, which is how it told a customer the wrong ceiling.

## The gates

Every gate is a number with a pass mark, and every number traces to
something in the brief. `evals/` holds them; `evals/reports/gates.json`
holds the last result.

| Gate | Measures | Pass mark |
|------|----------|-----------|
| G1 | Personal data removed before any model sees it | 0 leaks |
| G2 | Routing accuracy against where humans filed the ticket | ≥ 95% |
| G3 | Duplicate clusters merged, none merged wrongly | 0 wrong merges |
| G4 | Prompt injections blocked before a model call | 100% |
| G5 | Ragas faithfulness, relevancy, context precision/recall | see `evals/` |
| G6 | Output check catches planted faults | 6/6 |
| G7 | Latency and cost per ticket | see `docs/COST.md` |

## Layout

| Path | What |
|------|------|
| `src/pilot/` | one module per box in the diagram |
| `infra/` | CloudFormation: VPC endpoints, two RDS instances, the proxy |
| `scripts/` | load, index, bake-off, price, package, deploy |
| `evals/` | the seven gates and the golden datasets that feed them |
| `web/` | the Next.js UI |
| `docs/` | the plan, the cost sheet, the retrieval bake-off |

## Running it

```bash
source ../aws-env.sh                 # folder-scoped credentials, never ~/.aws
uv venv --python 3.12 && VIRTUAL_ENV=$PWD/.venv uv pip install -r requirements-dev.txt
.venv/bin/python scripts/load_data.py        # masks, then loads
.venv/bin/python scripts/index_corpus.py     # four chunking strategies
.venv/bin/python scripts/bakeoff.py          # pick chunking × retrieval
.venv/bin/python evals/test_g1_pii.py        # …and the rest of the gates
```

The virtualenv is Python 3.12 on purpose: it is the version AgentCore runs,
so what passes here is what ships.
