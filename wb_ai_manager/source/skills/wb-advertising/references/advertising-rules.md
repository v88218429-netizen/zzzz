# Advertising decision rules v0.3

These rules are a starting policy corpus. They are not universal Wildberries benchmarks. Client baselines and explicit business goals override heuristic thresholds where safe.

## Data sufficiency

AD-DATA-001 — Do not make a bid decision without a current campaign record and a measured performance window.
AD-DATA-002 — If clicks are below the configured minimum, label the sample thin and avoid spend-changing conclusions except emergency hard-loss guards.
AD-DATA-003 — Scaling decisions require the configured minimum completed orders or a client-specific alternative.
AD-DATA-004 — Compare equivalent weekday/season windows when seasonality is material.
AD-DATA-005 — Separate attributed advertising revenue from total product revenue.
AD-DATA-006 — If tracking/data freshness is degraded, block write proposals.
AD-DATA-007 — A campaign ID used for a change must have been returned by the live campaign tools for this tenant.
AD-DATA-008 — A product/nmID used in a change must exist in the tenant's current catalog snapshot.

## Diagnosis before bid change

AD-DIAG-001 — DRR increase alone is not sufficient to lower a bid.
AD-DIAG-002 — Before lowering a bid, inspect price/promo changes in the same window.
AD-DIAG-003 — Before lowering a bid, inspect product conversion/funnel change.
AD-DIAG-004 — Before lowering a bid, inspect stock cover and geographic availability.
AD-DIAG-005 — Before lowering a bid, inspect search-position movement and campaign placement.
AD-DIAG-006 — Before raising a bid because position fell, verify that stock is not critically low.
AD-DIAG-007 — Before raising a bid because position fell, verify that conversion did not materially deteriorate.
AD-DIAG-008 — CTR deterioration with stable bid should trigger card/price/competitive diagnosis before aggressive bid changes.
AD-DIAG-009 — CPC increase with stable conversion may justify a bid reduction only if contribution economics are deteriorating.
AD-DIAG-010 — Conversion deterioration after a price increase should be treated first as a pricing/funnel incident, not automatically an ad incident.
AD-DIAG-011 — A sudden visibility drop with healthy ad metrics may indicate inventory geography, indexing or card issues.
AD-DIAG-012 — If two campaigns compete for the same SKU/query intent, inspect cannibalization before scaling either.

## Profit guard

AD-PROFIT-001 — Never scale a campaign when marginal order economics are below the configured contribution floor.
AD-PROFIT-002 — Break-even CPO is derived from tenant unit economics, not a generic percentage.
AD-PROFIT-003 — Target DRR must be tied to the product's actual margin and business objective.
AD-PROFIT-004 — A launch may intentionally exceed normal DRR only under an explicit launch policy and budget cap.
AD-PROFIT-005 — A clearance campaign may use different constraints only under an explicit clearance policy.
AD-PROFIT-006 — Include expected return/cancellation economics where they materially affect contribution margin.

## Inventory guard

AD-STOCK-001 — If days of cover are below the critical threshold, block bid increases by default.
AD-STOCK-002 — If stockout is imminent, prefer preserving organic history/availability over buying incremental traffic unless policy says otherwise.
AD-STOCK-003 — If a replenishment is confirmed and arrival is near, the system may stage a time-bounded plan but must not assume arrival until status is verified.
AD-STOCK-004 — Overstock alone is not sufficient to increase bids; conversion and contribution economics still apply.

## Bid movement

AD-BID-001 — Every bid change uses the live current bid as the base.
AD-BID-002 — Never change a bid by more than the configured max percentage in one step.
AD-BID-003 — Never exceed the configured absolute bid cap.
AD-BID-004 — Out-of-range model output is rejected, never silently clamped.
AD-BID-005 — Respect the cooldown after a bid change before another algorithmic change.
AD-BID-006 — Save the previous value for rollback.
AD-BID-007 — Read the bid back from WB after execution and compare requested vs actual.
AD-BID-008 — If read-after-write disagrees, create an incident and stop further writes for that campaign.
AD-BID-009 — Change one primary causal variable at a time during controlled optimization.
AD-BID-010 — A WB recommended bid is a contextual input, not an instruction.
AD-BID-011 — If WB's recommended bid lies outside tenant guardrails, tenant guardrails win.
AD-BID-012 — If the proposed bid is based on a query/cluster, use the cluster-specific unit path; do not reuse campaign-level unit assumptions.

## Pause / emergency

AD-PAUSE-001 — A campaign with material spend and zero orders may be staged for emergency pause when the configured threshold is crossed.
AD-PAUSE-002 — A high DRR alert may justify a pause proposal when sample size is sufficient and loss guard is crossed.
AD-PAUSE-003 — Before permanent stop/delete, require explicit human approval; prefer pause because it is reversible.
AD-PAUSE-004 — Auto-pause must be separately enabled per tenant and may not be inferred from generic policy.

## Search clusters / minus phrases

AD-QUERY-001 — Do not add a query to minus phrases from impressions alone.
AD-QUERY-002 — A minus-phrase proposal needs enough click/spend evidence and poor downstream conversion relative to context.
AD-QUERY-003 — Do not minus a strategically important query solely because short-window DRR is high.
AD-QUERY-004 — Preserve discovery queries during launch unless launch policy says otherwise.
AD-QUERY-005 — When query performance is ambiguous, stage an observation window instead of an irreversible traffic cut.

## Experiments

AD-EXP-001 — Every planned optimization should declare hypothesis, primary metric, guard metric, start time and evaluation time.
AD-EXP-002 — Do not declare a winner before minimum sample or time window is met.
AD-EXP-003 — If a guard metric crosses a hard limit, terminate the experiment early and roll back.
AD-EXP-004 — Record interventions so future comparisons exclude contaminated windows.
AD-EXP-005 — Prefer within-SKU historical baselines to generic marketplace norms once sufficient data exists.
