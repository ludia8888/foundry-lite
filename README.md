# Foundry-lite

Foundry-lite is a small, replayable operational object system inspired by the local project
documents in this repository.

The MVP core proves this loop:

```text
CSV/PostgreSQL snapshot
-> raw dataset
-> DuckDB SQL transform
-> clean dataset
-> ontology activation
-> object index
-> ApproveOrder action
-> action_log/order_current materialization
-> downstream transform
-> Customer object refresh
```

Local demo:

```bash
PYTHONPATH=libs:apps/cli python3.12 -m foundry_lite_cli.main demo run-supply-chain
```

The local adapter uses SQLite plus local object storage under `.foundry-lite/`. The domain and
application contracts keep the same boundaries described in the planning docs: dataset versions are
immutable, writes are audited, action apply requires `expectedObjectVersion`, and all outputs are
committed through the dataset transaction/manifest protocol.

Quality and observability:

```bash
pnpm ci:gate
docker compose -f infra/docker-compose.dev.yml up -d prometheus tempo grafana
```

See [docs/quality-observability.md](docs/quality-observability.md) for static analysis, dynamic
diagnostics, OpenTelemetry, Grafana, Playwright, and CI/CD gates.
