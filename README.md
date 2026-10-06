# MARVIN admin bot

maubot plugin `ch.unibas.dmi.marvin.adminbot`. The plan, with the design and its reasoning, is in [claude.md](claude.md). The server-side setup (everything outside this folder) is in [../docs/admin-bot-setup.md](../docs/admin-bot-setup.md).

Professors and TAs talk to `@adminbot` in a direct chat. `!help` lists what the sender may use:

| Area | Commands |
|---|---|
| Courses | `!course create / list / info / ta / archive / rollover`, `!room add / remove` |
| People | `!invite`, `!roster sync`, `!signup new / guest / list / revoke / renew / reset` |
| Groups | `!groups create / assign / post` |
| Templates | `!template export / import / list / delete` |
| Own bots | `!bot create / update / list / info / config / start / stop / delete` |
| Admins | `!admin prof / audit / bots` |
| Bulk changes | shown as a plan first, run with `!confirm <code>`, dropped with `!cancel` |

## Layout

```
maubot.yaml, base-config.yaml   plugin metadata and default instance config (both go into the .mbp)
adminbot/
  bot.py                        Plugin class: builds the services, self-check, registers handlers
  commands/base.py              CommandRouter (the only Matrix event handler) + @command + helpers
  commands/*.py                 one class per command group
  web/                          signup / reset pages (maubot webapp)
  identity, parsing, roster,    pure logic, unit-tested without a server
  templates (parse/dump)
  synapse_admin, matrix_ops,    talk to Synapse
  attachments, userbots
  signups, confirm, jobs,       services
  authz, audit
  db, migrations                SQL (portable SQLite + Postgres)
  strings/en.py                 every user-facing text
tests/                          pytest (uses maubot's own test fixtures)
dev/                            local Synapse + Postgres (+ optional second maubot) for the MacBook
```

## Testing: four levels, fastest first

Uploading to a running instance is the *last* step. Most work should never need a server.

| Level | What | Runs where | Turnaround |
|---|---|---|---|
| 1. Unit tests | pure modules and `Store` against SQLite | MacBook, `pytest` | seconds |
| 2. Command tests | routing, permissions, replies, invite/confirm/job flow, signup pages; maubot's fake client records what the bot sends, Synapse calls are mocked | MacBook, `pytest` | seconds |
| 3. Local dev stack | the real bot against a real (throwaway) Synapse and, optionally, a second maubot for professor bots; plugin runs **from source**, so no build or upload | MacBook, Docker | restart ≈ 2 s |
| 4. Test node | the built `.mbp` in the real stack: E2EE, nginx, Postgres, backups | test node | upload |

Production gets exactly the `.mbp` that passed level 4.

### Setup (once)

Python **3.12** (`.python-version` pins it for uv). maubot's standalone mode (level 3) does not run on 3.14.

```bash
cd server/admin-bot
uv sync --group dev            # or: python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

### Levels 1 and 2

```bash
.venv/bin/pytest
```

- `tests/conftest.py` points maubot's pytest fixtures at this plugin. The `bot` fixture mocks all server calls, and `chat.send("!help", sender=...)` returns the bot's replies.
- The test DB is SQLite, so the SQL in `db.py` / `migrations.py` must work on SQLite and Postgres.
- `mautrix.crypto` needs python-olm, which isn't in the dev venv. Import it inside functions only (see `attachments.py`).

### Level 3: local dev stack

```bash
dev/setup.sh            # or dev/setup.sh --bots for the second maubot (professor bots)
.venv/bin/python -m maubot.standalone -m maubot.yaml -c dev/standalone.yaml -b dev/standalone.example.yaml
```

`setup.sh` deletes and recreates everything:
- a Synapse with server name `localhost` on `http://localhost:8008`, without rate limits
- Postgres for the plugin
- users `adminbot` (server admin), `admin`, `prof`, `ta` and `student`, all with password `devpass1234567`
- `dev/standalone.yaml` (gitignored) with the bot's token

Using it:
- Log in with Element Desktop at `http://localhost:8008` and DM `@adminbot:localhost`. `admin` is a bot admin, `prof` a professor.
- The signup pages are at `http://127.0.0.1:8090/_matrix/maubot/plugin/adminbot/...` (not 8080: Ketesa uses that port).
- After code changes, press Ctrl-C and start the bot again.
- `docker compose --profile bots down` (in `dev/`) stops the stack.

Limits:
- Local DMs are unencrypted (no device ID, so no E2EE). Encrypted uploads and verification are tested on level 4.
- With `--bots` there is no audit room locally, so bot requests are approved with `!admin bots pending` / `approve`.
- The stack uses named Docker volumes only: Docker Desktop often may not bind-mount from `~/Documents`.

### Level 4: test node, then production

```bash
# bump `version` in maubot.yaml first
find adminbot -name __pycache__ -prune -exec rm -rf {} +   # mbc packs __pycache__ otherwise
.venv/bin/mbc build                                       # -> ch.unibas.dmi.marvin.adminbot-v<version>.mbp (gitignored)
```

Upload it in the maubot UI on the test node (`https://localhost:8443/_matrix/maubot/` through the SSH tunnel). The instance reloads with the new version. After the checklist passes, upload the same file to production.

Checklist on the test node, which levels 1–3 can't cover:
- [ ] The DM with the bot is encrypted, and the bot answers.
- [ ] `!invite` with an attached `.txt` file.
- [ ] `!template import` with a replied-to `.yaml`.
- [ ] The CSV arrives as an encrypted file.
- [ ] The signup page works through `https://matrix.dmi.unibas.ch/_matrix/maubot/plugin/adminbot/signup` (production) or through the tunnel (test node).
- [ ] After a restart of maubot, an interrupted job continues.
- [ ] The audit room receives the startup checks.
