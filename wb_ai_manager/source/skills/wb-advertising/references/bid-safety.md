# Bid safety contract

The model does not have permission to execute raw WB advertising writes.

A bid proposal must be represented semantically in RUB as a staged change. The backend:

- reads the current live bid;
- validates campaign/product provenance;
- converts units for the specific WB endpoint;
- enforces the store's absolute bid cap;
- enforces maximum percentage change per step;
- checks cooldown after the previous change;
- checks evidence minimums;
- creates a preview with BEFORE → AFTER;
- records a rollback value;
- requires approval unless a policy explicitly marks the exact operation safe for guarded auto mode;
- reads the value back from WB after execution.

Current connector semantics may differ by endpoint. For example, the underlying connector documents ordinary campaign bid values in kopecks and search-cluster bids in RUB per 1,000 views. Never assume the units are interchangeable.

If the model asks for an out-of-range value, the backend must reject the proposal rather than clamp it silently.
