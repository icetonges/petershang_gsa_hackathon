# Deploying

Local development runs the server over **stdio**, which is how Claude
Desktop and Claude Code launch it. To share it with an agent platform, deploy
it somewhere that serves **streamable HTTP**, then register it.

This repository ships one deployment kit: **Databricks Apps**.

| Target | Kit | Transport | When |
|---|---|---|---|
| Databricks Apps | [`databricks/`](databricks/) | streamable HTTP | The hackathon workspace, and anywhere agents already live in Databricks |

## The server code does not change

`src/mcp_server_creator/app.py` selects its transport at startup:

- a platform port (`DATABRICKS_APP_PORT`, or a generic `PORT`) → HTTP on that
  port, bound to `0.0.0.0`;
- `MCP_TRANSPORT=streamable-http` → HTTP on `MCP_HOST:MCP_PORT`;
- otherwise → stdio.

So Databricks Apps works with no code change: it injects a port, and the
server serves `/mcp` and `/health`.

## Databricks Apps

See [`databricks/README.md`](databricks/README.md). The kit syncs this
repository to the workspace and runs `databricks apps deploy`. Three rules
decide whether it works at all:

1. **The app name starts with `mcp-`.** Without the prefix the app deploys
   and is never offered as an MCP server in AI Playground or Unity Gateway.
2. **Grant the app's service principal, not yourself.** The app runs as its
   own identity. This is the most common cause of a server that works
   locally and returns nothing once deployed.
3. **Callers need `CAN USE` on the app and an OAuth token.** Personal access
   tokens are not supported.

## cloud.gov (bonus)

The repository root also carries a `manifest.yaml` for cloud.gov (Cloud
Foundry): `cf push` after `cf login` and `cf target`. Not a maintained kit,
but it works and costs nothing to keep.

## Containers

The root `Dockerfile` builds an image serving streamable HTTP on `:8080`.
Databricks Apps does not use it; any other container host can.

---

## Security reminder

A Databricks App sits behind workspace authentication, so an unauthenticated
probe returning 302 or 401 is the app working, not failing. For anything
beyond a hackathon, review your agency's ATO requirements before exposing an
endpoint. Never commit secrets: every `.env` here is git-ignored and holds
coordinates only.
