# Brain-store Seeder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `brain/seeder/seed.py` — a deterministic synthetic-event generator that fills the brain store with ~30 days of believable operational events keyed to existing Backstage catalog entity refs, including the planted `payment-gateway` deploy→incident correlation the demo turns on.

**Architecture:** A standalone Python script run locally against the brain store (`postgresql://brain:brain@localhost:5432/brain`). Fixed RNG (`seed 42`) for all variable content; timestamps anchor to `now()` minus fixed/seeded offsets so the protagonist always has an open incident "right now". Idempotent: it `TRUNCATE`s and regenerates on every run. A `make seed` target runs it via `uv run`, which reads PEP 723 inline dependency metadata in the script and resolves an ephemeral, cached environment — no repo venv and nothing installed into the system Python.

**Tech Stack:** Python 3.14 (Homebrew) via `uv` (PEP 723 inline deps), `psycopg2-binary`, Make.

**Design / data contract:** `docs/superpowers/specs/2026-06-15-brain-store-data-layer-design.md` (sections "Data contract: …"). Prerequisite slice (Postgres + schema) is merged to `main` and running.

---

## Prerequisites

The brain store must be up: `make up && make db-up && make db-init` (Postgres running in `data`, schema applied, reachable at `localhost:5432`). Verify with `make db-up` (idempotent) before seeding.

## File structure

- **Create** `brain/seeder/seed.py` — the generator (PEP 723 header → constants → helpers → event builders → `main()`). Dependencies are declared inline in the script's `# /// script` block; no `requirements.txt`.
- **Modify** `.gitignore` — ignore Python caches.
- **Modify** `Makefile` — add `SEEDER_DIR` var and a `seed` target that runs `uv run`.

## Event tuple shape (used throughout)

Every builder returns a list of tuples:
`(entity_ref: str, source: str, kind: str, severity: str|None, payload: dict, occurred_at: datetime)`.
`main()` inserts them with `psycopg2.extras.Json(payload)` into the `jsonb` column.

---

### Task 1: Runnable seeder scaffold (empty dataset)

Prove the plumbing — venv, DB connect, `TRUNCATE`, insert path — before any data logic. With an empty event list the script should truncate and report 0.

**Files:**
- Create: `brain/seeder/seed.py`
- Modify: `.gitignore`
- Modify: `Makefile`

- [ ] **Step 1: Write `brain/seeder/seed.py` (scaffold)**

Create `brain/seeder/seed.py` with exactly this content. The `# /// script` block is PEP 723 inline metadata that `uv run` reads to build the ephemeral environment (Python 3.14 + `psycopg2-binary`):

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.14"
# dependencies = ["psycopg2-binary"]
# ///
"""Synthetic event seeder for the SRE Second Brain brain store.

Writes ~30 days of believable operational events keyed to existing Backstage
catalog entity refs. Deterministic (fixed RNG); timestamps anchor to now() so the
protagonist always has an open incident "right now". Idempotent: truncates and
regenerates on every run.

Run: make seed   (or: python brain/seeder/seed.py)
"""
import os
import random
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values, Json

DSN = os.environ.get("BRAIN_DSN", "postgresql://brain:brain@localhost:5432/brain")
RNG = random.Random(42)
NOW = datetime.now(timezone.utc)

PROTAGONIST = "component:default/payment-gateway"
BACKGROUND = [
    "component:default/notification-dispatcher",
    "component:default/inventory-tracker",
    "component:default/auth-service",
    "component:default/fraud-detection-service",
]


def hours(n):
    return NOW - timedelta(hours=n)


def days(n):
    return NOW - timedelta(days=n)


def build_events():
    """Return the full list of event tuples. Filled in by later tasks."""
    return []


def main():
    events = build_events()
    conn = psycopg2.connect(DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE events RESTART IDENTITY;")
            cur.execute("DELETE FROM dossiers;")
            if events:
                execute_values(
                    cur,
                    "INSERT INTO events "
                    "(entity_ref, source, kind, severity, payload, occurred_at) "
                    "VALUES %s",
                    [
                        (ref, src, kind, sev, Json(payload), ts)
                        for (ref, src, kind, sev, payload, ts) in events
                    ],
                )
        services = {e[0] for e in events}
        print(f"✓ seeded {len(events)} events across {len(services)} services")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Update `.gitignore`**

Append these lines to `.gitignore`:

```
# Python
__pycache__/
*.pyc
```

- [ ] **Step 3: Add the `seed` target to the Makefile**

In `Makefile`, after the line `SCHEMA_SQL ?= brain/schema.sql`, add:

```makefile
SEEDER_DIR ?= brain/seeder
```

Append `seed` to the `.PHONY:` line (after `db-init`).

Insert this target immediately before the `help:` target (recipe lines use real TABs). `uv run` reads the script's inline metadata, fetches Python 3.14 + `psycopg2-binary` into its cache, and runs — no repo venv, no system-Python install:

```makefile
seed: ## Generate synthetic events into the brain store (deterministic, idempotent)
	@echo "→ seeding brain store"
	@uv run "$(SEEDER_DIR)/seed.py"
```

- [ ] **Step 4: Run it**

Run: `make seed`
Expected: uv resolves Python 3.14 + `psycopg2-binary` (first run downloads them), then the script ends with `✓ seeded 0 events across 0 services`, no traceback.

- [ ] **Step 5: Confirm the table is empty and reachable**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc 'select count(*) from events;'
```
Expected: `0`.

- [ ] **Step 6: Commit**

```bash
git add brain/seeder/seed.py .gitignore Makefile
git commit -m "feat: seeder scaffold + make seed target (uv)"
```

---

### Task 2: Protagonist events (the planted timeline)

Implement `payment-gateway`'s fixed-offset timeline: a resolved incident with no nearby deploy, a healthy deploy, the change deploy, the open sev2 ~30 min later, two firing alerts, and a 5-week downward coverage trend.

**Files:**
- Modify: `brain/seeder/seed.py`

- [ ] **Step 1: Add `protagonist_events()` above `build_events()`**

Insert this function immediately before `def build_events():`:

```python
def protagonist_events():
    ref = PROTAGONIST
    ev = []

    # Contrast #1: a resolved incident with NO deploy in the preceding hours.
    started = hours(30)
    ev.append((ref, "pagerduty", "incident", "sev3", {
        "title": "intermittent timeout spike on /charge",
        "status": "resolved",
        "started_at": started.isoformat(),
        "resolved_at": hours(29).isoformat(),
        "service": "payment-gateway",
        "url": "https://pd.example/incidents/PD-1980",
    }, started))

    # Contrast #2: a healthy deploy that caused no incident.
    ev.append((ref, "argocd", "deploy", None, {
        "revision": "v2.3.1",
        "image_tag": "1.8.4->1.9.0",
        "status": "Succeeded",
        "sync_status": "Synced",
        "author": "platform-bot",
        "url": "https://argo.example/applications/payment-gateway",
    }, hours(26)))

    # THE change: deploy ~2h ago.
    ev.append((ref, "argocd", "deploy", None, {
        "revision": "v2.4.0",
        "image_tag": "1.9.0->1.10.0",
        "status": "Succeeded",
        "sync_status": "Synced",
        "author": "platform-bot",
        "url": "https://argo.example/applications/payment-gateway",
    }, hours(2)))

    # The open sev2 incident, ~30 min after v2.4.0 (the planted correlation).
    inc_start = hours(1.5)
    ev.append((ref, "pagerduty", "incident", "sev2", {
        "title": "p99 latency breach on /charge",
        "status": "open",
        "started_at": inc_start.isoformat(),
        "resolved_at": None,
        "service": "payment-gateway",
        "url": "https://pd.example/incidents/PD-2041",
    }, inc_start))

    # Two firing alerts, started with the incident.
    ev.append((ref, "prometheus", "alert", "critical", {
        "alertname": "HighErrorRate",
        "expr": "rate(http_requests_total{code=~\"5..\"}[5m]) > 0.05",
        "value": 0.087,
        "threshold": 0.05,
        "state": "firing",
        "started_at": inc_start.isoformat(),
        "resolved_at": None,
    }, inc_start))
    ev.append((ref, "prometheus", "alert", "warning", {
        "alertname": "HighLatencyP99",
        "expr": "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) > 0.8",
        "value": 1.24,
        "threshold": 0.8,
        "state": "firing",
        "started_at": inc_start.isoformat(),
        "resolved_at": None,
    }, inc_start))

    # Weekly SonarQube scans: coverage trending DOWN, last gate ERROR.
    coverages = [78.0, 74.0, 71.0, 69.0, 66.0]
    smells = [120, 150, 175, 198, 214]
    for i, (cov, sm) in enumerate(zip(coverages, smells)):
        week_ago = 28 - i * 7  # 28, 21, 14, 7, 0 days ago
        gate = "ERROR" if i == len(coverages) - 1 else "OK"
        ev.append((ref, "sonarqube", "scan", None, {
            "coverage": cov,
            "code_smells": sm,
            "bugs": 5 + i,
            "vulnerabilities": 1 + (i // 2),
            "quality_gate": gate,
            "project_key": "payment-gateway",
        }, days(week_ago)))

    return ev
```

- [ ] **Step 2: Call it from `build_events()`**

Replace the body of `build_events()` so it reads:

```python
def build_events():
    """Return the full list of event tuples."""
    events = []
    events += protagonist_events()
    return events
```

- [ ] **Step 3: Run the seeder**

Run: `make seed`
Expected: `✓ seeded 11 events across 1 services` (1 resolved + 1 open incident, 2 deploys, 2 alerts, 5 scans).

- [ ] **Step 4: Verify the protagonist's "right now" state and trend**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select 'open_incidents='   || count(*) from events where entity_ref='component:default/payment-gateway' and kind='incident' and payload->>'status'='open';
  "
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select 'firing_alerts='    || count(*) from events where entity_ref='component:default/payment-gateway' and kind='alert' and payload->>'state'='firing';
  "
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select 'latest_deploy='    || (payload->>'revision') from events where entity_ref='component:default/payment-gateway' and kind='deploy' order by occurred_at desc limit 1;
  "
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select string_agg(payload->>'coverage', ',' order by occurred_at) from events where entity_ref='component:default/payment-gateway' and kind='scan';
  "
```
Expected, in order: `open_incidents=1`, `firing_alerts=2`, `latest_deploy=v2.4.0`, and `78,74,71,69,66`.

- [ ] **Step 5: Commit**

```bash
git add brain/seeder/seed.py
git commit -m "feat: seed protagonist timeline (payment-gateway correlation)"
```

---

### Task 3: Background service events

Give the other four services sparse, healthy history so `list_services` has breadth and `payment-gateway` is unambiguously the one in trouble (no other open incidents, no other firing alerts, stable coverage, gate OK).

**Files:**
- Modify: `brain/seeder/seed.py`

- [ ] **Step 1: Add background data + builder above `build_events()`**

Insert these constants and the function immediately before `def build_events():`:

```python
INCIDENT_TITLES = [
    "elevated 5xx after rollout",
    "slow query on read replica",
    "memory pressure: pod OOMKilled",
    "cache stampede on cold start",
    "upstream dependency timeout",
]
ALERT_CHOICES = [
    ("HighErrorRate", "critical"),
    ("HighLatencyP99", "warning"),
    ("PodRestarts", "warning"),
]


def background_events(ref):
    short = ref.split("/")[-1]
    ev = []

    # 1-3 resolved incidents over the last ~month.
    for _ in range(RNG.randint(1, 3)):
        start = days(RNG.randint(3, 29)) - timedelta(hours=RNG.randint(0, 12))
        ev.append((ref, "pagerduty", "incident", RNG.choice(["sev3", "sev4"]), {
            "title": RNG.choice(INCIDENT_TITLES),
            "status": "resolved",
            "started_at": start.isoformat(),
            "resolved_at": (start + timedelta(hours=RNG.randint(1, 6))).isoformat(),
            "service": short,
            "url": f"https://pd.example/incidents/PD-{RNG.randint(1000, 1999)}",
        }, start))

    # 1-4 resolved alerts (none firing now).
    for _ in range(RNG.randint(1, 4)):
        start = days(RNG.randint(2, 29))
        name, sev = RNG.choice(ALERT_CHOICES)
        ev.append((ref, "prometheus", "alert", sev, {
            "alertname": name,
            "expr": "<synthetic alert expression>",
            "value": round(RNG.uniform(0.05, 0.2), 3),
            "threshold": 0.05,
            "state": "resolved",
            "started_at": start.isoformat(),
            "resolved_at": (start + timedelta(hours=RNG.randint(1, 5))).isoformat(),
        }, start))

    # Weekly SonarQube scans: stable coverage, gate OK.
    base = RNG.uniform(78, 86)
    for i in range(5):
        cov = round(base + RNG.uniform(-1.5, 1.5), 1)
        ev.append((ref, "sonarqube", "scan", None, {
            "coverage": cov,
            "code_smells": RNG.randint(30, 90),
            "bugs": RNG.randint(0, 4),
            "vulnerabilities": RNG.randint(0, 2),
            "quality_gate": "OK",
            "project_key": short,
        }, days(28 - i * 7)))

    # 1-4 successful deploys per week over 4 weeks.
    for week in range(4):
        for _ in range(RNG.randint(1, 4)):
            when = days(RNG.randint(week * 7, week * 7 + 6)) - timedelta(hours=RNG.randint(0, 18))
            ev.append((ref, "argocd", "deploy", None, {
                "revision": f"v1.{RNG.randint(0, 9)}.{RNG.randint(0, 9)}",
                "image_tag": f"1.{RNG.randint(0, 9)}.{RNG.randint(0, 9)}",
                "status": "Succeeded",
                "sync_status": "Synced",
                "author": "platform-bot",
                "url": f"https://argo.example/applications/{short}",
            }, when))

    return ev
```

- [ ] **Step 2: Extend `build_events()`**

Replace `build_events()` so it reads:

```python
def build_events():
    """Return the full list of event tuples."""
    events = []
    events += protagonist_events()
    for ref in BACKGROUND:
        events += background_events(ref)
    return events
```

- [ ] **Step 3: Run the seeder**

Run: `make seed`
Expected: `✓ seeded <N> events across 5 services` where N is roughly 60–90 (11 protagonist + variable background). The exact N is stable across runs (fixed RNG).

- [ ] **Step 4: Verify the protagonist is unambiguously the one in trouble**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select 'distinct_services=' || count(distinct entity_ref) from events;
  "
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select 'other_open_or_firing=' || count(*) from events
    where entity_ref <> 'component:default/payment-gateway'
      and ( (kind='incident' and payload->>'status'='open')
         or (kind='alert'    and payload->>'state'='firing') );
  "
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -tAc "
    select 'gate_error_services=' || string_agg(distinct entity_ref, ',') from events
    where kind='scan' and payload->>'quality_gate'='ERROR';
  "
```
Expected: `distinct_services=5`; `other_open_or_firing=0`; `gate_error_services=component:default/payment-gateway` (only the protagonist).

- [ ] **Step 5: Commit**

```bash
git add brain/seeder/seed.py
git commit -m "feat: seed background services (healthy history)"
```

---

### Task 4: Determinism + demo-question integration check

Confirm the seeder is idempotent/deterministic and that the data answers the four rehearsed demo questions.

- [ ] **Step 1: Idempotency / determinism**

Run:
```bash
make seed
A=$(kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- env PGPASSWORD=brain psql -U brain -d brain -tAc 'select count(*) from events;')
make seed
B=$(kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- env PGPASSWORD=brain psql -U brain -d brain -tAc 'select count(*) from events;')
echo "run1=$A run2=$B"
```
Expected: `run1` equals `run2` (TRUNCATE + fixed RNG → identical row count every run).

- [ ] **Step 2: Demo Q2 — "what's going on with payment-gateway right now?"**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -c "
    select kind, severity, occurred_at, payload->>'title' as title, payload->>'alertname' as alert
    from events
    where entity_ref='component:default/payment-gateway'
      and ( (kind='incident' and payload->>'status'='open')
         or (kind='alert'    and payload->>'state'='firing') )
    order by occurred_at;
  "
```
Expected: 3 rows — the open sev2 incident "p99 latency breach on /charge" and the two firing alerts (`HighErrorRate`, `HighLatencyP99`).

- [ ] **Step 3: Demo Q3 — "could the incident relate to a recent change?"**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -c "
    select occurred_at, kind, payload->>'revision' as rev, payload->>'title' as title
    from events
    where entity_ref='component:default/payment-gateway' and kind in ('deploy','incident')
    order by occurred_at desc limit 4;
  "
```
Expected: newest rows show the open incident (~90 min ago) immediately preceded by the `v2.4.0` deploy (~2h ago) — the correlation is visible by timestamp proximity.

- [ ] **Step 4: Demo Q4 — "whose code quality is trending the wrong way?"**

Run:
```bash
kubectl --context kind-sre-second-brain -n data exec deploy/postgres -- \
  env PGPASSWORD=brain psql -U brain -d brain -c "
    select entity_ref,
           (array_agg(payload->>'coverage' order by occurred_at))[1] as first_cov,
           (array_agg(payload->>'coverage' order by occurred_at))[array_length(array_agg(payload->>'coverage'),1)] as last_cov
    from events where kind='scan' group by entity_ref order by entity_ref;
  "
```
Expected: every service's first/last coverage is roughly flat except `component:default/payment-gateway`, which falls from `78` to `66`.

- [ ] **Step 5: No commit**

Verification only.

---

## Self-review checklist (completed by plan author)

- **Spec coverage:** payload shapes (Tasks 2–3 match the design's four source schemas), entity refs (PROTAGONIST + 4 BACKGROUND = the design's list), planted timeline (Task 2 = the design's timeline exactly, incl. both contrasts), background = healthy/sparse (Task 3), determinism + rebuild (Task 1 TRUNCATE, RNG(42); Task 4 idempotency check). ✓
- **Placeholders:** none — full code in every step; `<synthetic alert expression>` is intentional literal payload text, not a TODO. ✓
- **Consistency:** event tuple order `(entity_ref, source, kind, severity, payload, occurred_at)` is identical in the builders and the `execute_values` insert; `Json(payload)` adapts the dict to `jsonb`; `BRAIN_DSN` default matches the data-layer connection contract; protagonist ref string is identical across seeder and every verification query. ✓
