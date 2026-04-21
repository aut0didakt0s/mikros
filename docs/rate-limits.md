# Rate limits

megalos enforces token-bucket rate limits at the MCP tool-call boundary.
This page tells you what's limited, what a deny looks like on the wire,
and how to size polling to stay under the sustained budget.

## What's limited

Three axes, each a classic token bucket:

- **session** — per-session-id. Keyed by the capability token the client
  holds. Applies to every tool that takes a `session_id` argument.
- **ip** — per-source-IP. Applies on HTTP transports only; stdio
  deployments skip IP gating entirely (there is no IP to gate on).
- **ip_session_create** — per-source-IP, consulted only on
  `start_workflow` (the session-creating tool). Sits *before* the
  `ip` bucket on that call path so session-creation bursts trip first.

Each bucket holds up to `burst` tokens and refills at `rate` tokens per
second. A call costs one token. When the bucket is empty the call is
denied and the retry-after is the time until one token refills.

A single tool call may consult multiple axes; the first denial wins and
the others aren't charged.

## Default sizes

| Axis                | Rate (tokens/sec) | Burst (capacity) |
|---------------------|------------------:|-----------------:|
| `session`           | 2                 | 30               |
| `ip`                | 60                | 200              |
| `ip_session_create` | 1                 | 10               |

Rationale: `session` at 2/s sustained with a burst of 30 absorbs normal
interactive use plus short spikes without ever tripping. `ip` is sized
for a single client driving many sessions. `ip_session_create` throttles
the one operation that costs real state to allocate (session row + stack
bookkeeping + quota checks).

## Deny envelope

On a rate-limit denial the tool returns an error envelope with the same
shape as every other megalos error:

```json
{
  "status": "error",
  "code": "rate_limited",
  "error": "rate limit exceeded",
  "retry_after_ms": 850.0,
  "scope": "session"
}
```

Session-axis denials additionally carry `session_fingerprint` (a
sha256-derived 12-hex-char identifier — **not** the raw session_id).

Clients should **honor `retry_after_ms`**: wait at least that many
milliseconds before retrying the same call. `scope` tells you which
bucket tripped, which is usually the only diagnostic signal you need on
the client side.

The envelope deliberately omits bucket capacity, current token count,
and raw IP. Those would give an attacker probe-level telemetry.

## Safe poll intervals

The per-session sustained rate is 2 tokens/sec. Polling
`get_state(session_id)` at one call per second is well under that
budget, so steady polling never trips the session axis. A short burst
(dashboards, reconnect flurries) up to 30 rapid calls is also absorbed.

**Guidance:**

- `get_state` polling: `<= 2 calls/sec` sustained. 1/s is comfortable.
- Burst: up to 30 rapid calls will succeed; the 31st within the same
  second denies with a ~500ms `retry_after_ms`.
- If a client needs more than 2/s sustained on a single session,
  consolidate calls or split state into multiple sessions.

## Transport caveat

- **stdio**: no IP is available. Only the `session` axis is consulted.
  The `ip` and `ip_session_create` axes are inert on stdio.
- **streamable-http / sse / http**: all three axes are consulted
  as appropriate (see routing above).

Proxy-header handling (`X-Forwarded-For`) is deferred. If you deploy
megalos behind a reverse proxy today, the `ip` axis sees the proxy's
IP, not the client's — budget the `ip` burst accordingly or disable IP
gating until fronted-deployment support lands.

## Tunable env vars

All values are floats (rates / bursts) or non-negative integers (store
cap). Invalid values raise at process start, not silently.

| Variable                                | Default | Meaning                              |
|-----------------------------------------|--------:|--------------------------------------|
| `MEGALOS_RATELIMIT_SESSION_RATE`        | `2.0`   | session bucket refill rate (tok/s)   |
| `MEGALOS_RATELIMIT_SESSION_BURST`       | `30.0`  | session bucket capacity              |
| `MEGALOS_RATELIMIT_IP_RATE`             | `60.0`  | ip bucket refill rate (tok/s)        |
| `MEGALOS_RATELIMIT_IP_BURST`            | `200.0` | ip bucket capacity                   |
| `MEGALOS_RATELIMIT_IP_CREATE_RATE`      | `1.0`   | ip_session_create rate (tok/s)       |
| `MEGALOS_RATELIMIT_IP_CREATE_BURST`     | `10.0`  | ip_session_create burst              |
| `MEGALOS_RATELIMIT_IP_STORE_CAP`        | `10000` | max tracked IPs per axis (LRU cap)   |
| `MEGALOS_RATELIMIT_IP_IDLE_TTL_SEC`     | `600.0` | idle TTL for tracked IP buckets (s)  |

## Redeploy note

Buckets live in process memory and reset on redeploy. **This is
intentional, not a gap** — it parallels the split between SQLite-durable
state (sessions, workflows, artifacts) and runtime-transient state
(bucket balances, LRU stores, observability dedupe caches). A deploy
effectively grants every live session a fresh burst budget. Horizontal
scaling or shared rate limits across replicas need out-of-process state,
which would change the sync-consume atomicity story — revisit at that
point, not before.
