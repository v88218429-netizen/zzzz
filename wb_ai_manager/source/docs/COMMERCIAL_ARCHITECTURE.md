# Commercial architecture — no OpenAI API required

## Product surface

Customer installs one **WB AI Manager** plugin in ChatGPT, not 19 separate bots.

Inside the plugin:
- skills route the conversation to advertising, inventory, finance, cards, etc.;
- the remote MCP server reads the customer's live WB data;
- the deterministic backend monitors continuously even when ChatGPT is closed;
- all writes are staged and safety-checked outside the language model.

## Model cost

In the no-OpenAI-API product mode, conversational reasoning happens inside the customer's ChatGPT product session. Our backend does not call the OpenAI API for each message.

This means:
- we still pay for our infrastructure, database, monitoring and external services;
- the customer needs an eligible ChatGPT surface/account for the plugin;
- if we later want AI reasoning to run autonomously on our servers 24/7, that separate feature will require a model API or a self-hosted model.

## Tenant model

One published plugin can serve many customers.

Each customer:
1. creates an account in our service;
2. subscribes on our site;
3. connects the plugin via OAuth;
4. links one or more WB seller cabinets;
5. receives an isolated tenant id and encrypted credential vault;
6. completes onboarding: costs, tax model, product priorities, risk caps, ad goals;
7. starts in Shadow mode.

Do **not** make a separate code copy or separate bot deployment per customer unless an enterprise customer needs isolated infrastructure.

## Billing

Do not depend on a plugin-directory billing mechanism. Charge through our own SaaS account/subscription system and let OAuth entitlement decide whether the MCP tools are available.

Possible packaging to validate, not fixed market prices:
- Monitor: continuous deterministic monitoring + alerts;
- Copilot: ChatGPT plugin + diagnosis + staged recommendations;
- Autopilot: selected backend rules can execute under tenant guardrails;
- Agency/Enterprise: multi-cabinet, teams, policy templates, audit export.

## 24/7 behavior without a model API

The backend scheduler handles:
- campaign spend/DRR anomalies;
- stockout risk;
- card blocks/errors;
- FBS operational incidents;
- finance/penalty alerts;
- review/question queues;
- API health;
- scheduled snapshots and history.

When a complex anomaly occurs, it creates an evidence packet. The next time the customer opens ChatGPT, the plugin lets ChatGPT reason over that packet and explain/plan. Urgent deterministic alerts can go to Telegram/web push immediately.

## Later optional server-side AI

A future premium mode may add a server-side model API for autonomous causal investigation and natural-language reports. It is an enhancement, not a dependency of the core product.
