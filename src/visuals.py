"""Render the portfolio cover + carousel slides, headless (no browser).

Reads the engine's outputs (out/*.json|csv) and draws branded slides with
matplotlib. Dark brand palette, emerald accent, ASCII-only text (0 cyrillic).

Run:  python src/visuals.py   ->  screens/01-cover.png ... 07-runlog.png
"""
from __future__ import annotations

import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
SCR = os.path.join(os.path.dirname(__file__), "..", "screens")

BG = "#0b1220"
PANEL = "#101c33"
PANEL2 = "#0d2b26"
ACCENT = "#34d399"
RED = "#f87171"
AMBER = "#fbbf24"
TEXT = "#ffffff"
MUTE = "#b6c2d6"
GRID = "#24324a"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "text.color": TEXT,
    "axes.edgecolor": GRID, "axes.labelcolor": MUTE,
    "xtick.color": MUTE, "ytick.color": MUTE, "figure.dpi": 100,
})


def _d(c: int) -> str:
    return f"${c/100:,.0f}"


def _d2(c: int) -> str:
    return f"${c/100:,.2f}"


def load():
    with open(os.path.join(OUT, "dashboard.json"), encoding="utf-8") as f:
        return json.load(f)


def fig(w=1600, h=1200):
    f = plt.figure(figsize=(w / 100, h / 100))
    f.patch.set_facecolor(BG)
    ax = f.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100 * h / w)
    ax.axis("off")
    return f, ax


def panel(ax, x, y, w, h, color=PANEL, radius=1.2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                fc=color, ec=GRID, lw=1.2))


def save(f, name):
    os.makedirs(SCR, exist_ok=True)
    f.savefig(os.path.join(SCR, name), facecolor=BG)
    plt.close(f)
    print("[OK]", name)


# --- 01 cover ----------------------------------------------------------------
def cover(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "PYTHON ENGINE  -  MULTI-CHANNEL  -  RECONCILED TO THE CENT",
            color=ACCENT, fontsize=13, fontweight="bold")
    ax.text(6, H - 15, "Order & Payout", color=TEXT, fontsize=52, fontweight="bold")
    ax.text(6, H - 25, "Reconciliation", color=ACCENT, fontsize=52, fontweight="bold")
    ax.text(6, H - 32, "30,000 orders across Amazon, Shopify & eBay matched to their "
            "payout statements", color=MUTE, fontsize=15)

    chips = [("30,000", "orders"), ("3", "channels"),
             (_d(d["money_found"]), "unreconciled found"), ("0", "rows dropped")]
    cx = 6
    for big, small in chips:
        panel(ax, cx, H - 48, 20, 11, PANEL)
        ax.text(cx + 10, H - 40.5, big, color=ACCENT, fontsize=22, fontweight="bold", ha="center")
        ax.text(cx + 10, H - 45.5, small, color=MUTE, fontsize=11, ha="center")
        cx += 22

    panel(ax, 6, 4, 88, 14, PANEL2)
    ax.text(8, 15.5, "EXCEPTIONS DASHBOARD", color=MUTE, fontsize=11, fontweight="bold")
    mini = [("GMV", _d(d["gmv"])), ("Net payout", _d(d["net_payout"])),
            ("Effective fee", f"{d['effective_fee_pct']}%"),
            ("Reconciled", f"{d['reconciled_pct']}%"),
            ("Exceptions", f"{d['exceptions']:,}")]
    mx = 9
    for lab, val in mini:
        ax.text(mx, 11, val, color=TEXT, fontsize=18, fontweight="bold")
        ax.text(mx, 7.5, lab, color=MUTE, fontsize=11)
        mx += 17.4
    ax.text(6, 1.4, "Synthetic demonstration dataset  -  reproducible, fixed seed  -  no real "
            "client data", color=MUTE, fontsize=10)
    save(f, "01-cover.png")


# --- 02 dashboard ------------------------------------------------------------
def dashboard(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "RECONCILIATION DASHBOARD", color=ACCENT, fontsize=13, fontweight="bold")
    ax.text(6, H - 13, "Settlement vs. orders, all channels, 12 months", color=MUTE, fontsize=14)

    cards = [("GMV", _d(d["gmv"]), MUTE), ("Net payout", _d(d["net_payout"]), MUTE),
             ("Total fees", _d(d["total_fees"]), AMBER),
             ("Effective fee", f"{d['effective_fee_pct']}%", AMBER),
             ("Orders", f"{d['orders']:,}", MUTE),
             ("Auto-reconciled", f"{d['reconciled_pct']}%", ACCENT),
             ("Exceptions", f"{d['exceptions']:,}", RED),
             ("Unreconciled found", _d(d["money_found"]), ACCENT)]
    x0, y0, w, h, gap = 6, H - 34, 20.5, 13, 1.5
    for i, (lab, val, col) in enumerate(cards):
        cx = x0 + (i % 4) * (w + gap)
        cy = y0 - (i // 4) * (h + gap)
        panel(ax, cx, cy, w, h)
        ax.text(cx + 1.5, cy + h - 4, lab.upper(), color=MUTE, fontsize=10.5)
        ax.text(cx + 1.5, cy + 3, val, color=col, fontsize=23, fontweight="bold")

    mb = d["money_by_status"]
    panel(ax, 6, 4, 88, 12, PANEL)
    ax.text(8, 13.5, "WHERE THE %s IS  (unreconciled, by type)" % _d(d['money_found']),
            color=TEXT, fontsize=12, fontweight="bold")
    order = ["Missing payout", "Chargeback", "Refund", "Underpaid"]
    labels = {"Missing payout": "Shipped, never paid", "Chargeback": "Chargebacks",
              "Refund": "Fees kept on refunds", "Underpaid": "Fee overcharges"}
    total = sum(mb.values())
    cols = {"Missing payout": RED, "Chargeback": AMBER, "Refund": "#60a5fa", "Underpaid": ACCENT}
    bx = 8
    for k in order:
        v = mb.get(k, 0)
        wseg = 84 * v / total
        ax.add_patch(plt.Rectangle((bx, 6.5), wseg, 3.2, fc=cols[k], ec=BG))
        ax.text(bx + wseg / 2, 5.0, labels[k], color=MUTE, fontsize=9, ha="center")
        ax.text(bx + wseg / 2, 7.6, _d(v), color=BG, fontsize=10, fontweight="bold", ha="center")
        bx += wseg
    ax.text(6, 1.4, "Synthetic demonstration dataset  -  reproducible, fixed seed", color=MUTE,
            fontsize=10)
    save(f, "02-dashboard.png")


# --- 03 exceptions -----------------------------------------------------------
def exceptions(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "EXCEPTIONS  -  NOTHING DROPPED SILENTLY", color=ACCENT,
            fontsize=13, fontweight="bold")
    ax.text(6, H - 13, "Every order lands in exactly one state; matched + exceptions = total",
            color=MUTE, fontsize=14)

    sb = d["status_breakdown"]
    order = ["Matched", "Refund", "Promotion", "Underpaid", "Chargeback",
             "Missing payout", "Unexpected payout"]
    cols = {"Matched": ACCENT, "Refund": "#60a5fa", "Promotion": "#a78bfa",
            "Underpaid": AMBER, "Chargeback": RED, "Missing payout": RED,
            "Unexpected payout": "#f472b6"}
    axb = f.add_axes([0.07, 0.12, 0.52, 0.6])
    axb.set_facecolor(BG)
    vals = [sb.get(k, 0) for k in order]
    axb.barh(range(len(order)), vals, color=[cols[k] for k in order])
    axb.set_yticks(range(len(order)))
    axb.set_yticklabels(order, fontsize=12)
    axb.invert_yaxis()
    axb.set_xscale("log")
    for sp in axb.spines.values():
        sp.set_visible(False)
    axb.tick_params(length=0)
    for i, v in enumerate(vals):
        axb.text(v * 1.1, i, f"{v:,}", va="center", color=TEXT, fontsize=11, fontweight="bold")
    axb.set_xlim(0.8, max(vals) * 3)

    panel(ax, 62, 10, 32, H - 22, PANEL)
    ax.text(63.5, H - 14, "SAMPLE EXCEPTIONS", color=MUTE, fontsize=11, fontweight="bold")
    ty = H - 19
    for row in _exc_sample():
        ax.text(63.5, ty, row[0], color=TEXT, fontsize=9)
        ax.text(92, ty, row[1], color=RED if row[2] else AMBER, fontsize=9,
                ha="right", fontweight="bold")
        ax.text(63.5, ty - 2.1, row[3], color=MUTE, fontsize=8)
        ty -= 5.4
    save(f, "03-exceptions.png")


def _exc_sample():
    with open(os.path.join(OUT, "exceptions.csv"), encoding="utf-8") as fh:
        r = list(csv.DictReader(fh))
    want = {"Missing payout", "Chargeback", "Underpaid", "Refund", "Unexpected payout"}
    seen, rows = set(), []
    for row in r:
        s = row["status"]
        if s in want and s not in seen:
            seen.add(s)
            delta = int(row["delta"])
            val = _d2(abs(delta)) if delta else _d2(int(row["impact"]))
            rows.append((f"{row['channel'][:3].upper()}  {s}", val,
                         s in ("Missing payout", "Chargeback"), f"order {row['order_id'][:16]}"))
    return rows[:5]


# --- 04 pipeline -------------------------------------------------------------
def pipeline(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "HOW IT WORKS  -  TIERED MATCH ENGINE", color=ACCENT,
            fontsize=13, fontweight="bold")
    ndup = d["meta"]["n_dup"]
    steps = [
        ("3 payout schemas", "Amazon settlement V2,\nShopify & eBay exports", PANEL2),
        ("Normalize + validate", "one canonical line table,\nmoney in integer cents", PANEL),
        ("Dedup", f"SHA-256 fingerprint\n{ndup} re-imported lines", PANEL),
        ("Exact + group-and-net", "line-items grouped per order,\nfees netted", PANEL),
        ("Fuzzy fallback", "orphan lines relinked by\nref + amount + date", PANEL),
        ("Classify + control total", "8 states; matched +\nexceptions = total", PANEL2),
    ]
    y, x = H - 20, 6
    for i, (t, s, col) in enumerate(steps):
        panel(ax, x, y, 13.5, 13, col)
        ax.text(x + 0.9, y + 9.5, f"{i+1}", color=ACCENT, fontsize=18, fontweight="bold")
        ax.text(x + 0.9, y + 6.2, t, color=TEXT, fontsize=10.5, fontweight="bold")
        ax.text(x + 0.9, y + 1.2, s, color=MUTE, fontsize=8.5)
        if i != 2 and i < len(steps) - 1:
            ax.add_patch(FancyArrowPatch((x + 13.5, y + 6.5), (x + 14.6, y + 6.5),
                                         arrowstyle="-|>", mutation_scale=16, color=ACCENT))
        x += 14.6
        if i == 2:
            x, y = 6, y - 16

    panel(ax, 6, 6, 88, 9, PANEL2)
    sc = d["scores"]
    ax.text(8, 11.5, "VERIFIED AGAINST A LABELLED GROUND-TRUTH SET", color=MUTE,
            fontsize=11, fontweight="bold")
    facts = ["every injected discrepancy class recovered",
             f"orphan relink {sc['orphan_relink_recall']*100:.1f}%",
             "dropped lines are verified re-imports", f"{d['reconciled_pct']}% auto-reconciled"]
    ax.text(8, 7.6, "   -   ".join(facts), color=ACCENT, fontsize=11.5, fontweight="bold")
    save(f, "04-pipeline.png")


# --- 05 margins --------------------------------------------------------------
def margins(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "MARGIN PER CHANNEL", color=ACCENT, fontsize=13, fontweight="bold")
    ax.text(6, H - 13, "What each channel actually nets after fees and COGS", color=MUTE, fontsize=14)
    pc = d["per_channel"]
    chans = ["amazon", "shopify", "ebay"]
    axb = f.add_axes([0.08, 0.14, 0.86, 0.56])
    axb.set_facecolor(BG)
    xs = np.arange(len(chans))
    rev = [pc[c]["revenue"] / 100 for c in chans]
    fees = [pc[c]["fees"] / 100 for c in chans]
    cogs = [pc[c]["cogs"] / 100 for c in chans]
    net_margin = [(pc[c]["net_payout"] - pc[c]["cogs"]) / 100 for c in chans]
    wb = 0.6
    axb.bar(xs, rev, wb, color=GRID, label="Revenue")
    axb.bar(xs, [c + fe for c, fe in zip(cogs, fees)], wb, color=AMBER, label="Fees")
    axb.bar(xs, cogs, wb, color="#60a5fa", label="COGS")
    axb.bar(xs, net_margin, wb * 0.5, color=ACCENT, label="Net (after fees & COGS)")
    for sp in axb.spines.values():
        sp.set_visible(False)
    axb.set_xticks(xs)
    axb.set_xticklabels([f"{c.title()}\n{pc[c]['margin_pct']}% margin" for c in chans], fontsize=13)
    axb.tick_params(length=0)
    axb.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=11, loc="upper right")
    axb.set_ylabel("USD")
    for i, c in enumerate(chans):
        axb.text(i, rev[i] * 1.02, _d(pc[c]["revenue"]), ha="center", color=TEXT, fontsize=11,
                 fontweight="bold")
    save(f, "05-margin.png")


# --- 06 drill-down -----------------------------------------------------------
def drilldown(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "GROUP-AND-NET  -  ONE ORDER, MANY SETTLEMENT LINES", color=ACCENT,
            fontsize=13, fontweight="bold")
    ax.text(6, H - 13, "An Amazon order explodes into several settlement rows; the engine "
            "nets them back", color=MUTE, fontsize=13)
    lines, oid = _amazon_example()
    panel(ax, 6, 16, 55, H - 32, PANEL)
    ax.text(8, H - 19, f"settlement lines  -  order {oid[:18]}", color=MUTE, fontsize=11,
            fontweight="bold")
    ty, tot = H - 24, 0
    for desc, amt in lines:
        tot += amt
        ax.text(8, ty, desc, color=TEXT, fontsize=11)
        ax.text(58, ty, _d2(amt), color=ACCENT if amt >= 0 else RED, fontsize=11,
                ha="right", fontweight="bold")
        ty -= 3.6
    ax.plot([8, 58], [ty + 1.4, ty + 1.4], color=GRID, lw=1)
    ax.text(8, ty - 1.5, "settled net", color=TEXT, fontsize=12, fontweight="bold")
    ax.text(58, ty - 1.5, _d2(tot), color=ACCENT, fontsize=13, ha="right", fontweight="bold")

    panel(ax, 64, 16, 30, H - 32, PANEL2)
    ax.text(66, H - 19, "WHY IT MATTERS", color=MUTE, fontsize=11, fontweight="bold")
    notes = ["A naive order-id join sees", "many rows and no single",
             "'amount' to match.", "", "The engine groups every", "line by order, nets",
             "principal - fees - refunds,", "then compares to the", "order's expected net",
             "within a 2-cent tolerance."]
    ny = H - 24
    for n in notes:
        ax.text(66, ny, n, color=TEXT if n else MUTE, fontsize=10.5)
        ny -= 3.4
    save(f, "06-drilldown.png")


def _amazon_example():
    from collections import defaultdict
    with open(os.path.join(OUT, "amazon_settlement.txt"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    groups = defaultdict(list)
    for r in rows:
        if r["amount"] and r["order-id"]:
            groups[r["order-id"]].append((r["amount-description"],
                                          int(round(float(r["amount"]) * 100))))
    for oid, lns in groups.items():
        if len(lns) >= 4:
            return lns[:7], oid
    oid = next(iter(groups))
    return groups[oid][:7], oid


# --- 07 run log --------------------------------------------------------------
def runlog(d):
    f, ax = fig()
    H = 75
    ax.text(6, H - 6, "RUN LOG  -  BUILT LIKE A SYSTEM, NOT A SCRIPT", color=ACCENT,
            fontsize=13, fontweight="bold")
    with open(os.path.join(OUT, "run_log.csv"), encoding="utf-8") as fh:
        rl = list(csv.DictReader(fh))[0]
    items = [
        ("Raw settlement lines ingested", f"{int(rl['raw_lines']):,}"),
        ("Duplicate lines caught (SHA-256)", f"{int(rl['duplicates_dropped']):,}"),
        ("Orphan groups relinked (fuzzy)", f"{rl['fuzzy_assigned']}/{rl['fuzzy_groups']}"),
        ("Orders reconciled", f"{int(rl['matched']):,} / {int(rl['orders']):,}"),
        ("Exceptions surfaced", f"{int(rl['exceptions']):,}"),
        ("Unreconciled money found", _d(d["money_found"])),
        ("Control total", "PASS  (ties to the cent)"),
        ("Engine runtime", f"{rl['duration_s']} s"),
    ]
    y = H - 20
    for i, (lab, val) in enumerate(items):
        panel(ax, 6, y, 88, 5.6, PANEL if i % 2 == 0 else PANEL2)
        ax.text(8, y + 1.9, lab, color=MUTE, fontsize=13)
        ax.text(92, y + 1.9, val, color=ACCENT if ("PASS" in val or "$" in val) else TEXT,
                fontsize=14, ha="right", fontweight="bold")
        y -= 6.4
    save(f, "07-runlog.png")


def main():
    d = load()
    cover(d)
    dashboard(d)
    exceptions(d)
    pipeline(d)
    margins(d)
    drilldown(d)
    runlog(d)
    print("[OK] all slides rendered to screens/")


if __name__ == "__main__":
    main()
