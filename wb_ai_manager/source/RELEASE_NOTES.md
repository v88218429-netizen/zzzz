# WB AI Manager v1.1.4

Iterative stability audit of the standalone v1.1 control-and-learning release. The release gate is no longer a single successful run: any newly discovered defect resets the audit, and the candidate is accepted only after two consecutive independent clean passes.

## Corrections in 1.1.4

- made the read-only safety boundary a code-level build fuse: changing `.env`, policy, approval mode or guarded-auto settings cannot enable WB writes in this release;
- removed the raw `wb-mcp` external example that could bypass the safe high-level gateway; both MCP configs now use the local guarded `stdio` launcher;
- corrected the MCP setup error so it points to the real `START_WB_AI_MANAGER.command` launcher;
- fixed `.env` credential writes so backslashes and regex replacement sequences in tokens/keys are stored literally;
- AUTO now surfaces connection setup on first launch when no WB token is present;
- blocking Google Sheets/browser and source-discovery work is moved off the FastAPI event loop;
- Google Sheets browser export no longer fails merely because an unrelated stale `.crdownload` exists;
- DEMO is now genuinely network-independent: no remote policy, auto-update or public WB storefront calls are needed for the demo run;
- optional public competitor search now has bounded timeout/failure behavior instead of being able to stall a full audit for minutes;
- AUTO sheet refresh prefers a configured Apps Script bridge before browser-export fallback;
- runtime supervisor detects an already-running WB AI Manager before any update operation and detects dead child processes promptly;
- both live launch paths (`AUTO` and `Мой Wildberries`) now restore `WB_MODE=live`, `DATA_DIR=./data` and public-search state after DEMO;
- version metadata is aligned to `1.1.4` across the application, package and plugin.

## Verification gate

The final candidate is required to pass, from clean state/extraction:

- complete Python test suite;
- compile/shell/JavaScript syntax checks;
- `update_smoke.py`;
- deterministic DEMO with all 19 agents and Decision Engine;
- API/UI endpoint smoke tests and truthful read-only health state;
- hard-fuse write-block probe under deliberately permissive runtime settings;
- duplicate-instance supervisor behavior;
- full ZIP update, deliberately broken update, rollback and executable-bit preservation;
- preservation of tenant-owned `catalog.csv` and `watch_queries.yaml`;
- real v1.0 SQLite schema/data migration;
- package hygiene: no secrets, `.env`, venv, user databases, caches or build artifacts.
