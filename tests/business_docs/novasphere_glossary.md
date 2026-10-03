Novasphere Sales & Customer Success Glossary

This document defines internal terminology used across Novasphere's sales, customer success,
and finance teams. None of these definitions are recoverable from the raw CSV column names
alone — they reflect internal conventions, not something a data analyst could guess correctly
without this document.

A "whale" deal is any single deal with a total contract value (tcv_usd) greater than $100,000.
Whale deals are tracked separately in the weekly executive review, regardless of which industry
or region they come from.

A "land-and-expand" account is any customer_name that appears more than once in the dataset with
at least one deal_type of "New Business" followed chronologically by a later deal_type of
"Expansion" or "Renewal" for the same customer. This pattern is the core of Novasphere's growth
strategy and is reported on separately from raw new-logo counts.

The "fiscal year" at Novasphere runs from April 1st to March 31st, not the calendar year. Fiscal
Q1 is April-June, Q2 is July-September, Q3 is October-December, and Q4 is January-March. Any
question about "this fiscal year" or "last fiscal quarter" must be interpreted using this
calendar, not the standard January-December calendar.

"Support tier" values map to specific response-time commitments that are not stored in this
dataset: Standard support commits to a 48-hour first response, Premium commits to 8 hours, and
Enterprise SLA commits to 1 hour with a named technical account manager.

A deal is considered "at-risk" if churn_risk is "High" and nps_score is below 20. Deals meeting
only one of these two conditions are not considered at-risk for reporting purposes, even though
either condition alone might sound concerning.

The term "logo" in any sales context refers to a unique customer_name, not a deal_id. "New
logos this quarter" means distinct new customers acquired, not the count of new deals — a single
new customer signing three separate deals in one quarter counts as one new logo, not three.

"NRR" (Net Revenue Retention) at Novasphere is calculated only from deal_type values of
"Renewal", "Expansion", and "Contraction" — New Business and Churn deals are explicitly excluded
from NRR calculations, since NRR is meant to measure existing-customer revenue movement only.

A "champion" is the primary_contact on any deal where customer_size is "Enterprise" and
onboarding_status is "Completed". Champions are the internal term for enterprise contacts who
have successfully gone live and are considered reference-able for case studies.

The "acquisition_channel" value "Product-Led Growth" is internally abbreviated "PLG" in all
executive reporting. When a question refers to "PLG deals" or "PLG customers," it means deals
where acquisition_channel equals "Product-Led Growth".

A "contraction event" is any deal_type of "Contraction" where the seats value decreased compared
to that same customer's most recent prior deal. Not every Contraction-type deal represents a
seat reduction — some are price renegotiations with no seat change, and those are not counted as
contraction events in the strict sense.

The sales_rep field lists the closing rep, not necessarily the rep who sourced the deal.
Novasphere does not track source reps separately in this system, so any question about "who
sourced" a deal cannot be answered from this data alone — only who closed it.

"Sticky" customers are those with contract_type "Multi-Year" and support_tier "Enterprise SLA".
This combination is used internally as a proxy for low churn risk regardless of what the
churn_risk field itself says, since multi-year enterprise contracts rarely churn even when
flagged as at-risk by the automated churn model.

The term "ARR bridge" refers to the walk from beginning-of-period ARR to end-of-period ARR
through new business, expansion, contraction, and churn. It is a finance reporting concept and
cannot be computed from a single snapshot of this dataset without a time-series comparison.

A "starter-to-growth" upgrade is when a customer's plan value changes from "Starter" to "Growth"
between two deals. This is tracked as a leading indicator of product-market fit within the SMB
segment specifically, and is not meaningful for Enterprise or Mid-Market customers who typically
start on higher-tier plans.

"Onboarding_status" of "In Progress" for longer than 90 days from close_date is internally
flagged as a "stalled onboarding" and triggers an internal customer success escalation. This
90-day threshold is not stored anywhere in the dataset itself.

The word "won" in any Novasphere context means deal_stage equals "Closed Won" specifically — it
never refers to deal_type, plan, or any other field. A deal with deal_type "New Business" that
has not yet reached deal_stage "Closed Won" is not "won," it is still open or in another stage.

A "downsell" is different from a "contraction event" above — downsell specifically refers to a
plan downgrade (e.g. Growth to Starter), while contraction events are about seat count. A single
deal can be both, neither, or just one of the two.

Regional leadership reviews group "APAC" and "LATAM" together under the label "Emerging Regions"
in board-level reporting, even though they remain separate values in the region column itself.
"North America" and "EMEA" are referred to as "Core Regions" in the same reporting context.
