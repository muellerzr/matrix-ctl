# Matrix CLI Tool

Build `matrixctl`, a small non-interactive CLI for automating Matrix via the Client-Server API. It should be simple enough for an agent like Hermes to call as a tool.

## Env vars

Required:

- `MATRIX_HOMESERVER`
- `MATRIX_ACCESS_TOKEN`

Optional:

- `MATRIX_USER_ID`
- `MATRIX_DEFAULT_INVITEE`
- `MATRIX_TIMEOUT_SECONDS`

Use access-token auth only. Do not require password auth for normal use.

## Output

- Return JSON by default.
- Print errors to stderr.
- Exit `0` on success, non-zero on failure.
- Never print tokens.

## Commands

Implement these first:

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

## High-level helper

Implement this convenience command:

```bash
matrixctl create-topic-room "Topic Name" \
  --topic "Topic description" \
  --invite "@user:example.org" \
  --encrypted \
  --bot-room-display-name "Hermes — Topic Name" \
  --welcome-message "Welcome message"
```

It should create a private room, optionally enable encryption, invite the requested users, optionally set the bot’s per-room display name, optionally send a welcome message, and return JSON with the `room_id`.

## Notes

- A Matrix “room” is the chat. A “Space” is only for organizing rooms.
- “Close chat” should mean leave the room, and optionally forget it.
- Keep commands direct, predictable, and agent-friendly.
