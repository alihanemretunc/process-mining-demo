"""Build a P2P event log from the Oracle-style extracts.

Case notion: one purchasing line = requisition line -> PO line -> receipts -> invoice lines -> payment.
Requisition lines that never became a PO line keep a REQ-based case ID (open PRs).
Header-level events (requisition/PO approvals, VAT holds) are copied to every line ("fan-out").
Non-PO invoices cannot be placed on a PO line, so they are excluded and counted.
"""
from pathlib import Path

import pandas as pd

SRC = Path("oracle_extracts")
OUT = Path("p2p_event_log.csv")
EXTRACT_DATE = pd.Timestamp("2026-09-30 23:59")


def load(name):
    return pd.read_csv(SRC / f"{name}.csv")


req_h, req_l = load("POR_REQUISITION_HEADERS_ALL"), load("POR_REQUISITION_LINES_ALL")
po_h, po_l = load("PO_HEADERS_ALL"), load("PO_LINES_ALL")
actions, rcv = load("PO_ACTION_HISTORY"), load("RCV_TRANSACTIONS")
inv, inv_l = load("AP_INVOICES_ALL"), load("AP_INVOICE_LINES_ALL")
holds, pays, sup = load("AP_HOLDS_ALL"), load("AP_INVOICE_PAYMENTS_ALL"), load("POZ_SUPPLIERS")

# --- Case IDs: one per PO line, plus requisition lines with no PO line ---
po_lines = po_l.merge(po_h, on="PO_HEADER_ID", suffixes=("", "_HDR"))
po_lines["case_id"] = po_lines["SEGMENT1"] + "-L" + po_lines["LINE_NUM"].astype(str)
case_of_po_line = po_lines.set_index("PO_LINE_ID")["case_id"]

req = req_l.merge(req_h, on="REQUISITION_HEADER_ID", suffixes=("", "_HDR"))
req["case_id"] = req["PO_LINE_ID"].map(case_of_po_line)
open_pr = req["case_id"].isna()
req.loc[open_pr, "case_id"] = req["REQUISITION_NUMBER"] + "-L" + req["LINE_NUMBER"].astype(str)

events = []


def add(df, activity, ts_col, user_col, source):
    e = pd.DataFrame({"case_id": df["case_id"], "activity": activity,
                      "timestamp": df[ts_col], "resource": df[user_col], "source_table": source,
                      "invoice_num": df["INVOICE_NUM"] if "INVOICE_NUM" in df else None})
    events.append(e.dropna(subset=["timestamp"]))


# Requisition events
add(req, "Create Requisition", "CREATION_DATE", "PREPARER", "POR_REQUISITION_HEADERS_ALL")
req_act = actions[actions["OBJECT_TYPE_CODE"] == "REQ"].merge(
    req[["REQUISITION_HEADER_ID", "case_id"]], left_on="OBJECT_ID", right_on="REQUISITION_HEADER_ID")
for code, name in {"SUBMIT": "Submit Requisition", "REJECT": "Reject Requisition",
                   "APPROVE": "Approve Requisition"}.items():
    add(req_act[req_act["ACTION_CODE"] == code], name, "ACTION_DATE", "PERFORMED_BY", "PO_ACTION_HISTORY")

# Purchase order events
add(po_lines, "Create PO", "CREATION_DATE_HDR", "CREATED_BY", "PO_HEADERS_ALL")
po_act = actions[actions["OBJECT_TYPE_CODE"] == "PO"].merge(
    po_lines[["PO_HEADER_ID", "case_id"]], left_on="OBJECT_ID", right_on="PO_HEADER_ID")
for code, name in {"SUBMIT": "Submit PO", "APPROVE": "Approve PO", "CHANGE": "Change PO"}.items():
    add(po_act[po_act["ACTION_CODE"] == code], name, "ACTION_DATE", "PERFORMED_BY", "PO_ACTION_HISTORY")

# Receipts
rcv_c = rcv.assign(case_id=rcv["PO_LINE_ID"].map(case_of_po_line))
add(rcv_c, "Record Goods Receipt", "TRANSACTION_DATE", "CREATED_BY", "RCV_TRANSACTIONS")

# Invoices (only ITEM lines matched to a PO line)
item = inv_l[(inv_l["LINE_TYPE_LOOKUP_CODE"] == "ITEM") & inv_l["PO_LINE_ID"].notna()]
inv_po = item[["INVOICE_ID", "PO_LINE_ID"]].drop_duplicates().merge(inv, on="INVOICE_ID")
inv_po["case_id"] = inv_po["PO_LINE_ID"].map(case_of_po_line)
add(inv_po, "Receive Invoice", "CREATION_DATE", "CREATED_BY", "AP_INVOICES_ALL")
add(inv_po, "Cancel Invoice", "CANCELLED_DATE", "CANCELLED_BY", "AP_INVOICES_ALL")

# Holds: line-level holds map directly; invoice-level holds (no PO line) fan out to the invoice's lines
line_h = holds[holds["PO_LINE_ID"].notna()].merge(inv[["INVOICE_ID", "INVOICE_NUM"]], on="INVOICE_ID")
line_h["case_id"] = line_h["PO_LINE_ID"].map(case_of_po_line)
hdr_h = holds[holds["PO_LINE_ID"].isna()].drop(columns="PO_LINE_ID").merge(
    inv_po[["INVOICE_ID", "INVOICE_NUM", "case_id"]], on="INVOICE_ID")
all_h = pd.concat([line_h, hdr_h], ignore_index=True)
HOLD_NAMES = {"PRICE": "Invoice Hold: Price Variance", "QTY REC": "Invoice Hold: Not Yet Received",
              "VAT MISMATCH": "Invoice Hold: VAT Mismatch"}
for code, name in HOLD_NAMES.items():
    add(all_h[all_h["HOLD_LOOKUP_CODE"] == code], name, "HOLD_DATE", "HELD_BY", "AP_HOLDS_ALL")
add(all_h, "Release Invoice Hold", "RELEASE_DATE", "RELEASED_BY", "AP_HOLDS_ALL")

# Payments
pay_c = pays.merge(inv_po[["INVOICE_ID", "INVOICE_NUM", "case_id"]], on="INVOICE_ID")
add(pay_c, "Pay Invoice", "PAYMENT_DATE", "CREATED_BY", "AP_INVOICE_PAYMENTS_ALL")

log = pd.concat(events, ignore_index=True)
log["timestamp"] = pd.to_datetime(log["timestamp"])
log["execution_type"] = log["resource"].str.startswith("SYS_").map({True: "Automated", False: "Manual"})

# --- Case attributes ---
po_attr = po_lines.merge(sup, on="VENDOR_ID")
po_attr = pd.DataFrame({
    "case_id": po_attr["case_id"], "po_number": po_attr["SEGMENT1"],
    "supplier": po_attr["VENDOR_NAME"], "business_unit": po_attr["PRC_BU"],
    "category": po_attr["CATEGORY_NAME"], "line_amount_aed": (po_attr["QUANTITY"] * po_attr["UNIT_PRICE"]).round(2),
    "payment_terms": po_attr["PAYMENT_TERMS"],
    "has_requisition": po_attr["PO_LINE_ID"].isin(req_l["PO_LINE_ID"]).map({True: "Yes", False: "No"}),
})
pr_attr = req[open_pr].merge(sup, left_on="SUGGESTED_VENDOR_ID", right_on="VENDOR_ID")
pr_attr = pd.DataFrame({
    "case_id": pr_attr["case_id"], "po_number": "No PO", "supplier": pr_attr["VENDOR_NAME"],
    "business_unit": pr_attr["REQ_BU"], "category": pr_attr["CATEGORY_NAME"],
    "line_amount_aed": (pr_attr["QUANTITY"] * pr_attr["UNIT_PRICE"]).round(2),
    "payment_terms": "n/a", "has_requisition": "Yes",
})
attrs = pd.concat([po_attr, pr_attr], ignore_index=True)

# Paid on time: first payment of a non-cancelled invoice vs invoice date + terms
live = inv_po[inv_po["CANCELLED_DATE"].isna()].merge(pay_c[["INVOICE_ID", "PAYMENT_DATE"]].drop_duplicates(),
                                                     on="INVOICE_ID", how="left")
live = live.merge(attrs[["case_id", "payment_terms"]], on="case_id")
live["due"] = pd.to_datetime(live["INVOICE_DATE"]) + pd.to_timedelta(
    live["payment_terms"].str.replace("NET ", "").astype(int), unit="D")
live["paid"] = pd.to_datetime(live["PAYMENT_DATE"])
live["paid_on_time"] = "Not paid yet"
live.loc[live["paid"].notna(), "paid_on_time"] = (live["paid"] <= live["due"] + pd.Timedelta(days=1)).map(
    {True: "Yes", False: "No"})
attrs = attrs.merge(live.groupby("case_id")["paid_on_time"].first(), on="case_id", how="left")
attrs["paid_on_time"] = attrs["paid_on_time"].fillna("No invoice")

log = log.merge(attrs, on="case_id", how="left")
log = log.sort_values(["case_id", "timestamp"]).reset_index(drop=True)
log["timestamp"] = log["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
log.to_csv(OUT, index=False)

# --- Validation: fail loudly if the log breaks basic event-log rules ---
assert log[["case_id", "activity", "timestamp"]].notna().all().all(), "null in required column"
assert (pd.to_datetime(log["timestamp"]) <= EXTRACT_DATE).all(), "event after extract date"
assert log["supplier"].notna().all(), "case without attributes"
assert not log.duplicated().any(), "duplicate events"

# --- Answer key: numbers the tool should reproduce ---
ts = pd.to_datetime(log["timestamp"])
first_ts = log.assign(ts=ts).pivot_table(index="case_id", columns="activity", values="ts", aggfunc="min")
variants = log.groupby("case_id")["activity"].agg(" > ".join).value_counts()
n_cases = log["case_id"].nunique()
print(f"Written {OUT}: {len(log):,} events, {n_cases:,} cases, {len(variants)} variants, "
      f"{OUT.stat().st_size / 1e6:.1f} MB")
print(f"Non-PO invoices excluded: {inv['INVOICE_ID'].nunique() - inv_po['INVOICE_ID'].nunique()}")
print(f"Open PR lines (no PO): {open_pr.sum()}")
print(f"After-the-fact POs (invoice before PO): {(first_ts['Receive Invoice'] < first_ts['Create PO']).sum()} cases")
print(f"Manual share of events: {(log['execution_type'] == 'Manual').mean():.0%}")
print("\nCases per hold type:")
print(log[log["activity"].str.startswith("Invoice Hold")].groupby("activity")["case_id"].nunique().to_string())
print("\nPaid on time (cases):")
print(attrs["paid_on_time"].value_counts().to_string())
appr = log[log["activity"] == "Approve Requisition"].merge(
    log[log["activity"] == "Submit Requisition"].groupby("case_id")["timestamp"].min().rename("sub"), on="case_id")
appr["days"] = (pd.to_datetime(appr["timestamp"]) - pd.to_datetime(appr["sub"])).dt.total_seconds() / 86400
print("\nMean requisition approval time (days) by approver:")
print(appr.groupby("resource")["days"].mean().round(1).sort_values(ascending=False).to_string())
print("\nTop 5 variants (share of cases):")
for v, n in variants.head(5).items():
    print(f"  {n / n_cases:5.1%}  {v}")
