# Wiring `matrixctl` into Hermes

This walks through giving a Hermes agent the ability to drive Matrix through the
`matrixctl` CLI. The agent calls `matrixctl` as a shell tool; it reads its
credentials from the environment and returns JSON, so Hermes can parse results
directly.

## 1. Install the CLI

`matrixctl` ships as a standalone tool. Install it once into the environment that
actually talks to Matrix:

```bash
uv tool install git+https://github.com/muellerzr/matrix-ctl
```

This puts a `matrixctl` executable on `PATH` (typically `~/.local/bin`). Confirm:

```bash
matrixctl --version
```

To upgrade later: `uv tool upgrade matrixctl`.

### Where to install it: proxy container vs. Hermes env

**Install `matrixctl` wherever the Matrix API calls actually originate — not
necessarily where Hermes runs.**

- **No proxy** — Hermes reaches the homeserver directly. Install `matrixctl` in
  the Hermes environment and the agent calls it as a normal local shell command.
- **Matrix behind a proxy/sidecar container** — if Matrix traffic is routed
  through a proxy container (the homeserver is only reachable from inside it, or
  you front it with a sidecar), install `matrixctl` **inside that proxy
  container**, *not* in the Hermes env. Only the proxy container has the network
  path and credentials to reach the homeserver, so that's where the CLI has to
  live.

  In that setup the agent doesn't run `matrixctl` directly — it invokes the
  command **inside** the proxy container so it executes in the right network
  context, e.g.:

  ```bash
  docker exec matrix-proxy matrixctl whoami
  docker exec matrix-proxy matrixctl create-topic-room "Topic" --invite zach
  ```

  (substitute your proxy container's name/exec mechanism). Set the environment
  variables from step 2 **inside the proxy container**, since that's the process
  that reads them. Installing `matrixctl` in the Hermes env in this case won't
  work — those invocations can't reach Matrix.

## 2. Provide credentials via environment

`matrixctl` uses **access-token auth only** and reads everything from the
environment — nothing secret is ever passed on the command line, and tokens are
never printed.

Set these wherever Hermes launches the agent (systemd unit, container env,
`.env` loaded at startup, etc.):

```bash
# --- Required ---
export MATRIX_HOMESERVER="https://matrix.example.org"
export MATRIX_ACCESS_TOKEN="<bot access token>"

# --- Optional ---
export MATRIX_USER_ID="@hermes:example.org"      # auto-resolved if omitted
export MATRIX_DEFAULT_INVITEE="zach"             # invited to new rooms by default
export MATRIX_TIMEOUT_SECONDS="30"

# Friendly name -> MXID, so the agent can say `--invite zach` instead of a MXID.
export MATRIX_CONTACTS='{"zach":"@zach:example.org","hermes":"@hermes:example.org"}'
```

> Get the bot's access token from your homeserver (e.g. Element →
> Settings → Help & About → Access Token, or a login API call). Treat it like a
> password; keep it out of version control.

### Contacts file alternative

Instead of `MATRIX_CONTACTS`, you can drop a JSON file at
`~/.config/matrixctl/contacts.json` (override the path with
`MATRIX_CONTACTS_FILE`):

```json
{
  "zach": "@zach:example.org",
  "hermes": "@hermes:example.org"
}
```

Inline `MATRIX_CONTACTS` values override the file. Verify with `matrixctl contacts`.

## 3. Register the skill

Copy (or symlink) the skill directory into wherever Hermes loads agent skills so
the agent knows *when* and *how* to call the tool:

```
hermes/matrixctl/SKILL.md   ->   <hermes skills dir>/matrixctl/SKILL.md
```

The `SKILL.md` describes the commands, JSON output shapes, exit codes, and the
encryption caveat the agent must respect (see below).

## 4. Smoke test as the agent would

With the environment set, run the same commands the agent will issue:

```bash
# Identity — confirms the token works.
matrixctl whoami
# {"user_id":"@hermes:example.org","device_id":"...","is_guest":false}

# One-shot room creation.
matrixctl create-topic-room "Hermes Test" \
  --topic "Smoke test" \
  --invite zach \
  --bot-room-display-name "Hermes — Test" \
  --welcome-message "Hello from Hermes."
# {"room_id":"!abc:example.org","name":"Hermes Test","encrypted":false,
#  "invited":["@zach:example.org"],"bot_room_display_name":"Hermes — Test",
#  "welcome_event_id":"$..."}

# Clean up.
matrixctl leave "!abc:example.org" && matrixctl forget "!abc:example.org"
```

## 5. Handling results in Hermes

- **Success** → exit code `0`, one JSON object on stdout. Parse it and pull
  fields like `room_id` / `event_id`.
- **Failure** → non-zero exit, `error: <message>` on stderr. Branch on the code:
  - `1` — Matrix API / network error (transient → consider a retry).
  - `2` — config or unknown-contact error (fix env / contacts; don't retry blindly).
  - `3` — refused to plaintext-send into an **encrypted** room.

### The encryption rule

`matrixctl` can *enable* encryption and administer rooms but **cannot encrypt
message bodies** (it has no E2E crypto). So Hermes must:

- Not expect `send` to work on encrypted rooms — it returns exit `3` unless
  `--allow-plaintext` is passed (which sends UNENCRYPTED; avoid unless intended).
- After `create-topic-room --encrypted`, check the result: if
  `pending_welcome_message` is present, deliver that text into `room_id` over an
  E2E-capable channel (the mautrix adapter). If `welcome_event_id` is present,
  the message was already sent (room was unencrypted) and nothing more is needed.

This keeps `matrixctl` responsible for room/membership administration while
encrypted message delivery stays with the E2E-capable client.
