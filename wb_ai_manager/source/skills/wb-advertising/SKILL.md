---
name: wb-advertising
description: Diagnose and optimize Wildberries advertising safely. Use for DRR, CTR, CPC/CPM, bids, search clusters, minus phrases, budgets, campaign pauses, scaling and ad-related search-position questions.
---

# Wildberries advertising

The objective is contribution profit and durable visibility, not a single ad metric.

## Required workflow

1. Fetch the campaign and a recent performance window.
2. Fetch enough context to distinguish an ad problem from a product problem: stock cover, price/promo change, funnel conversion, search position and recent interventions.
3. Read client unit economics and target constraints.
4. Apply the decision rules in `references/advertising-rules.md`.
5. If proposing a bid/budget change, call only a staging tool. Never call a raw WB bid endpoint.
6. Treat the staged preview as a proposal, not an applied change.
7. After any approved change, require a cooldown and a read-after-write verification before another adjustment.

## Never do this

- Never choose a bid only because WB recommends it.
- Never optimize solely on DRR.
- Never increase a bid when stock cover is critically low unless the operator explicitly chooses sell-through/launch behavior and the policy permits it.
- Never change multiple causal variables at once during an experiment.
- Never use a fixed benchmark as proof that a campaign is bad.
- Never convert RUB to kopecks yourself in free-form reasoning. The backend adapter owns units.

Read `references/advertising-rules.md` and `references/bid-safety.md` for every bid/budget decision.
