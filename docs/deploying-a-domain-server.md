# Deploying a mikrós domain server to Horizon

This page is the mikrós-specific overlay on top of FastMCP's Horizon
deployment docs. For the full visual walk-through, read
<https://gofastmcp.com/deployment/prefect-horizon>. Everything below covers
only what is specific to a mikrós domain repo (entrypoint, git-pinned
runtime, smoke test, Remix composition).

## Prerequisites

- A Horizon account at <https://horizon.prefect.io/>.
- Push access to a domain repo under `github.com/agora-creations/` (e.g.
  `mikros-writing`, `mikros-analysis`, `mikros-professional`).
- The repo follows the standard mikrós domain layout at its root:
  - `main.py` exporting `mcp` (the FastMCP server instance).
  - `pyproject.toml` pinning `mikros-server @ git+https://github.com/agora-creations/mikros.git@v0.1.0`.
  - `Dockerfile`.
  - `workflows/` with the domain's YAML workflow files.
  - `deploy.sh`.
- `python3` locally with the `fastmcp` package installed (for the smoke test).

## Deploy steps

1. Verify the entrypoint locally:
   ```sh
   fastmcp inspect main.py:mcp
   ```
   Expected: non-zero workflows listed, no import errors.

2. Push the commit you intend to deploy to the repo's default branch.

3. Sign in to <https://horizon.prefect.io/>.

4. In the Horizon dashboard: **Servers → New Server → Connect GitHub repo**,
   and select the domain repo.

5. On the server config screen, set **Entrypoint** to exactly:
   ```
   main.py:mcp
   ```
   Leave other fields at Horizon defaults unless the FastMCP doc above says
   otherwise.

6. Click **Deploy**. Tail the build logs — the container build runs
   `fastmcp inspect` as a health check and will surface dependency errors
   there (see Troubleshooting).

7. When the deploy reports healthy, copy the server URL. It will look like
   `https://<server-name>.fastmcp.app/mcp`.

## Smoke test

**Interactive (ChatMCP):** open ChatMCP, add the server URL from step 7
above, and call `list_workflows`. Every workflow YAML present in the repo's
`workflows/` directory must appear. ChatMCP is for verification — not daily
use.

**Automated (CLI):** run the smoke script from the core `mikros` repo. It
exits 0 only if every `--expected` workflow is present:

```sh
python3 scripts/smoke_endpoint.py https://mikros-writing.fastmcp.app/mcp \
    --expected essay blog
```

Expected domain inventories:

- `mikros-writing` → `essay blog`
- `mikros-analysis` → `research decision`
- `mikros-professional` → `coding`

A zero exit with `OK: all N expected workflow(s) present …` means the
deploy is wired correctly. Any non-zero exit prints a one-line error on
stderr — feed that into Troubleshooting below.

## Adding to an existing Remix Agent

1. In the Horizon dashboard, open **Agents → \<your Remix Agent\>**.
2. Click **Add Server** and pick the server you just deployed.
3. Save. Horizon re-generates the Agent URL; prior URLs remain valid.
4. Smoke-test the composed Agent by asking the agent (in ChatMCP or the
   Horizon Agent console) to call one workflow from the newly-added
   server. If `list_workflows` now includes the new inventory, composition
   is live.

The reference endpoint `https://Mikros.fastmcp.app/mcp` (serves the bundled
`example.yaml`) is read-only during M008 — if your Remix Agent references
it, verify but do not modify its entrypoint config.

## Troubleshooting

- **Build fails during `fastmcp inspect` with a dependency resolution error.**
  The pinned runtime `mikros-server @ git+…@v0.1.0` is public, so auth is
  not the cause. Tail the full build log; typical root cause is a
  transitive version conflict introduced by a newly-added dep in the
  domain repo's `pyproject.toml`. Fix the conflict locally
  (`uv sync` or `pip install -e .`) until `fastmcp inspect main.py:mcp`
  runs clean, then re-push.

- **Server boots but `list_workflows` is empty or wrong.**
  Either the `workflows/` directory was not included in the build context
  (check the `Dockerfile` `COPY` lines) or the YAML files failed to parse
  at import time (check the server's runtime logs in Horizon for loader
  warnings).

- **Entrypoint mismatch (stale `server/main.py:mcp`).**
  Pre-split mikrós used `server/main.py:mcp`. Post-split domain repos use
  `main.py:mcp` at repo root. If Horizon is pointing at the old path, edit
  the server config → **Entrypoint** field → set to `main.py:mcp` → redeploy.

- **Smoke script prints `ERROR: connection failure`.** The server URL is
  wrong, not yet healthy, or the `/mcp` suffix is missing. Confirm the URL
  in the Horizon dashboard and retry.
