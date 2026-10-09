# P2P Process Mining Demo (synthetic data)

All data is fictitious. Table/column names are modelled on Oracle EBS/Fusion naming for
illustration only; real Fusion extracts (BICC / OTBI / BI Publisher) will differ.

## Files
- `oracle_extracts/*.csv`: 11 simulated source tables (requisitions, POs, approval history,
  receipts, invoices, holds, payments, suppliers). 2 years: Oct 2024 to Sep 2026.
- `p2p_event_log.csv`: the event log built from them (case = PO line). Upload this one.
- `generate_oracle_extracts.py`: regenerates the source tables (seeded, same output every run).
- `build_event_log.py`: turns the source tables into the event log, validates it, prints the answer key.

Regenerate: `python generate_oracle_extracts.py && python build_event_log.py`

## Mapping in Power Automate Process Mining
| Column | Attribute type |
|---|---|
| case_id | Case ID |
| activity | Activity name |
| timestamp | Event start |
| resource | Resource |
| execution_type, source_table, invoice_num | Event level attribute |
| po_number, supplier, business_unit, category, payment_terms, has_requisition, paid_on_time | Case level attribute (first event) |
| line_amount_aed | Financial per case (first event) |

No end timestamp: each row is a single event.

## Design choices
- Case = purchasing line (requisition line -> PO line -> receipt -> invoice -> payment).
- Header-level events (approvals, VAT holds) are copied to every line of that document.
- Requisition lines that never became a PO keep a REQ-based case ID (open PRs).
- Non-PO invoices (120) cannot sit on a PO line and are excluded.
- Users starting with `SYS_` are treated as system accounts (Automated); everyone else is Manual.

## Answer key (what the tool should show)
- ~17,350 events, 1,851 cases, ~160 variants.
- Happy path ~31% of cases.
- APPR.OPS_DIRECTOR approves requisitions in ~8 days vs ~1.8 for other approvers.
- ~125 after-the-fact cases (invoice received before the PO was created).
- Holds by case: Not Yet Received ~390, Price Variance ~120, VAT Mismatch ~100.
- ~73% of events are manual. Paid on time: ~1,220 yes, ~400 late.
