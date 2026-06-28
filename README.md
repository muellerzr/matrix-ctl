# matrixctl

```bash
uv tool install git+https://github.com/muellerzr/matrix-ctl   # installs `matrixctl`
export MATRIX_HOMESERVER="https://matrix.example.org" MATRIX_ACCESS_TOKEN="<token>"
matrixctl whoami                                  # JSON on stdout, exit 0 = ok
matrixctl create-topic-room "Topic" --invite zach --topic "..." --encrypted
matrixctl send "!room:id" "message"               # see Commands for the full set
```

A small non-interactive CLI for automating [Matrix](https://matrix.org) via the
Client-Server API. Designed to be simple enough for an agent to call as a tool:
it returns JSON on stdout, prints errors to stderr, and exits non-zero on
failure.

## Install

Requires [uv](https://docs.astral.sh/uv/) (Python 3.10+).

Install the `matrixctl` command globally, straight from the repo:

```bash
uv tool install git+https://github.com/muellerzr/matrix-ctl
```

This puts a `matrixctl` executable on your `PATH` (usually `~/.local/bin`).
Upgrade later with `uv tool upgrade matrixctl`.

### Local / development install

```bash
uv venv
uv pip install -e .
```

This installs the `matrixctl` console script into `.venv/bin/matrixctl`.
Activate the venv (`source .venv/bin/activate`) or call it directly.

## Using it with Hermes (or any agent)

`matrixctl` is built to be called as an agent tool. Two ready-made pieces:

- [`hermes/matrixctl/SKILL.md`](hermes/matrixctl/SKILL.md) — a drop-in agent
  skill describing when and how to call `matrixctl` (commands, JSON shapes, exit
  codes, the encryption rule).
- [`examples/hermes-config.md`](examples/hermes-config.md) — step-by-step setup:
  install, environment/credentials, registering the skill, and handling results.

## Configuration

Configuration is via environment variables (access-token auth only).

Required:

- `MATRIX_HOMESERVER` — e.g. `https://matrix.example.org`
- `MATRIX_ACCESS_TOKEN` — an access token for the acting user

Optional:

- `MATRIX_USER_ID` — your full user id (e.g. `@bot:example.org`). If unset,
  it is resolved automatically via `whoami` when needed.
- `MATRIX_DEFAULT_INVITEE` — a user id invited by default to newly created
  rooms when no `--invite` is given.
- `MATRIX_TIMEOUT_SECONDS` — request timeout in seconds (default `30`); applies
  to REST calls and to Hermes IPC waits.
- `MATRIXCTL_TRANSPORT` — `auto` (default), `rest`, or `hermes`. See
  [Transport modes](#transport-modes).
- `MATRIXCTL_HERMES_SOCKET` — path to the Hermes IPC gateway socket
  (default `/run/matrixctl/hermes.sock`).

Tokens are never printed.

## Commands

```bash
matrixctl whoami
matrixctl contacts
matrixctl create-room "Room Name" [--encrypted] [--topic "..."] [--invite "@user:server"]
matrixctl invite "!room:id" "@user:server"
matrixctl send "!room:id" "message"
matrixctl leave "!room:id"
matrixctl forget "!room:id"
matrixctl set-room-name "!room:id" "Name"
matrixctl set-room-topic "!room:id" "Topic"
matrixctl set-display-name "Display Name"
matrixctl set-room-display-name "!room:id" "Display Name"
```

`--invite` is repeatable on `create-room` and `create-topic-room`.

### Contacts (friendly names)

So agents don't have to juggle full MXIDs, any place that takes a user
(`invite`, `--invite`, `MATRIX_DEFAULT_INVITEE`) accepts either a full MXID
(`@zach:server`) or a short contact name (`zach`). A value that already looks
like an MXID is passed through untouched; anything else is resolved against
contacts, and an unknown name fails with a clear error listing known contacts.

Contacts are a name → MXID map, loaded from (later overrides earlier):

1. `~/.config/matrixctl/contacts.json` (override path with `MATRIX_CONTACTS_FILE`):

   ```json
   {
     "zach": "@zach:example.org",
     "hermes": "@hermes:example.org"
   }
   ```

2. An inline `MATRIX_CONTACTS` env var holding the same JSON object.

```bash
matrixctl contacts                       # list resolved contacts
matrixctl invite "!room:id" zach         # resolves to @zach:server
matrixctl create-room "Room" --invite zach --invite hermes
```

### High-level helper

Create a private room and set it up in a single call:

```bash
matrixctl create-topic-room "Topic Name" \
  --topic "Topic description" \
  --invite "@user:example.org" \
  --encrypted \
  --bot-room-display-name "Hermes — Topic Name" \
  --welcome-message "Welcome message"
```

Returns JSON including the `room_id`. Behaviour with `--encrypted` depends on the
transport (see [Transport modes](#transport-modes)): under **Hermes** the welcome
message is sent encrypted and you get a `welcome_event_id`; under **REST** it
cannot be encrypted, so it is returned as `pending_welcome_message` for an
E2E-capable client to send instead.

## Transport modes

matrixctl can reach Matrix two ways. Pick one with `--transport` (or
`MATRIXCTL_TRANSPORT`); the JSON output and exit codes are identical either way.

| Mode | What it does | E2E encryption |
| --- | --- | --- |
| `rest` | Direct Client-Server REST with access-token auth (the original behaviour). | **No** — cannot encrypt message bodies. |
| `hermes` | Forwards every operation to a running Hermes Matrix adapter over a Unix socket; messages go out through the adapter's live mautrix/Olm client. Fails clearly if the gateway isn't reachable. | **Yes** — handled by Hermes. |
| `auto` *(default)* | Uses Hermes when its socket is live; otherwise falls back to REST. | Yes when the gateway is up, else No. |

```bash
matrixctl --transport rest   send "!room:id" "plain"     # direct REST
matrixctl --transport hermes send "!room:id" "secret"    # via Hermes (E2E)
matrixctl send "!room:id" "hi"                            # auto
```

**REST mode cannot perform E2E encryption.** It can *enable* encryption on a room
(`--encrypted` writes the `m.room.encryption` state event) and do all room
administration, but it cannot encrypt message content. So under REST:

- `send` into an encrypted room is **refused** (exit code `3`) unless you pass
  `--allow-plaintext`, which writes the message UNENCRYPTED (clients flag it).
- `create-topic-room --encrypted --welcome-message …` returns the text as
  `pending_welcome_message` instead of sending it.

**Hermes mode delegates to the already-running mautrix/Olm client**, so it never
hits these limits: `send` into an encrypted room just works (the gateway
encrypts), and `--allow-plaintext` is unnecessary and ignored.

## Hermes E2E integration

The architecture is a one-way forward from matrixctl to a long-running gateway:

```
matrixctl  →  Unix socket  →  Hermes Matrix adapter  →  live mautrix client
              (JSON lines)     (holds Olm/Megolm state)  →  homeserver
```

matrixctl speaks a small versioned JSON-lines protocol over the socket (one
request/response per invocation, correlated by a UUID). Each CLI command maps to
one gateway action — `whoami`, `send`, `create_room`, `create_topic_room`,
`invite`, `leave`, `forget`, `set_room_name`, `set_room_topic`,
`set_display_name`, `set_room_display_name`. `create_topic_room` is a single
atomic action on the gateway side (create + invite + enable encryption + send the
welcome encrypted), so a half-built room can't result from one call.

To use it, point matrixctl at the gateway socket and select the transport:

```bash
export MATRIXCTL_TRANSPORT=hermes
export MATRIXCTL_HERMES_SOCKET=/run/matrixctl/hermes.sock
matrixctl send "!room:id" "encrypted message"
```

> The Hermes-side IPC gateway (the socket server and adapter wiring) is a
> separate component and is **not** part of this package. matrixctl only
> implements the client half of the protocol.

## Docker proxy usage

When Matrix is reached through a proxy/sidecar container, install matrixctl and
set the transport env vars **inside that container** (it's where the network path
and the gateway socket live), then invoke it from the host:

```bash
# inside the proxy image:
#   MATRIXCTL_TRANSPORT=hermes
#   MATRIXCTL_HERMES_SOCKET=/run/matrixctl/hermes.sock

docker exec hermes-matrix-proxy matrixctl --transport hermes \
  send "!room:server" "encrypted test"
```

See [`examples/hermes-config.md`](examples/hermes-config.md) for the full proxy
vs. host install discussion.

## Security model

- **The crypto database is never shared between processes.** matrixctl has no
  crypto and never opens, reads, or writes the adapter's Olm/Megolm store — only
  the Hermes gateway process touches it. matrixctl just sends JSON over a socket.
- **No silent plaintext into encrypted rooms.** REST refuses (exit `3`) unless
  you explicitly pass `--allow-plaintext`; Hermes encrypts. There is no path that
  quietly leaks plaintext into an encrypted room.
- **Bounded, validated IPC.** Responses are size-capped and time-bounded, the
  protocol version is checked, and each reply must match the request's UUID
  before it's trusted.
- **No symlink following / stale sockets.** The socket path is `lstat`-checked and
  must be a real socket (a symlink there is rejected); a stale socket (file
  present, nothing listening) is treated as unavailable — `auto` falls back to
  REST, `hermes` fails clearly.
- **Tokens are never printed** by matrixctl in any mode.

## Troubleshooting

- **`transport 'hermes' was requested but the IPC socket … is not available`**
  (exit `2`) — the gateway isn't running or `MATRIXCTL_HERMES_SOCKET` points at
  the wrong path. Start the Hermes adapter with its IPC server enabled, or use
  `--transport rest` / `auto`.
- **`auto` used REST when you expected Hermes** — the socket was missing, stale,
  or a symlink. Check it exists and is a live socket
  (`test -S "$MATRIXCTL_HERMES_SOCKET"`), and that nothing replaced it with a
  link.
- **`send` returns exit `3` ("refused … encrypted")** — you're on REST. Switch to
  `--transport hermes` to send encrypted, or pass `--allow-plaintext` to send
  unencrypted on purpose.
- **`Hermes gateway returned invalid JSON` / `… exceeded size limit` /
  `response id did not match`** (exit `1`) — a protocol-level problem with the
  gateway; check that its matrixctl IPC version matches this client.
- **Permission denied connecting to the socket** — the socket is `0600` and owned
  by the gateway user; run matrixctl as that user (e.g. inside the proxy
  container) rather than from a different account.

## Exit codes

- `0` — success
- `1` — Matrix API / network error
- `2` — configuration or contact-resolution error (bad/missing env, unknown name)
- `3` — refused to plaintext-send into an encrypted room (pass `--allow-plaintext`)

## Output reference (for agents)

On success each command prints a single JSON object to **stdout** and exits `0`.
On failure it prints `error: <message>` to **stderr** and exits non-zero (see
Exit codes). Tokens are never printed. Exact shapes per command:

| Command | stdout JSON |
| --- | --- |
| `whoami` | `{"user_id": "@bot:server", "device_id": "ABC", "is_guest": false}` |
| `contacts` | `{"contacts": {"zach": "@zach:server", ...}}` |
| `create-room` | `{"room_id": "!id:server"}` |
| `invite` | `{"room_id": "!id:server", "invited": "@zach:server"}` (`invited` is the resolved MXID) |
| `send` | `{"room_id": "!id:server", "event_id": "$evt"}` |
| `leave` | `{"room_id": "!id:server", "left": true}` |
| `forget` | `{"room_id": "!id:server", "forgotten": true}` |
| `set-room-name` | `{"room_id": "!id:server", "event_id": "$evt"}` |
| `set-room-topic` | `{"room_id": "!id:server", "event_id": "$evt"}` |
| `set-display-name` | `{"user_id": "@bot:server", "displayname": "Name"}` |
| `set-room-display-name` | `{"room_id": "!id:server", "user_id": "@bot:server", "displayname": "Name", "event_id": "$evt"}` |

### `create-topic-room` output

```json
{
  "room_id": "!id:server",
  "name": "Topic Name",
  "encrypted": true,
  "invited": ["@zach:server"],
  "bot_room_display_name": "Hermes — Topic Name",
  "pending_welcome_message": "Welcome message"
}
```

- `room_id`, `name`, `encrypted`, `invited` are always present (`invited` holds
  resolved MXIDs, possibly `[]`).
- `bot_room_display_name` appears only if `--bot-room-display-name` was given.
- The welcome message is reported under exactly one of two keys, and **only**
  if `--welcome-message` was given:
  - **`welcome_event_id`** — the message was sent; this is its event id. Happens
    for unencrypted rooms on any transport, and for **encrypted** rooms on the
    Hermes transport (sent encrypted by the gateway).
  - **`pending_welcome_message`** — the message was **not** sent because the REST
    transport can't encrypt and the room is encrypted. The caller (Hermes/
    mautrix) must send this text encrypted. Only appears under `--transport rest`.

  So Hermes should: check for `pending_welcome_message`; if present, send that
  text into `room_id` via mautrix. If `welcome_event_id` is present instead,
  nothing more to do.

## Notes

- A Matrix *room* is the chat; a *Space* is only for organizing rooms. This
  tool works with rooms.
- "Close chat" means `leave`; optionally follow with `forget`.
