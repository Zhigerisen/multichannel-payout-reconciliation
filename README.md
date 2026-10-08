# Order & Payout Reconciliation — engine

Reconciles ~30,000 e-commerce orders across three sales channels (Amazon,
Shopify, eBay) against the payout statements each channel issues, finds the
money that doesn't tie out, and reports margin per channel.

All data is **synthetic** (fixed seed, no real client) — a defensible demo of
the engine, not a record of a real engagement.

## Pipeline

| Stage | File | What it does |
|-------|------|--------------|
| Generate | `src/generate.py` | 30k orders + 3 payout files (real schemas) + ground-truth labels |
| Reconcile | `src/reconcile.py` | ingest → dedup (SHA-256) → exact + group-and-net → fuzzy fallback → classify → control total → score |
| Visualize | `src/visuals.py` | branded cover + 7 slides (matplotlib, headless) |
| Deliver | `src/make_workbook.py` | client-ready Excel workbook (Dashboard / Reconciliation / Exceptions / Run Log) |

## Run

```bash
pip install -r requirements.txt
python src/generate.py
python src/reconcile.py
python src/visuals.py
python src/make_workbook.py
```

Deterministic (`SEED = 20261006`): identical numbers every run.

## Results on the demo dataset

| Metric | Value |
|---|---|
| Orders / settlement lines | 30,000 / 101,138 |
| GMV | $2,060,124 |
| Net payout | $1,627,104 |
| Platform fees (from fee lines) | $296,973 — 14.42% effective |
| Auto-reconciled | 88.5% |
| Exceptions surfaced | 3,523 |
| **Unreconciled money found** | **$28,275** — $12,088 recoverable + $16,188 loss/risk |
| Breakdown | shipped-never-paid $9,192 · chargebacks $10,445 · fees kept on refunds $5,743 · fee overcharges $2,896 |
| Margin per channel | Amazon 26.1% · eBay 33.7% · Shopify 44.6% |
| Integrity | control total ties to the cent · 292 duplicate lines caught · orphan relink 99% (594/600) |

## Outputs (`out/`)

- `orders.csv` — the seller's order book (order id, channel, totals, expected fee, COGS, status)
- `amazon_settlement.txt` / `shopify_payouts.csv` / `ebay_transactions.csv` — payout statements, three schemas
- `reconciliation.csv` — every order with status, expected, settled, delta, recoverable
- `exceptions.csv` — only the breaks
- `run_log.csv` — run counts, control-total result, runtime
- `dashboard.json` — KPIs, per-channel margin, money-by-status, scores

## Engineering notes

- **Money is integer cents** everywhere — invariants (`net = gross − fee`,
  `payout = Σ(net)`, `total = Σ(detail)`, control total) hold exactly.
- **Group-and-net** is the core: a payout never equals gross sales, so each
  order's settlement lines are grouped and netted before comparison.
- **Fuzzy fallback** relinks orphan lines (missing order id) by their shipment
  reference + amount block + nearest order date, excluding orders already
  matched on a real id.
- **Control total** asserts `matched + exceptions == input` to the cent — it is
  never force-zeroed.
- Verified against a labelled ground-truth set (classification accuracy and
  orphan-relink recall reported in `dashboard.json`).
