# matrixctl

A small non-interactive CLI for automating [Matrix](https://matrix.org) via the
Client-Server API. Designed to be simple enough for an agent to call as a tool:
it returns JSON on stdout, prints errors to stderr, and exits non-zero on
failure.

## Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv
uv pip install -e .
```

This installs the `matrixctl` console script into `.venv/bin/matrixctl`.
Activate the venv (`source .venv/bin/activate`) or call it directly.

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

Returns JSON including the `room_id`.

## Notes

- A Matrix *room* is the chat; a *Space* is only for organizing rooms. This
  tool works with rooms.
- "Close chat" means `leave`; optionally follow with `forget`.
