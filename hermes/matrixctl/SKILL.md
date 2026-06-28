---
name: matrixctl
description: >-
  Automate Matrix chat from the command line: create rooms (optionally
  encrypted), invite people, send messages, rename/re-topic rooms, set display
  names, and close (leave/forget) chats. Use this whenever the user wants to
  start a new chat or topic room, message someone on Matrix, manage room
  membership, or tidy up rooms. When creating a room, ALWAYS invite the
  requester (the person who asked for the room) so they end up in it — unless
  they explicitly say to leave them out. Every command returns one JSON object
  on stdout and exits 0 on success.
---

# matrixctl

`matrixctl` is a small non-interactive CLI for driving [Matrix](https://matrix.org)
over the Client-Server API. It is built to be called as a tool: it prints a
single JSON object to **stdout** on success, prints `error: <message>` to
**stderr** on failure, and uses **exit codes** to signal the failure class. It
never prints access tokens.

## When to use this skill

Reach for `matrixctl` when the user wants to:

- **Start a chat / topic room** — "open a room for project X", "make me an
  encrypted room with Zach". Prefer `create-topic-room` for the one-shot path
  (create + encrypt + invite + name the bot + welcome message).

  **Always include the requester.** When someone asks you to make a room, invite
  *them* too (pass their MXID/contact via `--invite`) so the room isn't created
  with only the bot in it — they almost always want to be in the room they asked
  for. Only skip this if they explicitly say to leave themselves out. If
  `MATRIX_DEFAULT_INVITEE` is set to the requester, that already covers it when
  no other `--invite` is given; when you pass `--invite` for someone else,
  add the requester explicitly (`--invite` is repeatable) since an explicit
  `--invite` list opts out of the default invitee.
- **Send a message** to a known room id.
- **Manage a room** — invite a user, rename, change the topic, set the bot's
  per-room display name.
- **Close a chat** — leave a room, and optionally forget it.
- **Check identity** — confirm which Matrix user the token belongs to (`whoami`).

A Matrix **room** is the chat. A **Space** is only for organizing rooms — this
tool works with rooms, not spaces. "Close a chat" means `leave` (then optionally
`forget`).

## Setup (environment)

The token is read from the environment; you never pass it on the command line.

Required:

- `MATRIX_HOMESERVER` — e.g. `https://matrix.example.org` (a bare host defaults
  to `https://`).
- `MATRIX_ACCESS_TOKEN` — access token for the acting user.

Optional:

- `MATRIX_USER_ID` — full MXID (e.g. `@hermes:example.org`); auto-resolved via
  `whoami` if unset.
- `MATRIX_DEFAULT_INVITEE` — user invited to new rooms when no `--invite` given.
- `MATRIX_TIMEOUT_SECONDS` — HTTP timeout (default `30`).
- `MATRIX_CONTACTS` / `MATRIX_CONTACTS_FILE` — friendly-name → MXID map (see
  Contacts).

If a required variable is missing, commands fail fast with exit code `2`.

**Proxy container:** if Matrix is reached through a proxy/sidecar container, the
`matrixctl` binary and these env vars live **inside that container**, not in the
Hermes env. In that case don't run `matrixctl` directly — invoke it inside the
container so it runs in the right network context (e.g.
`docker exec matrix-proxy matrixctl whoami`). See `examples/hermes-config.md`.

## Contacts (friendly names)

Anywhere a user is expected (`invite`, `--invite`, `MATRIX_DEFAULT_INVITEE`) you
may pass **either** a full MXID (`@zach:server`) **or** a short contact name
(`zach`). Full MXIDs pass through untouched; bare names are resolved against the
contacts map, and an unknown name fails with exit code `2` and an error listing
the known contacts. Run `matrixctl contacts` to see what's configured.

## Commands

```bash
matrixctl whoami
matrixctl contacts
matrixctl create-room "Room Name" [--encrypted] [--topic "..."] [--invite zach]
matrixctl invite "!room:id" "@user:server"
matrixctl send "!room:id" "message" [--allow-plaintext]
matrixctl leave "!room:id"
matrixctl forget "!room:id"
matrixctl set-room-name "!room:id" "Name"
matrixctl set-room-topic "!room:id" "Topic"
matrixctl set-display-name "Display Name"
matrixctl set-room-display-name "!room:id" "Display Name"
```

`--invite` is repeatable on `create-room` and `create-topic-room`.

### One-shot: create-topic-room

The preferred way to open a fully set-up private room:

```bash
matrixctl create-topic-room "Topic Name" \
  --topic "Topic description" \
  --invite zach \
  --encrypted \
  --bot-room-display-name "Hermes — Topic Name" \
  --welcome-message "Welcome message"
```

It creates a private room, optionally enables encryption, invites the requested
users (plus `MATRIX_DEFAULT_INVITEE` if no `--invite`), optionally sets the bot's
per-room display name, optionally sends/queues a welcome message, and returns
JSON including `room_id`.

## Encryption — important for agents

`matrixctl` is token-only REST and has **no end-to-end crypto**. It can *enable*
encryption on a room and do all room administration, but it **cannot encrypt
message content**. So:

- `send` into an **encrypted** room is **refused with exit code 3** unless you
  pass `--allow-plaintext` (which writes the message UNENCRYPTED — clients will
  flag it). Only do that if the user explicitly wants an unencrypted message.
- `create-topic-room --encrypted --welcome-message "..."` does **not** send the
  welcome text. It returns it under **`pending_welcome_message`**, which you (the
  agent) must deliver through an E2E-capable path (e.g. the mautrix adapter).
  When the room is unencrypted, it sends the text itself and returns
  **`welcome_event_id`** instead — nothing more to do.

**Rule of thumb:** after `create-topic-room`, check the result JSON. If
`pending_welcome_message` is present, send that text into `room_id` over your
encrypted channel. If `welcome_event_id` is present, you're done.

## Exit codes

- `0` — success (JSON on stdout)
- `1` — Matrix API / network error
- `2` — configuration or contact-resolution error (missing env, unknown name)
- `3` — refused to plaintext-send into an encrypted room

Always check the exit code, not just the presence of output. On non-zero, read
the `error:` line on stderr to decide whether to retry, fix config, or surface
the problem to the user.

## Output shapes (success)

| Command | stdout JSON |
| --- | --- |
| `whoami` | `{"user_id": "@bot:server", "device_id": "ABC", "is_guest": false}` |
| `contacts` | `{"contacts": {"zach": "@zach:server", ...}}` |
| `create-room` | `{"room_id": "!id:server"}` |
| `invite` | `{"room_id": "!id:server", "invited": "@zach:server"}` |
| `send` | `{"room_id": "!id:server", "event_id": "$evt"}` |
| `leave` | `{"room_id": "!id:server", "left": true}` |
| `forget` | `{"room_id": "!id:server", "forgotten": true}` |
| `set-room-name` | `{"room_id": "!id:server", "event_id": "$evt"}` |
| `set-room-topic` | `{"room_id": "!id:server", "event_id": "$evt"}` |
| `set-display-name` | `{"user_id": "@bot:server", "displayname": "Name"}` |
| `set-room-display-name` | `{"room_id": "...", "user_id": "...", "displayname": "Name", "event_id": "$evt"}` |

`create-topic-room` returns `room_id`, `name`, `encrypted`, `invited` (always),
plus `bot_room_display_name` and one of `pending_welcome_message` /
`welcome_event_id` when those options were given.

## Worked examples

Open an encrypted topic room with Zach and queue a welcome:

```bash
matrixctl create-topic-room "Quarterly Planning" \
  --topic "Q3 planning + notes" \
  --invite zach \
  --encrypted \
  --bot-room-display-name "Hermes — Planning" \
  --welcome-message "Hi! I'll keep notes for our Q3 planning here."
# -> {"room_id":"!abc:server","encrypted":true,"invited":["@zach:server"],
#     "pending_welcome_message":"Hi! ..."}  -> deliver that text via mautrix
```

Send into an unencrypted room:

```bash
matrixctl send "!abc:server" "Build finished — all green."
```

Close a chat:

```bash
matrixctl leave "!abc:server" && matrixctl forget "!abc:server"
```
