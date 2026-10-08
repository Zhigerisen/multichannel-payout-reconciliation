"""Synthetic data generator for the Order & Payout Reconciliation case.

Produces a realistic ~30,000-order multi-channel dataset plus the payout
statements each channel would issue, with a controlled set of injected
discrepancies and a ground-truth label file so the engine can be scored.

No real client data. Everything is procedurally generated from a fixed seed,
so the dataset is fully reproducible and defensible as synthetic.

Money is handled in INTEGER CENTS everywhere; dollars appear only at
serialization. This keeps every invariant (net = gross - fee,
payout = sum(lines), settlement total = sum(detail)) exact to the cent.

Run:  python src/generate.py
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

SEED = 20261006
OUT = os.path.join(os.path.dirname(__file__), "..", "out")

N_ORDERS = 30_000
START = date(2025, 7, 1)
END = date(2026, 6, 30)
SPAN_DAYS = (END - START).days

CHANNELS = ["amazon", "shopify", "ebay"]
CHANNEL_MIX = [0.45, 0.30, 0.25]

# --- fee models (rates applied to integer cents) -------------------------
AMAZON_REFERRAL = 0.15
SHOPIFY_RATE, SHOPIFY_FIXED = 0.029, 30          # 2.9% + $0.30
EBAY_RATE = 0.136                                 # final value fee ~13.6%
DISPUTE_FEE = 1500                                # $15, non-refundable

# --- discrepancy injection rates (share of orders) -----------------------
R_REFUND_FULL = 0.05
R_REFUND_PARTIAL = 0.02
R_FEE_OVERCHARGE = 0.015
R_DUPLICATE = 0.003
R_MISSING_PAYOUT = 0.005
R_DISPUTE = 0.007
R_DISPUTE_REVERSED = 0.40      # of disputes
R_PROMO = 0.04
R_TIMING = 0.10                # settled in the next period (explained)
R_MISSING_ID = 0.02            # settlement line loses its order id (fuzzy)
N_UNEXPECTED = 60              # payout lines with no matching order

rng = np.random.default_rng(SEED)


# --- helpers -------------------------------------------------------------
def rcents(x: float) -> int:
    """Round a cents value, half-up, to a whole cent."""
    return int(np.floor(x + 0.5))


def d(n: int) -> str:
    return f"{n / 100:.2f}"


def seasonal_dates(n: int) -> list[date]:
    days = np.arange(SPAN_DAYS + 1)
    dates = [START + timedelta(days=int(x)) for x in days]
    weights = np.array([1.8 if dt.month in (10, 11, 12) else 1.0 for dt in dates])
    weights = weights / weights.sum()
    picks = rng.choice(len(dates), size=n, p=weights)
    return [dates[i] for i in picks]


# --- SKU catalogue (Pareto demand, per-SKU cost basis) -------------------
def build_skus(n_sku: int = 500):
    skus = []
    for i in range(n_sku):
        base = int(rng.lognormal(mean=3.6, sigma=0.6))  # ~$10-200 base
        price_cents = max(499, rcents(base * 100))
        cost_cents = rcents(price_cents * rng.uniform(0.35, 0.65))
        fba_tier = rng.choice([349, 449, 599, 899])      # flat FBA fee by size
        skus.append({
            "sku": f"HL-{i:04d}",
            "price": price_cents,
            "cost": cost_cents,
            "fba": int(fba_tier),
        })
    pop = rng.pareto(a=1.2, size=n_sku) + 1              # 20% of SKUs ~80% demand
    pop = pop / pop.sum()
    return skus, pop


@dataclass
class Line:
    channel: str
    settlement_id: str
    order_id: str
    posted_date: date
    deposit_date: date
    amount_type: str
    amount: int          # signed cents
    sku: str = ""
    qty: int = 0
    ref: str = ""        # per-order reference (shipment id) — survives id loss


@dataclass
class Order:
    order_id: str
    channel: str
    order_date: date
    sku: str
    qty: int
    item_cents: int      # unit price
    ship_cents: int
    customer: str
    currency: str = "USD"
    status: str = "paid"          # paid|refunded|partial_refund|missing_payout|disputed
    expected_net: int = 0
    settled_net: int = 0
    gt_type: str = "clean"        # ground-truth discrepancy label
    loss: int = 0
    timing_defer: bool = False
    lines: list = field(default_factory=list)


def channel_order_id(channel: str, i: int) -> str:
    if channel == "amazon":
        a = rng.integers(100, 999)
        b = rng.integers(1_000_000, 9_999_999)
        c = rng.integers(1_000_000, 9_999_999)
        return f"{a}-{b}-{c}"
    if channel == "shopify":
        return f"#{10000 + i}"
    return f"{rng.integers(10, 39)}-{rng.integers(10000, 99999)}-{rng.integers(10000, 99999)}"


def fee_for(channel: str, item_total: int, ship: int, sku_fba: int):
    """Return (total_fee_cents, components dict) for a fully-paid order."""
    gross = item_total + ship
    if channel == "amazon":
        commission = rcents(AMAZON_REFERRAL * item_total)
        comp = {"Commission": commission, "FBAFee": sku_fba}
        return commission + sku_fba, comp
    if channel == "shopify":
        fee = rcents(SHOPIFY_RATE * gross) + SHOPIFY_FIXED
        return fee, {"ProcessingFee": fee}
    fixed = 30 if gross <= 1000 else 40
    fvf = rcents(EBAY_RATE * gross)
    return fvf + fixed, {"FinalValueFee": fvf, "FixedFee": fixed}


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    skus, pop = build_skus()
    dates = seasonal_dates(N_ORDERS)
    channels = rng.choice(CHANNELS, size=N_ORDERS, p=CHANNEL_MIX)

    orders: list[Order] = []
    for i in range(N_ORDERS):
        ch = str(channels[i])
        s = skus[int(rng.choice(len(skus), p=pop))]
        qty = int(rng.choice([1, 1, 1, 2, 2, 3]))
        item_total = s["price"] * qty
        ship = int(rng.choice([0, 0, 0, 399, 599, 799]))
        cust = f"buyer{int(rng.integers(1, 99999)):05d}@example.com"
        o = Order(
            order_id=channel_order_id(ch, i),
            channel=ch,
            order_date=dates[i],
            sku=s["sku"],
            qty=qty,
            item_cents=s["price"],
            ship_cents=ship,
            customer=cust,
        )
        o._item_total = item_total          # type: ignore[attr-defined]
        o._fba = s["fba"]                    # type: ignore[attr-defined]
        o._cogs = s["cost"] * qty            # type: ignore[attr-defined]
        o._refund_amt = 0                     # type: ignore[attr-defined]
        o._ref = f"SHP-{i:07d}"              # type: ignore[attr-defined]
        orders.append(o)

    _assign_outcomes(orders)
    _build_settlements(orders)

    _write_orders(orders)
    _write_ground_truth(orders)
    _write_amazon(orders)
    _write_shopify(orders)
    _write_ebay(orders)
    _write_unexpected(orders)
    _summary(orders)


def _assign_outcomes(orders: list[Order]) -> None:
    n = len(orders)
    idx = rng.permutation(n)
    cursor = 0

    def take(rate):
        nonlocal cursor
        k = int(rate * n)
        sl = idx[cursor:cursor + k]
        cursor += k
        return set(int(x) for x in sl)

    refunded = take(R_REFUND_FULL)
    partial = take(R_REFUND_PARTIAL)
    overcharge = take(R_FEE_OVERCHARGE)
    duplicate = take(R_DUPLICATE)
    missing = take(R_MISSING_PAYOUT)
    disputed = take(R_DISPUTE)
    promo = take(R_PROMO)
    timing = take(R_TIMING)
    missing_id = take(R_MISSING_ID)

    for i, o in enumerate(orders):
        item_total = o._item_total          # type: ignore[attr-defined]
        fee, _ = fee_for(o.channel, item_total, o.ship_cents, o._fba)  # type: ignore[attr-defined]
        o._fee = fee                         # type: ignore[attr-defined]
        o.expected_net = item_total + o.ship_cents - fee
        o.timing_defer = i in timing
        if i in missing:
            o.status = "missing_payout"
            o.gt_type = "missing_payout"
            o.loss = o.expected_net
        elif i in disputed:
            o.status = "disputed"
            o._reversed = rng.random() < R_DISPUTE_REVERSED   # type: ignore[attr-defined]
            o.gt_type = "chargeback"
        elif i in refunded:
            o.status = "refunded"
            o.gt_type = "refund"
        elif i in partial:
            o.status = "partial_refund"
            o.gt_type = "refund"
        if i in overcharge and o.status == "paid":
            o._overcharge = rcents(fee * rng.uniform(0.3, 0.8)) + 100  # type: ignore[attr-defined]
            o.gt_type = "fee_overcharge"
            o.loss = o._overcharge                                     # type: ignore[attr-defined]
        if i in duplicate and o.status == "paid":
            o._dup = True                    # type: ignore[attr-defined]
            o.gt_type = "duplicate"
        # promo only on Amazon: there it serializes as a distinct "Promotion"
        # amount-description. Shopify/eBay lump it with other adjustments, so a
        # promo there would be indistinguishable from a fee overcharge.
        if i in promo and o.status == "paid" and o.gt_type == "clean" and o.channel == "amazon":
            o._promo = rcents(item_total * rng.uniform(0.05, 0.15))    # type: ignore[attr-defined]
            o.gt_type = "promo"
        # drop the order id from a clean paid order's lines -> exercises the
        # fuzzy fallback (relink by channel + amount block + date proximity).
        if i in missing_id and o.gt_type == "clean" and o.status == "paid":
            o._drop_id = True                # type: ignore[attr-defined]


def _period(channel: str, dt: date) -> tuple[str, date, date]:
    if channel == "amazon":
        epoch = (dt - START).days // 14
        sid = f"{19000000000 + epoch}"
        start = START + timedelta(days=epoch * 14)
        deposit = start + timedelta(days=16)
        return sid, dt, deposit
    if channel == "shopify":
        avail = dt + timedelta(days=3)
        return f"{70000000 + (avail - START).days}", dt, avail
    avail = dt + timedelta(days=2)
    return f"EBY-{(avail - START).days:04d}", dt, avail


def _build_settlements(orders: list[Order]) -> None:
    for o in orders:
        if o.status == "missing_payout":
            o.settled_net = 0
            continue
        item_total = o._item_total           # type: ignore[attr-defined]
        fba = o._fba                          # type: ignore[attr-defined]
        _, comp = fee_for(o.channel, item_total, o.ship_cents, fba)
        post = o.order_date + timedelta(days=14 if o.timing_defer else 1)
        sid, posted, deposit = _period(o.channel, post)
        oid = o.order_id
        L = o.lines

        L.append(Line(o.channel, sid, oid, posted, deposit, "Principal",
                      item_total, o.sku, o.qty))
        if o.ship_cents:
            L.append(Line(o.channel, sid, oid, posted, deposit, "Shipping", o.ship_cents))
        for k, v in comp.items():
            L.append(Line(o.channel, sid, oid, posted, deposit, k, -v))
        if o.gt_type == "fee_overcharge":
            L.append(Line(o.channel, sid, oid, posted, deposit, "FeeAdjustment",
                          -o._overcharge))    # type: ignore[attr-defined]
        if o.gt_type == "promo":
            L.append(Line(o.channel, sid, oid, posted, deposit, "Promotion",
                          -o._promo))          # type: ignore[attr-defined]

        _refund_and_dispute_lines(o, comp)

        if getattr(o, "_dup", False):
            o.lines = o.lines + [Line(x.channel, x.settlement_id, x.order_id,
                                      x.posted_date, x.deposit_date, x.amount_type,
                                      x.amount, x.sku, x.qty) for x in list(o.lines)]

        for ln in o.lines:
            ln.ref = o._ref                   # type: ignore[attr-defined]
        if getattr(o, "_drop_id", False):
            for ln in o.lines:
                ln.order_id = ""              # ref stays — that is what fuzzy uses

        o.settled_net = sum(ln.amount for ln in o.lines)


def _refund_and_dispute_lines(o: Order, comp: dict) -> None:
    item_total = o._item_total               # type: ignore[attr-defined]
    sid = o.lines[0].settlement_id
    posted = o.lines[0].posted_date + timedelta(days=5)
    dep = o.lines[0].deposit_date + timedelta(days=5)
    oid = o.order_id

    if o.status == "refunded":
        o.lines.append(Line(o.channel, sid, oid, posted, dep, "RefundPrincipal", -item_total))
        if o.ship_cents:
            o.lines.append(Line(o.channel, sid, oid, posted, dep, "RefundShipping", -o.ship_cents))
        refundable, non_ref = _refund_split(o.channel, comp)
        if refundable:
            o.lines.append(Line(o.channel, sid, oid, posted, dep, "RefundFeeCredit", refundable))
        o.expected_net = 0
        o.loss = non_ref
    elif o.status == "partial_refund":
        part = rcents(item_total * rng.uniform(0.3, 0.6))
        o.lines.append(Line(o.channel, sid, oid, posted, dep, "RefundPrincipal", -part))
        o._refund_amt = part                 # type: ignore[attr-defined]
        o.loss = rcents(o._fee * 0.2)        # type: ignore[attr-defined]
    elif o.status == "disputed":
        o.lines.append(Line(o.channel, sid, oid, posted, dep, "Chargeback", -item_total))
        o.lines.append(Line(o.channel, sid, oid, posted, dep, "DisputeFee", -DISPUTE_FEE))
        if getattr(o, "_reversed", False):
            o.lines.append(Line(o.channel, sid, oid, posted, dep, "ChargebackReversal", item_total))
            o.loss = DISPUTE_FEE
        else:
            o.loss = item_total + DISPUTE_FEE


def _refund_split(channel: str, comp: dict) -> tuple[int, int]:
    """(refundable_fee_credit, non_refundable_fee) when a sale is refunded."""
    if channel == "shopify":
        return 0, comp["ProcessingFee"]                 # fee never returned
    if channel == "amazon":
        commission = comp["Commission"]
        admin = min(500, rcents(commission * 0.2))       # refund admin fee
        return commission - admin, comp["FBAFee"] + admin
    return comp["FinalValueFee"], comp["FixedFee"]       # ebay: variable back, fixed kept


# --- serialization -------------------------------------------------------
def _write_orders(orders: list[Order]) -> None:
    with open(os.path.join(OUT, "orders.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "channel", "order_date", "sku", "qty",
                    "item_price", "shipping", "order_total", "expected_fee", "cogs",
                    "refund_amount", "customer_email", "status"])
        for o in orders:
            total = o._item_total + o.ship_cents        # type: ignore[attr-defined]
            # seller-visible status: the store never knows a payout is missing or
            # disputed from its own order record — the engine discovers those.
            seller_status = o.status if o.status in ("refunded", "partial_refund") else "paid"
            w.writerow([o.order_id, o.channel, o.order_date.isoformat(), o.sku, o.qty,
                        d(o.item_cents), d(o.ship_cents), d(total),
                        d(o._fee), d(o._cogs), d(o._refund_amt),   # type: ignore[attr-defined]
                        o.customer, seller_status])


def _write_ground_truth(orders: list[Order]) -> None:
    with open(os.path.join(OUT, "ground_truth.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "channel", "gt_type", "expected_net", "settled_net",
                    "loss", "timing_defer", "dropped_id"])
        for o in orders:
            w.writerow([o.order_id, o.channel, o.gt_type, d(o.expected_net), d(o.settled_net),
                        d(o.loss), int(o.timing_defer), int(getattr(o, "_drop_id", False))])


def _write_amazon(orders: list[Order]) -> None:
    rows = [o for o in orders if o.channel == "amazon" and o.lines]
    settlements: dict[str, list[Line]] = {}
    for o in rows:
        for ln in o.lines:
            settlements.setdefault(ln.settlement_id, []).append(ln)
    path = os.path.join(OUT, "amazon_settlement.txt")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["settlement-id", "settlement-start-date", "settlement-end-date",
                    "deposit-date", "total-amount", "currency", "transaction-type",
                    "order-id", "shipment-id", "amount-type", "amount-description", "amount",
                    "posted-date", "sku", "quantity-purchased"])
        for sid, lines in sorted(settlements.items()):
            total = sum(ln.amount for ln in lines)
            dep = lines[0].deposit_date
            starts = min(ln.posted_date for ln in lines)
            ends = max(ln.posted_date for ln in lines)
            w.writerow([sid, starts.isoformat(), ends.isoformat(), dep.isoformat(),
                        d(total), "USD", "", "", "", "", "", "", "", "", ""])
            for ln in lines:
                w.writerow([sid, "", "", "", "", "USD", _amazon_ttype(ln.amount_type),
                            ln.order_id, ln.ref, _amazon_atype(ln.amount_type), ln.amount_type,
                            d(ln.amount), ln.posted_date.isoformat(), ln.sku, ln.qty or ""])


def _amazon_ttype(at: str) -> str:
    if at.startswith("Refund"):
        return "Refund"
    if at in ("Chargeback", "DisputeFee", "ChargebackReversal"):
        return "Adjustment"
    return "Order"


def _amazon_atype(at: str) -> str:
    if at in ("Principal", "Shipping", "RefundPrincipal", "RefundShipping",
              "ChargebackReversal", "Chargeback"):
        return "ItemPrice"
    if at in ("Commission", "FBAFee", "FeeAdjustment", "RefundFeeCredit", "DisputeFee"):
        return "ItemFees"
    if at == "Promotion":
        return "Promotion"
    return "other-transaction"


def _write_shopify(orders: list[Order]) -> None:
    """Per-line balance-transaction export. Net column is the source of truth;
    sum(Net) over an order's rows == that order's settled net."""
    rows = [o for o in orders if o.channel == "shopify" and o.lines]
    path = os.path.join(OUT, "shopify_payouts.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Transaction Date", "Type", "Order", "Reference", "Card Brand", "Card Source",
                    "Payout Status", "Payout Date", "Available On", "Amount", "Fee", "Net"])
        for o in rows:
            brand = str(rng.choice(["Visa", "Mastercard", "Amex"]))
            dep = o.lines[0].deposit_date
            for ln in o.lines:
                typ = _shopify_type(ln.amount_type)
                is_fee = typ in ("fee", "dispute_fee")
                amount = 0 if is_fee else ln.amount
                fee = -ln.amount if is_fee else 0
                w.writerow([ln.posted_date.isoformat(), typ, ln.order_id, ln.ref, brand, "chip",
                            "paid", dep.isoformat(), dep.isoformat(),
                            d(amount), d(fee), d(ln.amount)])


def _shopify_type(at: str) -> str:
    if at in ("Principal", "Shipping"):
        return "charge"
    if at == "ProcessingFee":
        return "fee"
    if at == "FeeAdjustment":
        return "fee_adjustment"
    if at == "Promotion":
        return "promotion"
    if at.startswith("Refund"):
        return "refund"
    if at == "Chargeback":
        return "dispute"
    if at == "DisputeFee":
        return "dispute_fee"
    if at == "ChargebackReversal":
        return "dispute_reversal"
    return "adjustment"


def _write_ebay(orders: list[Order]) -> None:
    """Per-line Finances-API style export; amount column carries the signed net
    of each transaction, so sum(amount) over an order == its settled net."""
    rows = [o for o in orders if o.channel == "ebay" and o.lines]
    path = os.path.join(OUT, "ebay_transactions.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["transactionId", "transactionType", "bookingEntry", "orderId", "reference",
                    "amount", "totalFeeAmount", "payoutId", "payoutDate", "transactionDate"])
        tid = 0
        for o in rows:
            dep = o.lines[0].deposit_date
            for ln in o.lines:
                tid += 1
                ttype = _ebay_type(ln.amount_type)
                booking = "CREDIT" if ln.amount >= 0 else "DEBIT"
                w.writerow([f"T{tid:08d}", ttype, booking, ln.order_id, ln.ref, d(ln.amount), "0.00",
                            ln.settlement_id, dep.isoformat(), ln.posted_date.isoformat()])


def _ebay_type(at: str) -> str:
    if at in ("Principal", "Shipping"):
        return "SALE"
    if at in ("FinalValueFee", "FixedFee", "FeeAdjustment"):
        return "NON_SALE_CHARGE"
    if at.startswith("Refund"):
        return "REFUND"
    if at == "Chargeback":
        return "DISPUTE"
    if at == "DisputeFee":
        return "DISPUTE_FEE"          # distinct type: never hash-collides with the -$ principal
    if at == "ChargebackReversal":
        return "ADJUSTMENT"
    return "ADJUSTMENT"


def _write_unexpected(orders: list[Order]) -> None:
    """Payout lines whose order ids never appear in orders.csv."""
    path = os.path.join(OUT, "unexpected_payouts.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["channel", "order_id", "posted_date", "amount"])
        for _ in range(N_UNEXPECTED):
            ch = str(rng.choice(CHANNELS))
            oid = channel_order_id(ch, int(rng.integers(900000, 999999)))
            dt = (START + timedelta(days=int(rng.integers(0, SPAN_DAYS)))).isoformat()
            amt = rcents(rng.uniform(2000, 9000))
            w.writerow([ch, oid, dt, d(amt)])


def _summary(orders: list[Order]) -> None:
    from collections import Counter
    c = Counter(o.gt_type for o in orders)
    gmv = sum(o._item_total + o.ship_cents for o in orders)   # type: ignore[attr-defined]
    found = sum(o.loss for o in orders if o.gt_type in
                ("fee_overcharge", "missing_payout", "chargeback", "refund", "duplicate"))
    n_lines = sum(len(o.lines) for o in orders)
    print("[OK] generated", len(orders), "orders,", n_lines, "settlement lines")
    print("[OK] GMV $", d(gmv))
    print("[OK] ground-truth breakdown:", dict(c))
    print("[OK] true-loss headline (pre-engine est) $", d(found))


if __name__ == "__main__":
    main()
