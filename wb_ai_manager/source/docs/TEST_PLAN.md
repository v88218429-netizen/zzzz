# Test plan before real writes

## Gate 0 — static safety tests

Required on every build:
- mutating endpoint cannot be exposed as read;
- raw WB write tools are not exposed to the ChatGPT plugin;
- RUB/kopeck conversion tests;
- absolute bid cap tests;
- percentage step cap tests;
- tenant isolation tests;
- duplicate apply/idempotency tests;
- irreversible tools always need human approval;
- shadow mode can never write.

A specific regression test is included for a **200,000 RUB bid** and must remain blocked.

## Gate 1 — simulation corpus

Create at least 150 deterministic scenarios covering:
- high DRR caused by CPC growth;
- high DRR caused by conversion drop after price change;
- position loss with low stock;
- position loss with healthy stock and strong conversion;
- campaign spend with zero orders;
- too little data;
- new product launch;
- clearance;
- seasonal change;
- duplicate/cannibalizing campaigns;
- bad API freshness;
- wrong/ambiguous units;
- competitor price shock;
- card block;
- stock geography problem.

Every scenario has an expected action class: observe / investigate / stage pause / stage bid change / block / escalate.

## Gate 2 — historical backtest

Replay 8–12 weeks of one real seller's snapshots.

Score:
- false positive rate;
- unsafe recommendation count;
- missed material incidents;
- estimated contribution impact;
- number of unnecessary changes;
- oscillation rate;
- agreement/disagreement with human decisions.

Do not pretend a counterfactual profit result is certain. Label it estimated unless the historical experiment provides a clean comparison.

## Gate 3 — live shadow mode

Duration: at least 7–14 days per first pilot tenant.

The system:
- reads live data;
- creates alerts and staged previews;
- executes nothing;
- logs what it would have done.

Human records: accept / reject / modify + reason.

## Gate 4 — approval mode

Human approval required for every write. Start with reversible operations only.

After every apply:
- read-after-write verification;
- cooldown;
- before/after metric observation;
- rollback if defined and a guard metric is breached.

## Gate 5 — guarded auto

Enable per rule, not globally.

Example eligibility:
- rule has strong backtest and shadow evidence;
- action is reversible;
- money exposure is capped;
- current data is fresh;
- tenant explicitly opted in;
- rollback is available;
- monitoring is healthy.

Permanent stop/delete, destructive stock changes and similarly high-impact actions remain human-approved.
