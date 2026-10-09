"""Generate synthetic, Oracle Fusion-style P2P extract tables (all data fictitious).

Table and column names are modelled on Oracle EBS/Fusion naming for illustration.
Real Fusion extracts (BICC / OTBI / BI Publisher) will differ and must be confirmed.

Planted patterns (so the demo has something to find):
  - a slow approver on large AD_OPERATIONS requisitions
  - POs created without a requisition, and after-the-fact POs (invoice before PO)
  - requisition rejections and PO change orders (rework loops)
  - partial and late receipts -> quantity holds
  - price-variance and VAT-mismatch invoice holds, duplicate invoices
  - manual vs automated steps (users prefixed SYS_ are system accounts)
  - open PRs, open POs, non-PO invoices
"""
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

SEED = 42
START = datetime(2024, 10, 1)
EXTRACT_DATE = datetime(2026, 9, 30, 23, 59)
N_PURCHASES = 1100
N_NON_PO_INVOICES = 120
OUT = Path("oracle_extracts")

rng = random.Random(SEED)

BUS = ["AD_HQ", "AD_OPERATIONS", "AD_FACILITIES"]
CATEGORIES = {  # name: (min unit price AED, max unit price AED, is_service)
    "IT Hardware": (800, 9000, False),
    "Software Licenses": (2000, 60000, True),
    "Facilities Maintenance": (1500, 40000, True),
    "Professional Services": (10000, 150000, True),
    "Office Supplies": (20, 400, False),
    "Marketing Materials": (500, 15000, False),
}
SUPPLIERS = [
    "Zenith Office Supplies", "Falconet IT Solutions", "Bluedune Facilities", "Corvane Consulting",
    "Sandline Print & Media", "Nexora Software", "Oasis Tech Distribution", "Meridian Advisory",
    "Pearlgate Maintenance", "Atlas Hardware Trading", "Crescent Marketing", "Vantage Licensing",
    "Harbor Cleaning Services", "Summit Professional Group", "Qamar Electronics", "Lumen Workspace",
    "Silvercoast Engineering", "Northwind Digital", "Palmyra Logistics", "Orchid Events",
]
SUPPLIER_TERMS = [rng.choice([30, 45, 60]) for _ in SUPPLIERS]
REQUESTERS = ["S.HASSAN", "M.RAHMAN", "L.NAIR", "J.THOMAS", "A.KHAN", "F.ALI", "N.SAEED", "R.MENON"]
BUYERS = ["BUYER.OMAR", "BUYER.PRIYA", "BUYER.JAMES", "BUYER.SARA"]
APPROVERS = {"AD_HQ": "APPR.HQ_MGR", "AD_OPERATIONS": "APPR.OPS_MGR", "AD_FACILITIES": "APPR.FM_MGR"}
SLOW_APPROVER = "APPR.OPS_DIRECTOR"
CFO = "APPR.CFO"
AP_CLERKS = ["AP.ANNA", "AP.RAVI", "AP.MONA"]
SYS_PRC, SYS_IMAGING, SYS_VALIDATION, SYS_PAY = (
    "SYS_PRC_AUTOCREATE", "SYS_AP_IMAGING", "SYS_AP_VALIDATION", "SYS_PAYMENT_RUN")

rows = {t: [] for t in [
    "POZ_SUPPLIERS", "POR_REQUISITION_HEADERS_ALL", "POR_REQUISITION_LINES_ALL",
    "PO_HEADERS_ALL", "PO_LINES_ALL", "PO_ACTION_HISTORY", "RCV_TRANSACTIONS",
    "AP_INVOICES_ALL", "AP_INVOICE_LINES_ALL", "AP_HOLDS_ALL", "AP_INVOICE_PAYMENTS_ALL",
]}
ids = {"action": 1, "rcv": 1, "inv": 700001, "hold": 1, "pay": 1}


def next_id(key):
    ids[key] += 1
    return ids[key] - 1


def bh(dt):
    """Move a timestamp into UAE business hours (Mon-Fri, 08:00-17:59)."""
    if dt.hour < 8:
        dt = dt.replace(hour=8 + rng.randint(0, 2), minute=rng.randint(0, 59))
    elif dt.hour >= 18:
        dt = (dt + timedelta(days=1)).replace(hour=8 + rng.randint(0, 2), minute=rng.randint(0, 59))
    while dt.weekday() >= 5:  # Sat/Sun
        dt += timedelta(days=1)
    return dt


def later(dt, mean_days):
    """A random business-hours timestamp roughly mean_days after dt."""
    return bh(dt + timedelta(hours=rng.gammavariate(2.0, mean_days * 24 / 2.0)))


def act(obj_type, obj_id, code, t, user):
    rows["PO_ACTION_HISTORY"].append(dict(
        ACTION_ID=next_id("action"), OBJECT_TYPE_CODE=obj_type, OBJECT_ID=obj_id,
        ACTION_CODE=code, ACTION_DATE=t, PERFORMED_BY=user))


def hold(inv_id, pol_id, code, t_hold, t_release, released_by):
    rows["AP_HOLDS_ALL"].append(dict(
        HOLD_ID=next_id("hold"), INVOICE_ID=inv_id, PO_LINE_ID=pol_id, HOLD_LOOKUP_CODE=code,
        HOLD_DATE=t_hold, HELD_BY=SYS_VALIDATION, RELEASE_DATE=t_release, RELEASED_BY=released_by))


def add_invoice(inv_num, vendor_id, t_create, creator, source, item_lines, tax, inv_date):
    inv_id = next_id("inv")
    items = sum(a for _, a, _ in item_lines)
    rows["AP_INVOICES_ALL"].append(dict(
        INVOICE_ID=inv_id, INVOICE_NUM=inv_num, VENDOR_ID=vendor_id, INVOICE_DATE=inv_date,
        CREATION_DATE=t_create, CREATED_BY=creator, SOURCE=source,
        INVOICE_AMOUNT=round(items + tax, 2), TOTAL_TAX_AMOUNT=tax,
        CANCELLED_DATE=None, CANCELLED_BY=None))
    for n, (pol_id, amount, qty) in enumerate(item_lines, start=1):
        rows["AP_INVOICE_LINES_ALL"].append(dict(
            INVOICE_ID=inv_id, LINE_NUMBER=n, LINE_TYPE_LOOKUP_CODE="ITEM", PO_LINE_ID=pol_id,
            QUANTITY_INVOICED=qty, AMOUNT=round(amount, 2), CREATION_DATE=t_create))
    rows["AP_INVOICE_LINES_ALL"].append(dict(
        INVOICE_ID=inv_id, LINE_NUMBER=len(item_lines) + 1, LINE_TYPE_LOOKUP_CODE="TAX",
        PO_LINE_ID=None, QUANTITY_INVOICED=None, AMOUNT=tax, CREATION_DATE=t_create))
    return inv_id


def pay(inv_id, amount, t_pay, user):
    rows["AP_INVOICE_PAYMENTS_ALL"].append(dict(
        INVOICE_PAYMENT_ID=next_id("pay"), INVOICE_ID=inv_id, AMOUNT=amount,
        PAYMENT_DATE=t_pay, CREATED_BY=user))


for i, name in enumerate(SUPPLIERS):
    rows["POZ_SUPPLIERS"].append(dict(VENDOR_ID=9001 + i, VENDOR_NAME=name))

for p in range(N_PURCHASES):
    bu = rng.choice(BUS)
    cat = rng.choice(list(CATEGORIES))
    lo, hi, is_service = CATEGORIES[cat]
    sup = rng.randrange(len(SUPPLIERS))
    vendor_id, terms = 9001 + sup, SUPPLIER_TERMS[sup]
    n_lines = rng.choices([1, 2, 3], weights=[50, 30, 20])[0]
    lines = [(ln, 1 if is_service else rng.randint(1, 40), round(rng.uniform(lo, hi), 2))
             for ln in range(1, n_lines + 1)]
    amount = sum(q * pr for _, q, pr in lines)
    po_id, req_id = 500001 + p, 300001 + p
    po_line_ids = [po_id * 10 + ln for ln, _, _ in lines]
    t = bh(START + timedelta(days=rng.uniform(0, 715)))

    after_the_fact = rng.random() < 0.06
    has_req = (not after_the_fact) and rng.random() < 0.90
    open_pr = has_req and rng.random() < 0.05
    requester = rng.choice(REQUESTERS)
    buyer = rng.choice(BUYERS)

    # --- Requisition ---
    if has_req:
        rows["POR_REQUISITION_HEADERS_ALL"].append(dict(
            REQUISITION_HEADER_ID=req_id, REQUISITION_NUMBER=f"REQ{200001 + p}",
            PREPARER=requester, REQ_BU=bu, CREATION_DATE=t))
        for (ln, q, pr), pol_id in zip(lines, po_line_ids):
            rows["POR_REQUISITION_LINES_ALL"].append(dict(
                REQUISITION_LINE_ID=req_id * 10 + ln, REQUISITION_HEADER_ID=req_id, LINE_NUMBER=ln,
                CATEGORY_NAME=cat, QUANTITY=q, UNIT_PRICE=pr, CURRENCY_CODE="AED",
                SUGGESTED_VENDOR_ID=vendor_id, PO_LINE_ID=None if open_pr else pol_id,
                CREATION_DATE=t))
        approver = SLOW_APPROVER if (bu == "AD_OPERATIONS" and amount > 50000) else APPROVERS[bu]
        mean = 6.0 if approver == SLOW_APPROVER else 1.0
        t_sub = later(t, 0.2)
        act("REQ", req_id, "SUBMIT", t_sub, requester)
        t_dec = later(t_sub, mean)
        if rng.random() < 0.06:
            act("REQ", req_id, "REJECT", t_dec, approver)
            t_sub = later(t_dec, 1.5)
            act("REQ", req_id, "SUBMIT", t_sub, requester)
            t_dec = later(t_sub, mean)
        act("REQ", req_id, "APPROVE", t_dec, approver)
        if open_pr:
            continue
        t = t_dec

    # --- Purchase order ---
    t_po = later(t, 8.0) if after_the_fact else later(t, 1.5)
    po_creator = SYS_PRC if (has_req and rng.random() < 0.45) else buyer
    rows["PO_HEADERS_ALL"].append(dict(
        PO_HEADER_ID=po_id, SEGMENT1=f"PO{100001 + p}", VENDOR_ID=vendor_id, PRC_BU=bu,
        BUYER=buyer, PAYMENT_TERMS=f"NET {terms}", CREATION_DATE=t_po, CREATED_BY=po_creator))
    for (ln, q, pr), pol_id in zip(lines, po_line_ids):
        rows["PO_LINES_ALL"].append(dict(
            PO_LINE_ID=pol_id, PO_HEADER_ID=po_id, LINE_NUM=ln, CATEGORY_NAME=cat,
            ITEM_DESCRIPTION=f"{cat} item {ln}", QUANTITY=q, UNIT_PRICE=pr, CREATION_DATE=t_po))
    t_sub = later(t_po, 0.2)
    act("PO", po_id, "SUBMIT", t_sub, po_creator)
    t_app = later(t_sub, 1.0)
    act("PO", po_id, "APPROVE", t_app, APPROVERS[bu])
    if amount > 250000:
        t_app = later(t_app, 3.0)
        act("PO", po_id, "APPROVE", t_app, CFO)
    if rng.random() < 0.10:
        t_chg = later(t_app, 5.0)
        act("PO", po_id, "CHANGE", t_chg, buyer)
        t_app = later(t_chg, 1.5)
        act("PO", po_id, "APPROVE", t_app, APPROVERS[bu])
    if not after_the_fact and rng.random() < 0.04:
        continue  # open PO: nothing received or invoiced yet

    # --- Receipts ---
    receipt_done = {}
    for (ln, q, pr), pol_id in zip(lines, po_line_ids):
        t_r = later(t_app, 1.0 if after_the_fact else (8.0 if is_service else 4.0))
        receiver = requester if has_req else buyer
        if (not is_service) and q > 1 and rng.random() < 0.15:  # partial delivery
            rows["RCV_TRANSACTIONS"].append(dict(
                TRANSACTION_ID=next_id("rcv"), TRANSACTION_TYPE="RECEIVE", TRANSACTION_DATE=t_r,
                PO_HEADER_ID=po_id, PO_LINE_ID=pol_id, QUANTITY=q // 2, CREATED_BY=receiver))
            t_r = later(t_r, 6.0)
            q = q - q // 2
        rows["RCV_TRANSACTIONS"].append(dict(
            TRANSACTION_ID=next_id("rcv"), TRANSACTION_TYPE="RECEIVE", TRANSACTION_DATE=t_r,
            PO_HEADER_ID=po_id, PO_LINE_ID=pol_id, QUANTITY=q, CREATED_BY=receiver))
        receipt_done[pol_id] = t_r

    # --- Invoice ---
    t_inv = t if after_the_fact else later(t_app, 16.0 if is_service else 12.0)
    manual = rng.random() < 0.45
    creator = rng.choice(AP_CLERKS) if manual else SYS_IMAGING
    source = "Manual Invoice Entry" if manual else "Imaging"
    inv_date = (t_inv - timedelta(days=rng.randint(1, 6))).replace(hour=0, minute=0, second=0, microsecond=0)
    price_var = rng.random() < 0.10
    vat_issue = rng.random() < 0.06
    item_lines = []
    for n, ((ln, q, pr), pol_id) in enumerate(zip(lines, po_line_ids)):
        uplift = 1 + rng.uniform(0.03, 0.12) if (price_var and n == 0) else 1
        item_lines.append((pol_id, q * pr * uplift, q))
    items = sum(a for _, a, _ in item_lines)
    tax = round(items * (rng.choice([0.0, 0.10]) if vat_issue else 0.05), 2)
    inv_num = f"INV-{vendor_id}-{rng.randint(10000, 99999)}"
    inv_id = add_invoice(inv_num, vendor_id, t_inv, creator, source, item_lines, tax, inv_date)

    t_val = later(max(t_inv, t_app), 0.3)
    ready = t_val
    if price_var:
        t_rel = later(t_val, 5.0)
        hold(inv_id, po_line_ids[0], "PRICE", t_val, t_rel, buyer)
        ready = max(ready, t_rel)
    for pol_id, t_r in receipt_done.items():
        if t_r > t_val:
            t_rel = later(t_r, 0.3)
            hold(inv_id, pol_id, "QTY REC", t_val, t_rel, SYS_VALIDATION)
            ready = max(ready, t_rel)
    if vat_issue:  # invoice-level hold: no PO line
        t_rel = later(t_val, 7.0)
        hold(inv_id, None, "VAT MISMATCH", t_val, t_rel, rng.choice(AP_CLERKS))
        ready = max(ready, t_rel)

    due = inv_date + timedelta(days=terms)
    if rng.random() < 0.08:
        t_pay, payer = later(ready, 2.0), rng.choice(AP_CLERKS)  # manual payment
    else:
        t_pay = max(ready, due - timedelta(days=5))
        t_pay += timedelta(days=(3 - t_pay.weekday()) % 7)  # weekly Thursday payment run
        t_pay = t_pay.replace(hour=10, minute=0)
        if rng.random() < 0.15:
            t_pay += timedelta(days=7)  # payment run skipped
        payer = SYS_PAY
    pay(inv_id, round(items + tax, 2), t_pay, payer)

    if rng.random() < 0.015:  # duplicate invoice entered, later cancelled
        t_dup = later(t_inv, 6.0)
        dup_id = add_invoice(inv_num.replace("-", ""), vendor_id, t_dup, rng.choice(AP_CLERKS),
                             "Manual Invoice Entry", item_lines, tax, inv_date)
        rows["AP_INVOICES_ALL"][-1].update(CANCELLED_DATE=later(t_dup, 3.0), CANCELLED_BY=rng.choice(AP_CLERKS))

# --- Non-PO invoices (no PO lines at all) ---
for _ in range(N_NON_PO_INVOICES):
    sup = rng.randrange(len(SUPPLIERS))
    t_inv = bh(START + timedelta(days=rng.uniform(0, 715)))
    amt = round(rng.uniform(500, 20000), 2)
    inv_id = add_invoice(f"INV-{9001 + sup}-{rng.randint(10000, 99999)}", 9001 + sup, t_inv,
                         rng.choice(AP_CLERKS), "Manual Invoice Entry", [(None, amt, None)],
                         round(amt * 0.05, 2), t_inv.replace(hour=0, minute=0))
    pay(inv_id, round(amt * 1.05, 2), later(t_inv, 20.0), SYS_PAY)

# --- Apply extract cut-off: drop events after the extract date ---
PRIMARY_DATE = {
    "POR_REQUISITION_HEADERS_ALL": "CREATION_DATE", "POR_REQUISITION_LINES_ALL": "CREATION_DATE",
    "PO_HEADERS_ALL": "CREATION_DATE", "PO_LINES_ALL": "CREATION_DATE",
    "PO_ACTION_HISTORY": "ACTION_DATE", "RCV_TRANSACTIONS": "TRANSACTION_DATE",
    "AP_INVOICES_ALL": "CREATION_DATE", "AP_INVOICE_LINES_ALL": "CREATION_DATE",
    "AP_HOLDS_ALL": "HOLD_DATE", "AP_INVOICE_PAYMENTS_ALL": "PAYMENT_DATE",
}
SECONDARY = {"AP_HOLDS_ALL": ["RELEASE_DATE", "RELEASED_BY"],
             "AP_INVOICES_ALL": ["CANCELLED_DATE", "CANCELLED_BY"]}

OUT.mkdir(exist_ok=True)
for table, data in rows.items():
    df = pd.DataFrame(data)
    if table in PRIMARY_DATE:
        df = df[df[PRIMARY_DATE[table]] <= EXTRACT_DATE].copy()
    if table in SECONDARY:
        date_col, user_col = SECONDARY[table]
        future = df[date_col].notna() & (df[date_col] > EXTRACT_DATE)
        df.loc[future, [date_col, user_col]] = None
    for col in df.columns:
        if col.endswith("_DATE") and col != "INVOICE_DATE":
            df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d %H:%M:%S")
    if "INVOICE_DATE" in df:
        df["INVOICE_DATE"] = pd.to_datetime(df["INVOICE_DATE"]).dt.strftime("%Y-%m-%d")
    for col in ["PO_LINE_ID", "QUANTITY_INVOICED"]:
        if col in df:
            df[col] = df[col].astype("Int64")
    df.to_csv(OUT / f"{table}.csv", index=False)
    print(f"{table:32s} {len(df):6d} rows")
