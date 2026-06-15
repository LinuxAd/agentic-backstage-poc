-- Brain store schema. Applied by `make db-init`. Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS events (
    id          bigserial   PRIMARY KEY,
    entity_ref  text        NOT NULL,   -- component:default/payment-gateway
    source      text        NOT NULL,   -- pagerduty | prometheus | sonarqube | argocd
    kind        text        NOT NULL,   -- incident | alert | scan | deploy
    severity    text,                   -- sev1..sev4 | critical | warning | null
    payload     jsonb       NOT NULL,   -- source-shaped detail
    occurred_at timestamptz NOT NULL
);

-- Exact entity lookup, optionally narrowed by source, newest first.
CREATE INDEX IF NOT EXISTS idx_events_lookup
    ON events (entity_ref, source, occurred_at DESC);

-- Exact entity lookup narrowed by signal kind, newest first.
CREATE INDEX IF NOT EXISTS idx_events_kind
    ON events (entity_ref, kind, occurred_at DESC);

-- Materialised per-service rollup. Cache: rebuilt on read by the MCP server,
-- NOT written by the seeder.
CREATE TABLE IF NOT EXISTS dossiers (
    entity_ref   text        PRIMARY KEY,
    generated_at timestamptz NOT NULL,
    summary      jsonb       NOT NULL   -- counts, open items, trend, last deploy
);
