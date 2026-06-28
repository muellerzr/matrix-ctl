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
- `MATRIX_TIMEOUT_SECONDS` — HTTP timeout in seconds (default `30`).

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

Returns JSON including the `room_id`. When `--encrypted` is set, the welcome
message is **not** sent by matrixctl (see Encryption); it is returned as
`pending_welcome_message` for an E2E-capable client to send instead.

## Encryption

matrixctl uses token-only REST and has **no end-to-end crypto**. It can *enable*
encryption on a room (`--encrypted` writes the `m.room.encryption` state event)
and do all room administration, but it **cannot encrypt message content**.
Encrypted messaging is expected to go through an E2E-capable client such as
Hermes's mautrix adapter. To stay safe matrixctl never shares or touches that
adapter's crypto store.

As a result:

- `send` into an encrypted room is **refused** (exit code `3`) unless you pass
  `--allow-plaintext`, which writes the message UNENCRYPTED (clients will flag
  it). Use this only when you really mean to.
- `create-topic-room --encrypted` with a `--welcome-message` returns the text as
  `pending_welcome_message` instead of sending it, so the caller can send it
  encrypted.

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
  - **`pending_welcome_message`** — room is encrypted; matrixctl did **not**
    send it. The caller (Hermes/mautrix) must send this text encrypted.
  - **`welcome_event_id`** — room is unencrypted; matrixctl already sent it,
    this is the resulting event id.

  So Hermes should: check for `pending_welcome_message`; if present, send that
  text into `room_id` via mautrix. If `welcome_event_id` is present instead,
  nothing more to do.

## Notes

- A Matrix *room* is the chat; a *Space* is only for organizing rooms. This
  tool works with rooms.
- "Close chat" means `leave`; optionally follow with `forget`.
