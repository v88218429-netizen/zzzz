# Research comparison — what we reuse and what we reject

Snapshot: 2026-09-18.

## 1. DeviceIngineering/wb-mcp-server

Use: broad WB Seller API connector and diagnostics.

Strong ideas:
- wide Seller API surface including ads, prices, cards, FBS, finance, reviews and diagnostics;
- multi-store support;
- current tool documentation with explicit endpoint semantics;
- API degradation/news awareness.

Do not use as the "brain": it is a tool surface, not a seller-specific decision policy.

## 2. webkoth/sellerai

Use: workflow/skill decomposition and operations-director → specialist hierarchy.

Strong ideas:
- narrow domain agents;
- knowledge/skills separate from tools;
- preview/confirm pattern for writes;
- repeatable reports and workflows.

Changes in our design:
- generic CTR/DRR norms are alert defaults only, never sufficient evidence for a money-changing action;
- decisions use seller/SKU baselines and unit economics;
- raw writes are hidden behind a backend action gateway.

## 3. Anthropic commerce-agents

Use: safety architecture inspiration, independent of Claude runtime.

Strong ideas we port:
- provenance gate: IDs used for a write must come from live tool reads;
- stage first, apply later;
- preview card before apply;
- guardrails rechecked at apply time;
- approval separated from recommendation;
- durable change ledger/audit trail.

This is the strongest safety pattern found in the reviewed agent repositories.

## 4. ferrants/amazon-seller-agent-claude-code

Use: operational memory pattern.

Strong ideas we port:
- durable brand/store profile;
- initiatives with PLAN / LOG / METRICS;
- orient from previous work before taking new action;
- write down what was changed and why.

For WB AI Manager this becomes an experiment/intervention ledger per tenant/SKU/campaign.

## 5. MissiaL/wildberries-api

Use: official-schema knowledge pipeline.

Strong ideas:
- local snapshots of official WB OpenAPI schemas;
- production host allowlist;
- operation-specific rate-limit references;
- guarded helper instead of ad hoc HTTP calls.

For our product this becomes the WB Official knowledge layer and a scheduled documentation/schema update pipeline.

## 6. ilyautov/marketplaces-mcp-ru

Use: safety catalog and schema-driven API coverage ideas.

Strong ideas:
- every operation classified read / write / destructive;
- CI fails if a mutating HTTP method is mislabeled read;
- explicit confirmation flags;
- compact meta-tool surface instead of overwhelming a model with hundreds of raw endpoints.

Our stricter version additionally hides raw write endpoints from ChatGPT entirely.

## 7. shndo1337/wildberries-mcp and other public-WB connectors

Use: competitor/search intelligence only.

Constraint:
- public/internal WB endpoints can change or rate-limit aggressively;
- therefore public intelligence is isolated from the Seller API connector and can never become the only source for a money-changing decision.

# Conclusion

The product is not one "smart prompt". It combines:

1. official/live data connector;
2. deterministic monitoring;
3. knowledge/skills;
4. seller-specific economics and baselines;
5. staged change ledger;
6. independent guardrails;
7. ChatGPT as the conversational reasoning/UI layer;
8. continuous tests and backtests.
