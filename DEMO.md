# SRE Second Brain — Demo Runbook

The 60-second pitch: *the Backstage catalog is the spine — every operational signal is keyed to a catalog entity ref and indexed in Postgres for exact, time-ranged retrieval. The agent gets precise tools over a well-indexed store, not a fuzzy memory. Vectors are reserved for prose (designed, deliberately not built).*

## Bring it up (one command)

```sh
PATH="$HOME/.local/share/nvm/v22.22.3/bin:$PATH" make demo
```

`make demo` cold-starts everything: kind cluster → ingress-nginx → brain-store Postgres + schema → synthetic seed → Backstage → MCP registration, then prints this script.

> The `PATH=...` prefix puts Node 22/24 + Corepack `yarn@4.4.1` on PATH for the Backstage build — `make`'s shell doesn't source nvm. Adjust the version to one you have installed (`ls ~/.local/share/nvm`).

Then **start a NEW Claude Code session in this repo** — MCP tools load at session start, so the `second-brain` tools only appear in a fresh session.

Endpoints once up:
- Backstage catalog: <http://localhost:3000> (guest sign-in)
- Brain store: `postgresql://brain:brain@localhost:5432/brain`
- MCP server: `second-brain` (registered with Claude Code)

## The four questions

Ask these in order in the new Claude Code session:

**1. "What services exist on this platform and who owns them?"**
→ `list_services` reads the Backstage catalog REST API → components with owners/system. *Establishes the catalog as the spine.*

**2. "What's going on with payment-gateway right now?"**
→ `get_service_dossier` (+ `get_active_alerts`) → the open **sev2** "p99 latency breach on /charge" and two **firing** alerts (`HighErrorRate`, `HighLatencyP99`), plus the downward quality trend. *The headline — one assembled read.*

**3. "Could the open incident be related to a recent change?"**
→ `get_recent_deploys` → the **`v2.4.0`** deploy (`1.9.0→1.10.0`) ~30 min before the incident, with the older `v2.3.1` deploy and an earlier *resolved* incident as contrast. *The money moment — reasoning, not pattern-matching.*

**4. "Which service's code quality is trending the wrong way?"**
→ `get_quality_trend` → `payment-gateway` falling **78 → 66** while the others stay flat. *Time-ranged retrieval across services.*

**Closer:** open <http://localhost:3000> and show `payment-gateway` in the Backstage catalog — the same entity ref (`component:default/payment-gateway`) the agent keyed every signal to.

## Runway / pre-flight checklist

1. **Rehearse the cold path once**, end to end: `make demo` (with the PATH prefix) → new Claude Code session → the four questions → the Backstage closer. This is the path that hasn't been exercised fully cold, so do it at least once before the interview.
2. **Record a ~90-second backup video** of the happy path. If the live demo wobbles, switch to the recording without apologising and keep talking architecture.
3. **Demo from this machine** (where it was built). Pre-pull images so a re-run is fast.

## If something breaks

- **Backstage 404s / empty reply at :3000** → ingress isn't installed. `make ingress-install` (the `demo` target sequences this; only an issue if you ran `make up` alone).
- **`yarn: command not found` during the build** → wrong Node on PATH. Prepend your nvm node 22/24 bin (see the `make demo` command above).
- **MCP tools missing in Claude Code** → you're in the session that was open before registration. Start a new session. Re-register with `make mcp-register` if needed.
- **Agent answers look empty / stale** → re-seed: `make seed` (deterministic; safe to re-run, truncates and regenerates).
- **`payment-gateway` shows no "right now" incident** → the seed anchors to `now()`, so just re-run `make seed`.

## Talking points (the BSA axes)

- **Technical depth:** composite-index exact retrieval vs. a vector DB (rejected, with reasons); entity-ref as the canonical key; async-embedding sidecar reserved for prose, designed and deliberately not built.
- **Problem-solving:** the planted deploy→incident correlation the agent discovers live.
- **Autonomy / quality:** one-command cold start, deterministic seed, hand-written schema — all reproducible.
