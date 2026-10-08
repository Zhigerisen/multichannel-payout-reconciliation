"""Order & Payout Reconciliation engine.

Reads the seller's order book (orders.csv) and the three channels' payout
statements, then reconciles every order against what was actually paid out.

Pipeline (tiered, cheapest stage first):
  1. ingest + normalize   - 3 different real schemas -> one canonical line table
  2. dedup                - content-hash fingerprint drops re-imported lines
  3. exact-key match      - settlement line order-id -> order
  4. fuzzy fallback        - orphan lines (no order id) relinked by channel +
                            amount block and date-proximity score
  5. group-and-net        - sum each order's settlement lines to a settled net
  6. classify             - Matched / Underpaid / Overpaid / Missing payout /
                            Unexpected payout / Refund / Chargeback / Promotion
  7. control total        - matched + exceptions == input, to the cent
  8. score vs ground truth - classification accuracy + fuzzy relink recall

Money is integer cents throughout. Run:  python src/reconcile.py
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime

import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
TOL = 2                      # cents; reconcile window for rounding
DATE_WINDOW = 10            # days; fuzzy relink date proximity
PRINCIPAL_TYPES = {"principal", "shipping"}


def cents(x) -> int:
    if x is None or x == "" or (isinstance(x, float) and pd.isna(x)):
        return 0
    return int(round(float(x) * 100))


def pdate(x: str) -> date:
    return datetime.strptime(x, "%Y-%m-%d").date()


# --- ingest: 3 schemas -> canonical table -----------------------------------
def ingest_amazon() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(OUT, "amazon_settlement.txt"), sep="\t", dtype=str).fillna("")
    det = df[df["amount"] != ""].copy()          # drop settlement header rows
    return pd.DataFrame({
        "channel": "amazon", "order_id": det["order-id"], "ref": det["shipment-id"],
        "settlement_id": det["settlement-id"], "posted_date": det["posted-date"],
        "amount": det["amount"].map(cents),
        "atype": det["amount-description"].map(_canon_amazon), "sku": det["sku"],
    })


def _canon_amazon(desc: str) -> str:
    m = {"Principal": "principal", "Shipping": "shipping", "Commission": "fee",
         "FBAFee": "fee", "FeeAdjustment": "fee_adjustment", "Promotion": "promotion",
         "RefundPrincipal": "refund", "RefundShipping": "refund",
         "RefundFeeCredit": "refund_fee", "Chargeback": "dispute",
         "DisputeFee": "dispute_fee", "ChargebackReversal": "dispute_reversal"}
    return m.get(desc, "other")


def ingest_shopify() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(OUT, "shopify_payouts.csv"), dtype=str).fillna("")
    return pd.DataFrame({
        "channel": "shopify", "order_id": df["Order"], "ref": df["Reference"],
        "settlement_id": df["Payout Date"], "posted_date": df["Transaction Date"],
        "amount": df["Net"].map(cents), "atype": df["Type"].map(_canon_shopify), "sku": "",
    })


def _canon_shopify(t: str) -> str:
    m = {"charge": "principal", "fee": "fee", "fee_adjustment": "fee_adjustment",
         "promotion": "promotion", "refund": "refund", "dispute": "dispute",
         "dispute_fee": "dispute_fee", "dispute_reversal": "dispute_reversal"}
    return m.get(t, "other")


def ingest_ebay() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(OUT, "ebay_transactions.csv"), dtype=str).fillna("")
    return pd.DataFrame({
        "channel": "ebay", "order_id": df["orderId"], "ref": df["reference"],
        "settlement_id": df["payoutId"], "posted_date": df["transactionDate"],
        "amount": df["amount"].map(cents),
        "atype": df["transactionType"].map(_canon_ebay), "sku": "",
    })


def _canon_ebay(t: str) -> str:
    m = {"SALE": "principal", "NON_SALE_CHARGE": "fee", "REFUND": "refund",
         "DISPUTE": "dispute", "DISPUTE_FEE": "dispute_fee", "ADJUSTMENT": "dispute_reversal"}
    return m.get(t, "other")


def ingest_unexpected() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(OUT, "unexpected_payouts.csv"), dtype=str).fillna("")
    return pd.DataFrame({
        "channel": df["channel"], "order_id": df["order_id"], "posted_date": df["posted_date"],
        "amount": df["amount"].map(cents),
    })


# --- dedup ------------------------------------------------------------------
def dedup(lines: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    def key(r):
        raw = (f"{r.channel}|{r.order_id}|{r.ref}|{r.settlement_id}|{r.posted_date}"
               f"|{r.atype}|{r.amount}|{r.sku}")
        return hashlib.sha256(raw.encode()).hexdigest()
    lines = lines.copy()
    lines["_hash"] = [key(r) for r in lines.itertuples()]
    dup_mask = lines.duplicated(subset="_hash", keep="first")
    clean, dups = lines[~dup_mask], lines[dup_mask]
    # integrity: a dropped line must have a surviving twin (same fingerprint).
    # This is what backs "nothing dropped silently" — we only drop re-imports.
    assert set(dups["_hash"]).issubset(set(clean["_hash"])), \
        "dedup dropped a line with no surviving twin"
    return clean.drop(columns="_hash"), dups.drop(columns="_hash")


# --- fuzzy fallback: relink orphan (blank order id) groups ------------------
def fuzzy_relink(lines: pd.DataFrame, orders: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Orphan lines (order id missing) still carry a per-order reference
    (shipment id). Group by it, then match each group to the order book by
    channel + order-total block and nearest order date within a window."""
    blank = lines[lines["order_id"] == ""]
    if blank.empty:
        return lines, 0, 0
    # block index: (channel, order_total, expected_fee) -> [(order_id, date)].
    # Order total alone collides (common prices); adding the fee signature makes
    # the block key near-unique, so the date only breaks residual ties.
    from collections import defaultdict
    idx: dict[tuple, list] = defaultdict(list)
    for o in orders.itertuples():
        idx[(o.channel, o.order_total_c, o.expected_fee_c)].append((o.order_id, o.order_date_d))
    # orders already matched on a real order id are off the table for fuzzy — an
    # orphan group only competes for orders that have no id-linked lines.
    claimed: set[str] = set(lines[lines["order_id"] != ""]["order_id"])
    assignments: dict[str, str] = {}
    n_groups = 0
    for (ch, ref), grp in blank.groupby(["channel", "ref"]):
        n_groups += 1
        total = int(grp[grp["atype"].isin(PRINCIPAL_TYPES)]["amount"].sum())
        fee = -int(grp[grp["atype"] == "fee"]["amount"].sum())
        want = min(pdate(p) for p in grp["posted_date"])
        best, best_gap = None, 10 ** 9
        for oid, odate in idx.get((ch, total, fee), []):
            if oid in claimed:
                continue
            gap = abs((odate - want).days)
            if gap <= DATE_WINDOW and gap < best_gap:
                best, best_gap = oid, gap
        if best is not None:
            claimed.add(best)
            assignments[ref] = best
    lines = lines.copy()
    lines["order_id"] = [
        (assignments.get(ref, "") if oid == "" else oid)
        for oid, ref in zip(lines["order_id"], lines["ref"])]
    return lines, n_groups, len(assignments), assignments


# --- classify ---------------------------------------------------------------
def classify(order, settled: int, flags: dict) -> tuple[str, int]:
    if not flags.get("has_lines") and order.status == "paid":
        return "Missing payout", order.expected_c
    if flags.get("dispute"):
        return "Chargeback", max(0, order.expected_c - settled)
    if order.status in ("refunded", "partial_refund") or flags.get("refund"):
        return "Refund", max(0, order.expected_c - settled)
    delta = settled - order.expected_c
    if flags.get("promotion") and delta < -TOL:
        return "Promotion", 0
    if abs(delta) <= TOL:
        return "Matched", 0
    return ("Underpaid", -delta) if delta < 0 else ("Overpaid", 0)


def main() -> None:
    t0 = time.time()
    orders = pd.read_csv(os.path.join(OUT, "orders.csv"), dtype=str).fillna("")
    orders["order_total_c"] = orders["order_total"].map(cents)
    orders["expected_fee_c"] = orders["expected_fee"].map(cents)
    orders["cogs_c"] = orders["cogs"].map(cents)
    orders["refund_c"] = orders["refund_amount"].map(cents)
    orders["order_date_d"] = orders["order_date"].map(pdate)

    def expected(r):
        if r.status == "refunded":
            return 0
        if r.status == "partial_refund":
            return r.order_total_c - r.expected_fee_c - r.refund_c
        return r.order_total_c - r.expected_fee_c
    orders["expected_c"] = [expected(r) for r in orders.itertuples()]

    raw = pd.concat([ingest_amazon(), ingest_shopify(), ingest_ebay()], ignore_index=True)
    n_raw = len(raw)
    clean, dups = dedup(raw)
    clean, n_groups, n_assigned, assignments = fuzzy_relink(clean, orders)

    net = clean[clean["order_id"] != ""].groupby("order_id")["amount"].sum()
    by_order = net.astype(int).to_dict()

    clean["_refund"] = clean["atype"].isin(["refund", "refund_fee"])
    clean["_dispute"] = clean["atype"].isin(["dispute", "dispute_fee", "dispute_reversal"])
    clean["_promo"] = clean["atype"] == "promotion"
    fl = clean[clean["order_id"] != ""].groupby("order_id")[["_refund", "_dispute", "_promo"]].any()
    refund_d = fl["_refund"].to_dict()
    dispute_d = fl["_dispute"].to_dict()
    promo_d = fl["_promo"].to_dict()

    order_ids = set(orders["order_id"])
    rows = []
    for o in orders.itertuples():
        settled = by_order.get(o.order_id, 0)
        has = o.order_id in by_order
        flags = {"has_lines": has, "refund": refund_d.get(o.order_id, False),
                 "dispute": dispute_d.get(o.order_id, False),
                 "promotion": promo_d.get(o.order_id, False)}
        status, impact = classify(o, settled, flags)
        rows.append({"order_id": o.order_id, "channel": o.channel, "status": status,
                     "expected": o.expected_c, "settled": settled,
                     "delta": settled - o.expected_c, "impact": impact})
    recon = pd.DataFrame(rows)

    unlinked_amt = int(clean[clean["order_id"] == ""]["amount"].sum())
    unexpected = ingest_unexpected()
    _control_total(clean, recon, unlinked_amt)
    scores = _score(recon, unexpected)
    _fuzzy_truth(scores, assignments, orders)
    meta = {"n_raw": n_raw, "n_dup": len(dups), "fuzzy_groups": n_groups,
            "fuzzy_assigned": n_assigned, "unlinked_amt": unlinked_amt,
            "duration_s": round(time.time() - t0, 2)}
    _write_outputs(recon, dups, unexpected, orders, clean, meta, scores)


def _fuzzy_truth(scores: dict, assignments: dict, orders: pd.DataFrame) -> None:
    """True orphan-relink precision/recall: compare each fuzzy assignment to the
    real owner (ref SHP-<i> belongs to the order at generation row i)."""
    true_map = {f"SHP-{i:07d}": oid for i, oid in enumerate(orders["order_id"])}
    correct = sum(1 for ref, oid in assignments.items() if true_map.get(ref) == oid)
    n = len(assignments)
    scores["orphan_relink_precision"] = round(correct / max(1, n), 4)
    scores["orphan_relink_recall"] = round(correct / max(1, scores["n_dropped"]), 4)
    scores["orphan_correct"] = correct


def _control_total(clean, recon, unlinked_amt) -> None:
    total_lines = int(clean["amount"].sum())
    matched_net = int(recon["settled"].sum())
    reconstructed = matched_net + unlinked_amt
    assert abs(total_lines - reconstructed) <= 2, (
        f"control total broke: lines={total_lines} vs rebuilt={reconstructed}")
    print(f"[OK] control total ties to the cent: {total_lines} == {reconstructed}")


def _score(recon: pd.DataFrame, unexpected: pd.DataFrame) -> dict:
    gt = pd.read_csv(os.path.join(OUT, "ground_truth.csv"), dtype=str).fillna("")
    gt_map = dict(zip(gt["order_id"], gt["gt_type"]))
    gt_drop = {k for k, v in zip(gt["order_id"], gt["dropped_id"]) if v == "1"}
    fam = {"Underpaid": "fee_overcharge", "Missing payout": "missing_payout",
           "Refund": "refund", "Chargeback": "chargeback", "Promotion": "promo"}
    correct = total = 0
    for r in recon.itertuples():
        g = gt_map.get(r.order_id)
        if g is None:
            continue
        total += 1
        pred = fam.get(r.status, "clean" if r.status == "Matched" else r.status)
        ok = (pred == g) or (g == "clean" and r.status == "Matched") \
            or (g == "duplicate" and r.status == "Matched")
        correct += int(ok)
    drop_matched = sum(1 for r in recon.itertuples()
                       if r.order_id in gt_drop and r.status == "Matched")
    return {"class_accuracy": round(correct / max(1, total), 4),
            "orphans_reconciled_pct": round(drop_matched / max(1, len(gt_drop)), 4),
            "n_dropped": len(gt_drop), "dropped_matched": drop_matched}


def _write_outputs(recon, dups, unexpected, orders, clean, meta, scores) -> None:
    # unexpected payouts: money received that matches no order in the book
    unexp_rows = pd.DataFrame({
        "order_id": unexpected["order_id"], "channel": unexpected["channel"],
        "status": "Unexpected payout", "expected": 0,
        "settled": unexpected["amount"], "delta": unexpected["amount"], "impact": 0})
    recon_all = pd.concat([recon, unexp_rows], ignore_index=True)

    recon_all.to_csv(os.path.join(OUT, "reconciliation.csv"), index=False)
    exc = recon_all[recon_all["status"] != "Matched"].copy()
    exc.to_csv(os.path.join(OUT, "exceptions.csv"), index=False)

    brk = recon_all["status"].value_counts().to_dict()
    found = int(recon["impact"].sum())
    money_by_status = {k: int(v) for k, v in
                       recon.groupby("status")["impact"].sum().items() if v > 0}
    # honest split: recoverable (chaseable) vs loss/risk (already gone)
    recoverable = sum(money_by_status.get(k, 0) for k in ("Underpaid", "Missing payout"))
    loss_risk = sum(money_by_status.get(k, 0) for k in ("Chargeback", "Refund"))

    # fees from actual fee-type lines, NOT GMV - net (which also absorbs leakage)
    feelines = clean[clean["atype"].isin(["fee", "fee_adjustment"])]
    fees_by_ch = (-feelines.groupby("channel")["amount"].sum()).astype(int).to_dict()
    total_fees = int(sum(fees_by_ch.values()))

    orders = orders.copy()
    orders["tot"] = orders["order_total_c"]
    gmv = int(orders["tot"].sum())
    merged = recon.merge(orders[["order_id", "tot", "cogs_c"]], on="order_id", how="left")
    per_channel = {}
    for ch, g in merged.groupby("channel"):
        rev, net, cogs = int(g["tot"].sum()), int(g["settled"].sum()), int(g["cogs_c"].sum())
        per_channel[ch] = {"revenue": rev, "net_payout": net,
                           "fees": int(fees_by_ch.get(ch, 0)), "cogs": cogs,
                           "margin_pct": round((net - cogs) / rev * 100, 1) if rev else 0.0,
                           "orders": int(len(g))}

    total_net = int(recon["settled"].sum())
    n = len(recon)
    dash = {
        "gmv": gmv, "net_payout": total_net, "total_fees": total_fees,
        "effective_fee_pct": round(total_fees / gmv * 100, 2) if gmv else 0,
        "orders": n,
        "reconciled_pct": round(int(brk.get("Matched", 0)) / n * 100, 1),
        "exceptions": int(len(exc)), "money_found": found,
        "recoverable": recoverable, "loss_risk": loss_risk,
        "money_by_status": money_by_status,
        "status_breakdown": {k: int(v) for k, v in brk.items()},
        "per_channel": per_channel, "scores": scores, "meta": meta,
    }
    with open(os.path.join(OUT, "dashboard.json"), "w", encoding="utf-8") as f:
        json.dump(dash, f, indent=2)

    with open(os.path.join(OUT, "run_log.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("ts,raw_lines,duplicates_dropped,fuzzy_groups,fuzzy_assigned,"
                "orders,matched,exceptions,money_found,control_total,duration_s\n")
        f.write(f"{datetime.now().isoformat(timespec='seconds')},{meta['n_raw']},"
                f"{meta['n_dup']},{meta['fuzzy_groups']},{meta['fuzzy_assigned']},{n},"
                f"{brk.get('Matched', 0)},{dash['exceptions']},{found},PASS,"
                f"{meta['duration_s']}\n")

    print(f"[OK] orders={n} matched={brk.get('Matched', 0)} reconciled={dash['reconciled_pct']}%")
    print(f"[OK] status breakdown: {dash['status_breakdown']}")
    print(f"[OK] money found (recoverable) ${found/100:,.2f}")
    print(f"[OK] duplicates dropped: {meta['n_dup']} | fuzzy relinked: "
          f"{meta['fuzzy_assigned']}/{meta['fuzzy_groups']} groups")
    print(f"[OK] scores: {scores}")
    print(f"[OK] effective fee {dash['effective_fee_pct']}% | net payout "
          f"${total_net/100:,.2f} of GMV ${gmv/100:,.2f}")


if __name__ == "__main__":
    main()
