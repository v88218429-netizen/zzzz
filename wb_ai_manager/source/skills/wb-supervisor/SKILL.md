---
name: wb-supervisor
description: Coordinate a Wildberries seller account across advertising, search visibility, inventory, pricing, finance, cards, FBS, returns and reviews. Use for broad audits, "что происходит в кабинете", daily summaries and multi-domain incidents.
---

# WB Supervisor

Use this workflow for multi-domain Wildberries questions and executive summaries.

1. Start from live backend facts. Never infer live WB metrics from memory or generic benchmarks.
2. Read the current incidents and the latest relevant snapshots before making a recommendation.
3. If a problem crosses domains, route the diagnosis in this order: availability/stock → price/promo → card/blocks → funnel → ads/search → finance/returns.
4. Separate facts, hypotheses and actions. Label a hypothesis as such until supported by data.
5. Never call a raw marketplace write. Only stage a change through the WB AI Manager controlled tools.
6. Every recommendation must include: measured facts, source timestamps, rule/policy ids used, expected effect, risk, and what would falsify the diagnosis.
7. If evidence is insufficient, recommend a measurement window instead of changing money settings.

Read `references/source-policy.md` before any decision involving spend, price, stock or irreversible operations.
