# Budgets and spending alerts

Operations lets each account create up to 100 budgets and delete its own budgets. Standalone mode supports the same controls on the local ledger. Choose an account-wide scope or an exact project/model identifier, daily or monthly UTC calendar period, positive USD threshold, and either estimated API-equivalent or supplied billed cost. Model scope matches that exact identifier across providers; project scope matches the stored project identifier.

Spent amounts sum only retained canonical usage events in the current period through the evaluation time. Revisions replace earlier measurements, duplicate ingestion does not add spend, and future-dated events are excluded. Missing prices remain unavailable with an explicit unpriced-request count. Subscription usage estimates are not invoices. Budgets are advisory thresholds, not spending enforcement.

Dashboard alerts appear at or above the threshold and refresh every 30 seconds while open. Desktop notifications require browser permission and are deduplicated by budget and period for the mounted account view. Reloading or signing in again can notify again. Notifications are not delivered when the dashboard is closed; server-side email/webhook scheduling is not implemented. Current known spend may fall after corrections or deletion.

Account authentication scopes all reads. Server mutations also require same-origin CSRF validation. Budget definitions survive restarts in each account's SQLite ledger. Retention/deletion can remove spending evidence; use retention covering the reporting period. Budget totals are sums of recorded costs, not reconciled provider invoices.
