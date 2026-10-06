# MARVIN Admin Bot: Implementation Plan (v5)

Status: **implemented** (phases 0–8), tested locally, not yet deployed. v1 2026-10-05, v2 2026-10-05 (after review), v3 2026-10-06 (aligned with the repo), v4 2026-10-06 (no IDM), v5 2026-10-06 (implemented).
Scope: a new maubot plugin in `server/admin-bot/` (plugin ID `ch.unibas.dmi.marvin.adminbot`). See `README.md` for development and testing.
Infrastructure changes outside the plugin are listed in §16 (I1–I10); how to apply them is in `server/docs/admin-bot-setup.md`.

**Implementation notes (v5): where the code differs from the plan below**
- **Own command router instead of maubot's `@command` decorators.** maubot answers usage help to anyone, in any room, before a handler runs. The bot sits in every course room, so `commands/base.py` routes all messages itself: unknown senders get silence, known users outside a DM get "please DM me".
- **Email localparts with characters outside `[a-z0-9._=-]` are rejected**, not stripped (§4), so `max+1@` and `max1@` can never become the same account.
- **A person may have several open signup links** (e.g. from two professors). The first one used creates the account, applies *all* pending invites and invalidates the others. `!signup renew` replaces them all with one.
- `!signup <email>` is `!signup new <email> [course]`.
- **The bot tags every room it creates** with a state event `ch.unibas.dmi.marvin.room` (`kind` room/group/space, `members` all/staff, group number). Templates, groups, archive and the DM check rely on it. Rooms with that tag are never treated as DMs, even with two members.
- **Rollover** renames the old course to `<code>-<old semester>` (e.g. `CS101-HS26`); the new semester keeps the plain code. Archiving stays a separate step.
- **Without an audit room the bot still runs.** The `audit_room` check is a warning; entries go to the DB table. Only a failed `admin` check blocks commands, and `userbots` blocks `!bot` / `!admin bots`.
- **Personal data is scrubbed from jobs.** Invite jobs drop the email list once the links are out; `!groups post` jobs drop the messages after sending.
- **Professor bots:** plugins whose pip dependencies aren't installed in the (shared, pinned) maubot image are rejected up front. Admins' own uploads skip approval. A deleted bot's account is reactivated when its name is used again.
- **Notifications** (bot approved/rejected, password reset) go to the user's most recent DM with the bot. The router records every DM a command arrives in.

**Verified** (2026-10-06):
- 87 pytest tests.
- Against a real local Synapse 1.157 with the plugin from source (`dev/setup.sh --bots`):
  - course create (rooms, join rules, power levels), TA scoping, refusal outside DMs
  - invite with CSV, the signup page (CSRF nonce, single use, force-join, threepid)
  - groups create/assign, template export, roster `--remove`, rollover + archive
  - password reset via page, audit log
  - professor bot: request → approve → provisioned in a second maubot → answers `!ping` → stop → delete → re-create
- **Not verified yet:** E2EE (encrypted DMs and files) and the real nginx route. They need the test node; see the checklist in `README.md`.

**Changes since v3**
- **Decision:** the project finishes without IDM/edu-ID integration. Bot-issued signup links are the *permanent* way accounts are created, not a stopgap.
- Without IDM, the username taken from the university email is the server's only link between an account and a real person. That is the reason to keep the custom signup page instead of Synapse registration tokens (§4).
- Password resets now go through the same signup page, because there is no SMTP and no IDM to reset passwords (§4).
- Guest accounts for people without a unibas address (§4).
- Open token registration gets switched off once Phase 3 is live; until then Ketesa tokens remain the stopgap (I8).
- Offboarding is noted as a known gap (§1, §15).

**Changes since v2** (from comparing the plan with the repo at `235833c`)
- Paths are now `server/admin-bot/`. The "hello-world plugin" and `docs/infrastructure-notes.md` from v2 don't exist. Phase 0 starts from nothing.
- Plugin ID changed to reverse-DNS (`ch.unibas.dmi.marvin.adminbot`). This is free to change because nothing is deployed yet.
- The F4/F7 references pointed to a file that doesn't exist. They're replaced by the concrete infrastructure list in §16.
- The plugin DB isn't Postgres today: `plugin_databases.postgres` is `null` in `server/maubot/config.yaml.template`, so asyncpg plugins get SQLite (I2).
- Bot state isn't backed up: `backup.sh` only dumps the Synapse DB (I5).
- The signup page isn't publicly reachable. Host nginx sends all of `/_matrix` to Synapse, and maubot only listens on localhost (I1).
- Signup links can't use maubot's `public_url`, because it is `https://localhost:8443` (§4).
- Synapse already has open token registration (`enable_registration` + `registration_requires_token`). That path bypasses the email→username mapping (I8).
- The maubot image is `:latest`, so the "confirmed in maubot 0.6.0 / mautrix 0.21.1" API notes need a pinned tag (I4).
- `crypto_db_pickle_key` is the public default `mau.crypto`. It has to change *before* the bot client is created (I3).
- Added: how a DM is detected, the client IP behind two proxies, threepids and data protection, and `.gitignore` traps.
- Startup check simplified: the bot sets its own rate-limit override instead of only checking it.

## 0. Environments and working rules

| Where | Purpose |
|---|---|
| MacBook | Write code, run pytest, build the `.mbp` (`mbc build`) |
| Test node behind the VPN | No domain; run and test everything here |
| ITS VM `dmi-matrix.dmi.unibas.ch` | Production: `matrix.dmi.unibas.ch` with a Let's Encrypt cert (ALIS); repo is cloned into `/MARVIN/matrix-unibas` |

- The repo only documents the ITS VM (Journal 02.10). The test node is set up the same way: `create_env.sh`, `setup.sh`, the same compose file.
- Each environment has its own `.env` and its own rendered configs. The plugin hardcodes no server names, URLs or room IDs. Everything comes from the instance config: defaults in `base-config.yaml`, edited per environment in the maubot UI (`https://localhost:8443/_matrix/maubot/` via SSH tunnel, see `server/docs/maubot.md`).
- Release flow: bump the version in `maubot.yaml` → `mbc build` → upload to the test node (maubot UI or `mbc upload`) → run the checklist → upload the same `.mbp` to production. `*.mbp` is gitignored, so builds stay out of the repo.
- Allowed changes for this work: files in `server/admin-bot/` plus `.md` files. Compose, nginx, the maubot config template, `.env.example`, `create_env.sh`, `setup.sh` and `backup.sh` are only *documented* (§16) and changed in a separate, reviewed infra PR. §14 shows which phase needs which item.
- **`.gitignore` traps for the plugin directory:** `*.sql`, `*.db`, `*.sqlite`, `*.log`, `*.tar`, `*.secret` and any `data/` directory are ignored repo-wide. Migrations therefore live in Python (mautrix `UpgradeTable`), not `.sql` files, and no folder may be called `data/`.

## 1. Scope

**Core**
- Bulk invite by email. The username comes from the email address.
- Create course spaces and the rooms inside them.
- Export a layout as a template and reuse it.
- Only allowlisted people can use the commands.
- Self-service signup links, with no access to the admin UI.

**Extras**
- Roles and course scoping.
- Confirm and dry-run for bulk or destructive actions.
- Audit log.
- Roster sync.
- Exercise groups, including per-group posts. This answers the "Punkte in Räume von Gruppen posten" feedback in `Project-Description.md`.
- Semester archive and rollover.
- **Professor-owned bots**: upload an `.mbp` file and a bot is provisioned automatically. This answers the open question from Journal 30.08.

**Not included**
- MAS (stretch goal).
- SMTP or any automated email.
- Announcement broadcast.
- User lookup and deactivation. Ketesa run locally covers this (`server/docs/admin_ui.md`).
- Automatic offboarding of people who leave the university. Without IDM, nothing tells the server. Deactivation stays manual in Ketesa for now (§15).
- A web UI for professors other than the signup page.

**Auth assumption**
- No IDM/SSO, now or later. Every account (except bots and the first admins) is created through a bot-issued signup link.
- The server is therefore its own identity provider. Usernames are derived from the university email (§4), so admins and roster sync can tell who an account belongs to.
- If IDM ever does come, the same username rules would match an OIDC `localpart_template` with `allow_existing_users: true`. Nothing in the plan depends on that.

## 2. Architecture

```
Professor / TA ──DM (E2EE)──► @adminbot (in matrix-maubot) ─┬─ http://synapse:8008  Admin API + Client-Server API (Docker network)
                                                            ├─ plugin DB (Postgres schema in the `maubot` DB, needs I2)
                                                            ├─ webapp /_matrix/maubot/plugin/<instance>/signup ◄── host nginx (I1) ◄── student browser
                                                            └─ http://userbots:29316/_matrix/maubot/v1  (second maubot, I7) ──► professor bots
```

**Privilege**
- The bot's own Matrix account (`@adminbot:<server>`) is the only privileged identity. It has the server-admin flag.
- Professors never get credentials.
- The bot reaches Synapse directly on the Docker network (`http://synapse:8008`, as in `homeservers:` in the maubot config). The host nginx IP allowlist on `/_synapse/admin` therefore doesn't affect it, and no nginx change is needed for the admin API.

**Where commands are accepted**
- Only in a direct room between the bot and the sender. Links, email lists and tokens therefore never end up in a shared room.
  - Matrix has no server-side "is DM" flag. The bot treats a room as a DM when it has exactly two joined members, the bot and the sender, and no third invite is pending. This is checked on every command, not cached, because someone can be invited later.
- Only from senders on the local server. Federation is already off (`federation_domain_whitelist: []`), so this is a cheap second guard.

**APIs**
- Synapse calls go through mautrix's `SynapseAdminPath` and `client.api.request`.
- maubot management calls use `self.http` (aiohttp) against `<base>/_matrix/maubot/v1/...` (`auth/login`, `plugins/upload`, `client/new`, `instance/{id}`, and so on).
- The API details were checked against maubot 0.6.0 / mautrix 0.21.1. The compose file uses `maubot:latest`, so pin that tag first (I4). Otherwise these notes can go stale without warning.

**Dependencies**
- No new pip dependencies. The maubot image doesn't install plugin dependencies.
- YAML uses `ruamel.yaml` (bundled with maubot). Zip inspection uses the stdlib.
- On the MacBook, a venv with `maubot` installed provides `mbc` and lets pytest import mautrix.

**Startup self-check**
- On start, the plugin does the following and reports any failure to the audit room:
  - Checks that the bot account is a server admin (`GET /_synapse/admin/v2/users/<self>`).
  - Sets its own rate-limit override (`POST /_synapse/admin/v1/users/<self>/override_ratelimit`). As a server admin it can do this itself, so it isn't a manual setup step.
  - Checks that the audit room is reachable.
  - Checks that the plugin DB is Postgres, not SQLite. Otherwise it warns: I2 is missing, and the data wouldn't be in the backup.
  - Checks that the userbots maubot is a *different* maubot than the one the plugin runs in (§10). Only if bots are enabled.
- Commands that depend on a failed check are refused.

## 3. Hardening in plugin code

The plugin enforces these itself, whatever the deployment looks like:

- Allowlist and role checks on every handler (§5).
- Commands only in DMs (definition in §2), only from local senders.
- Signup tokens are random, single-use, stored only as a hash, and expire after 14 days.
- Rate limits on the signup page, per client IP and globally.
  - The client IP comes from `X-Forwarded-For`, which host nginx sets.
  - The header is trusted only from the configured proxy addresses, because maubot only sees the Docker gateway as the peer.
- Minimum password length enforced in the bot, because the admin API bypasses Synapse's password policy (`homeserver.yaml.template` doesn't set a `password_config` anyway).
- Never logs tokens, passwords or access tokens. This matters here because the maubot logging config is DEBUG for `maubot` and `mau`.
- Bulk and destructive actions go through confirm.
- The plugin doesn't need Synapse's `registration_shared_secret`. Users and bot accounts are created through the admin API with the bot's own token. That makes it safe to remove `registration_secrets` from the maubot config (I3).

## 4. Accounts and signup links

**Username mapping (`identity.py`)**
- The localpart is the part before `@`, lowercased; it must consist of `[a-z0-9._=-]` only (otherwise the address is rejected).
- Allowed domains come from config, by default `unibas.ch` and `stud.unibas.ch`.
- **The same localpart on either allowed domain is the same person, and so the same account.**
  - `max.muster@unibas.ch` and `max.muster@stud.unibas.ch` both resolve to `@max.muster:matrix.dmi.unibas.ch`.
  - Both addresses can be bound to that account as email threepids.
  - Pending invites and signup tokens are keyed by user ID, not by email. A professor listing both addresses, or two professors using different ones, still produces one account, one link and merged invites.
  - *Assumption to confirm with ITS:* both domains share one localpart namespace, so one localpart can never be two different people (§15).
- **Reserved localparts:** `adminbot`, `admin`, the `bot.` prefix (§10) and the `guest.` prefix (below) are rejected for people.

**Guests** (no unibas address, for example a guest lecturer)
- `!signup guest <email> <name> [course]` (admins only) creates a link for `@guest.<name>:server`.
- The `guest.` prefix makes these accounts recognisable and keeps them from colliding with university localparts.

**Signup token**
- 32 random bytes in URL-safe encoding. Only the SHA-256 hash is stored.
- Single use, valid for 14 days.
- Tied to `(user_id, emails, created_by, pending_invites)`.

**Link base URL**
- Links are built from the plugin config key `signup.public_base_url`, *not* from maubot's `self.webapp_url`. That one comes from `server.public_url`, which is `https://localhost:8443` (the SSH tunnel).
  - Production: `https://matrix.dmi.unibas.ch/_matrix/maubot/plugin/<instance>/`, once I1 is in place.
  - Test node: the tunnel URL until it has its own route.

**Delivery without SMTP.** The bot only *produces* links. People deliver them. Options, from most to least practical:

1. **Mail-merge CSV (default for bulk).**
   - `!invite` returns `signup-links.csv` (`email, user_id, link, course`) plus a ready-to-paste message template.
   - Professors send it with Outlook/Word mail merge or whatever they already use to email the course.
   - The professor's own address is the sender, which students trust more than an unknown system address anyway.
2. **A single link inline.** `!signup new <email>` replies with one link and a short text to forward. Good for latecomers and guest lecturers.
3. **Through the course platform** (ADAM/Moodle). The professor uploads per-student messages or attaches the CSV in the LMS's own messaging. This is outside the bot; it's documented in the professor guide.

- The delivery code sits behind a small `Delivery` interface, so SMTP could be added later without touching the commands.
- Not recommended: running our own mail server or relay container. The university's mail filters will likely mark it as spam, and it needs ITS approval anyway.

**Why not Synapse registration tokens** (they already work through Ketesa)
- Considered and rejected for the permanent setup:
  - **Usernames are free.** Synapse can't tie a token to a username; its spam-checker hook doesn't see the token. Anyone can register `prof.mueller`, and one person can make several accounts.
  - **The token doesn't say who used it.** The admin API only counts uses. `!invite` therefore can't put new people into their course after they sign up.
  - **No email on the account.** Roster sync and "who is this account?" become guesswork.
- With no IDM ever coming, these would be permanent rather than temporary problems. The signup page costs one webapp and one nginx route (I1).
- **Stopgap:** until Phase 3 is live, Ketesa tokens remain the way to onboard staff and testers. Those few accounts are checked by hand. Afterwards open registration is switched off (I8).

**Signup page (maubot webapp)**
- `GET /signup?t=…` shows the fixed username, a display-name field and password + confirm.
- `POST` does the following:
  1. Creates the user with `PUT /_synapse/admin/v2/users/@x:server` (password, display name, email threepids). This works whatever `enable_registration` is set to.
  2. Burns the token.
  3. Applies pending invites (see below).
  4. Shows a "log in at `https://matrix.dmi.unibas.ch` with Element" page.
- Generic error pages. Minimal HTML with no external assets.
- The form is protected against CSRF, with a per-form nonce bound to the token.

**Invite vs. force-join**

| | Invite | Force-join (`POST /_synapse/admin/v1/join/<room>`) |
|---|---|---|
| What the user sees | A pending invitation they accept or decline | The room or space just appears in their list; they're already a member |
| Consent | Yes, standard Matrix behaviour | No; the server admin puts them in |
| Failure mode | Invites get ignored or missed, so students "aren't there" | Unwanted rooms; users have to leave themselves |
| Requirements | None special | Server-admin token and a local user. For non-public rooms the bot must be in the room with invite power, which is always true for bot-created rooms |

Decision:
- **Existing accounts get an invite to the course space.** Rooms are `restricted` to space members, so one accepted invite opens the whole course.
- **People who create their account through a course signup link are force-joined** into that course's space. They clicked the link for exactly this purpose, so asking again would only add friction.
- Both are configurable (`invite_mode_existing`, `invite_mode_new`).

**Password reset (same page)**
- Without SMTP and without IDM, Synapse can't reset passwords by itself. Today the only way is an admin in Ketesa.
- `!signup reset @user` (admins only) issues a single-use reset link, valid for 24 hours. The page shows the fixed username and only asks for a new password. It then calls the admin API (`POST /_synapse/admin/v1/reset_password/<user>`, `logout_devices: true`).
- **Admins only, not professors:** whoever issues the link can open it themselves. For professors, that would mean taking over a student's account.
- Every reset is audited. The bot also DMs the affected user that their password was reset, so a misused reset is noticed.
- The user guide says: keep the Element recovery key. Otherwise a reset logs out all devices and encrypted history is lost.

**Commands**
- `!signup new <email> [course]`
- `!signup guest <email> <name> [course]` (admins)
- `!signup list [course]`
- `!signup revoke <email|@user>`
- `!signup renew <email|@user>`: issues a new link and invalidates the old one.
- `!signup reset @user` (admins): password-reset link for an existing account.

## 5. Roles and authorisation

**An email domain never grants rights.** A `@unibas.ch` address (ITS staff, for example) only means "may have an account".

| Role | Granted by | Can do |
|---|---|---|
| `admin` | `admins:` list in the plugin instance config (bootstrap) | everything: manage professors, global templates, audit, approve/kill user bots |
| `professor` | allowlist, managed by admins: `!admin prof add\|remove\|list @user` (DB, audited); config may pre-seed it | create courses; full control of courses they own; appoint TAs; personal templates; own bots (§10) |
| `ta` | appointed per course by that course's professor: `!course ta add\|remove <code> @user` | invite, roster sync and groups within that course only; no delete, archive, TA changes or bots |

- Checks run in one place, `authz.require(role, course=…)`.
- Ownership lives in the bot DB (`course_staff`), never in room power levels.
- Power levels: owner (professor) **100**, TA 50, bot 100. They're configurable, but those are the defaults.
- The plugin's `admins:` list is separate from the Synapse server-admin flag and from the maubot UI `admins:`. Being a Synapse admin (for Ketesa) grants nothing in the bot.

## 6. Confirm, dry-run and jobs

**Confirm and dry-run**
- Every mutating bulk command first replies with a plan summary and a code: `!confirm 7f3k` / `!cancel`.
- Only the requester can confirm. Plans expire after 10 minutes.
- `--dry-run` shows the plan only.

**Jobs**
- Confirmed plans become jobs with item-level state.
- A throttled worker runs them and edits a single progress message.
- Unfinished jobs resume after a restart.
- The final report lists failures per item.

## 7. Audit

- An `audit` table plus a private `audit_room`. Each entry records timestamp, actor, action, course or bot, target count, job id and result.
- `!admin audit [n]` shows recent entries.

**Data protection**
- The *bot DB* keeps emails only while a signup is pending. They are deleted when the token is used or expires (14 days).
- The audit log stores counts and user IDs, never email lists.
- Emails bound as threepids stay in *Synapse* as part of the account. That is deliberate: without IDM, the email is the only record of who an account belongs to. It's also needed for roster sync and for SMTP-based resets if SMTP ever comes.
  - The privacy notice should say so.
  - If that isn't wanted, set `bind_threepids: false` in the config and keep only the localpart.

## 8. Language

- All user-facing text lives in `adminbot/strings/en.py`, as keyed strings with placeholders. Handlers never contain literal text.
- German is added later as `strings/de.py` plus a `language:` config key, or per-user preference.

## 9. Courses, rooms, templates

**Course commands**
- `!course create <code> "<title>" [--semester HS26] [--template <name>]`
- `!course list`
- `!course info <code>`
- `!course ta add|remove <code> @user`
- `!room add <code> "<name>" [--private] [--topic …]`
- `!room remove <code> <room>`

**Template format (YAML, `version: 1`)**

```yaml
version: 1
space: {name: "{code} {title} ({semester})", topic: "..."}
defaults: {join_rule: restricted, encryption: true, history_visibility: shared}
rooms:
  - {name: "Announcements", power_levels: {events_default: 50}}
  - {name: "General"}
  - {name: "Questions"}
  - {name: "Staff", join_rule: invite, members: staff}
groups: {count: 0, name: "Group {n}"}
```

- Rooms use the server's default room version (v10 on Synapse 1.157). `restricted` needs v8 or newer, so templates may not set `room_version`.
- The bot creates encrypted rooms, and posts into them for `!groups post`. That needs working E2EE on the bot client: the pickle key from I3 and verification as in `server/docs/maubot.md`.

**Template commands (export and show merged)**
- `!template export <course-code> [<name>]`
  - Snapshots the live course: names, topics, order, join rules, encryption and power-level overrides. Members are left out.
  - Saves it under `<name>` (default: the course code) and **always replies with the `.yaml` as an attachment**, so one command both stores and downloads.
- `!template export --saved <name>` re-downloads a stored template whose course may no longer exist. v1 used a separate `show` command for this.
- `!template import <name>` (with an attached `.yaml`): validates against the schema and saves.
- `!template list`
- `!template delete <name>`
- Scope: `personal` (the owning professor) or `global` (admins).

## 10. Professor-owned bots

**Goal**
- A professor sends an `.mbp` file to the admin bot in a DM.
- A bot account, maubot client and instance are created automatically.
- The professor can then invite the bot into their rooms.
- This replaces the manual process in `server/docs/maubot.md`: create the account, `mbc auth`, create the client and the instance.

### Why a second maubot instance is required

- maubot plugins run as ordinary Python inside the maubot process. There is no sandbox.
- Any plugin can import maubot internals and read every client's access token and the plugin databases.
- If professor plugins ran in the same maubot as the admin bot (`matrix-maubot`), **any uploaded plugin could take the admin bot's server-admin token**.
- So professor bots run in a separate maubot ("userbots", I7):
  - its own container and database
  - no `registration_secrets`
  - no published port, reachable only on the Docker network
  - its only admin account is used by the admin bot
- The admin bot manages it through the maubot management API.
- **Startup guard:** the admin bot lists the target instance's clients and instances. If its own client shows up there, the target is the same maubot, and bot provisioning is refused.

**Remaining risk to accept**
- Professor bots share one process with *each other*. A malicious professor plugin could read another professor bot's token. Those tokens belong to unprivileged bot accounts.
- Mitigations: admin approval, a per-professor quota, and a kill switch.

### Flow: `!bot create <name>` with an attached `.mbp`

1. **Authz:** professor or admin. Quota check (`user_bots.max_per_professor`, default 3).
2. **Validate the package** (stdlib `zipfile`):
   - size limit
   - `maubot.yaml` present
   - `id` and `version` parse
   - main module exists
   - no absolute or `..` paths
   - Record the SHA-256 hash.
3. **Plugin ID ownership.** maubot plugin IDs are global, and uploading the same ID replaces the existing plugin for everyone.
   - The bot keeps `plugin_owner(plugin_id → professor)`.
   - An ID owned by someone else is rejected.
   - The same professor uploading a newer version means an update.
4. **Approval** (`user_bots.require_approval`, default **true**).
   - The request is posted to the audit room with owner, plugin ID, version, hash and declared dependencies.
   - An admin runs `!admin bots approve <req>` or `!admin bots reject <req> [reason]`. The professor is notified either way.
5. **Provision** (as a job):
   1. Create `@bot.<owner>.<name>:server` with `PUT /_synapse/admin/v2/users/...`, using a random password, a display name and `user_type: "bot"`. The Synapse admin API documents that field; check it once on 1.157.
   2. Exempt the account from rate limits only if configured.
   3. Get a token and device: `POST /_matrix/client/v3/login` (m.login.password). The password is then thrown away.
      - Not the admin "login as user" API, which doesn't create a real device, so E2EE wouldn't work.
   4. Log in to userbots: `POST /_matrix/maubot/v1/auth/login` with the credentials from the plugin config (`user_bots.maubot_url`, `user_bots.username`, `user_bots.password`).
   5. `POST /_matrix/maubot/v1/plugins/upload?allow_override=true`, guarded by the ownership check in step 3.
   6. `POST /_matrix/maubot/v1/client/new` with `{homeserver: "http://synapse:8008", access_token, device_id, autojoin: true, displayname}`.
   7. `PUT /_matrix/maubot/v1/instance/<owner>-<name>` with `{type: <plugin_id>, primary_user, enabled: true, started: true}`.
6. **Report back** with the bot's user ID and the instruction "invite it to your room".
   - E2EE verification of professor bots is optional and documented, not automated.

### Management commands

Professors (own bots only) and admins:
- `!bot list`
- `!bot info <name>`: status, plugin version, rooms joined
- `!bot update <name>` with an attached `.mbp`: same validation; approval again if the hash changed
- `!bot config <name>`: returns the instance config YAML; with an attached YAML it updates it (`PUT .../instance/<id>` `config`)
- `!bot start|stop <name>`
- `!bot delete <name>`:
  1. delete the instance and client
  2. deactivate the account through the admin API
  3. delete the plugin if no other instance uses it

Admin only:
- `!admin bots pending|approve|reject`
- `!admin bots disable-all`: stops every instance on userbots

**Not in scope**
- Log streaming to professors (maubot logs come through a global websocket and aren't per-instance).
- Plugin dependency installation (plugins with pip dependencies that aren't in the image are rejected, with a clear error).

## 11. Bulk invite, roster sync, groups

**Input**
- Emails pasted into the message, or an attached `.txt`/`.csv`.
- Attachments in E2EE DMs are decrypted with `mautrix.crypto.attachments.decrypt_attachment`.
- The parser normalises, de-duplicates and maps to user IDs. Both domains collapse to one ID.

**`!invite <code>`**
- Splits into existing accounts (invite to the space), new people (signup link with a pending force-join) and invalid entries.
- Goes through confirm.
- Returns the mail-merge CSV for new people.

**`!roster sync <code> [--remove]`**
- Compares the list with space members, excluding professors and TAs.
- `--remove` kicks dropped students from the space *and* every child room.

**`!groups create <code> <n>`**
- Creates invite-only rooms `Group 1..n`.

**`!groups assign <code>`**
- CSV `email,group`, or `--auto` for a round-robin split.
- Optional `ta,group` mapping.

**`!groups post <code>`**
- CSV `group,message` or `email,message`.
- Posts to each group room, or a DM per student. Always previews and asks for confirmation.

## 12. Archive and rollover

**`!course archive <code>`**
- Raise `events_default` so students can't post.
- Rename rooms to `[Archiv <semester>] …`.
- Optional `--kick-students`.
- Move the space into the configured Archive space: add an `m.space.child` there and remove it from the old parent.

**`!course rollover <code> <new-semester>`**
- Snapshot the layout as a template.
- Create the new course from it.
- Carry over the professor and TAs. No students are invited.

## 13. Code layout

Implemented 2026-10-06. `commands/uploads.py` from the skeleton became part of the router in `commands/base.py`.

```
server/admin-bot/
  claude.md              # this plan
  README.md              # dev setup, the four test levels, build
  maubot.yaml            # id ch.unibas.dmi.marvin.adminbot; config, database (asyncpg), webapp: true
  base-config.yaml       # instance config defaults
  pyproject.toml  requirements-dev.txt  .gitignore   # dev only, not in the .mbp
  adminbot/
    __init__.py          # exports AdminBot
    bot.py               # Plugin: start/stop, self-check, cleanup, registers command + web classes
    config.py  errors.py  db.py  migrations.py
    authz.py  audit.py  confirm.py  jobs.py
    identity.py          # email → user ID, domain merge, guests, reserved localparts (pure)
    parsing.py           # quoted args/flags, email lists, CSV (pure)
    roster.py            # roster diff, round-robin groups (pure)
    templates.py         # schema, parse/dump/render (pure), snapshot/apply
    synapse_admin.py     # admin API wrappers
    matrix_ops.py        # spaces, rooms, power levels, membership, DM check
    attachments.py       # file in/out incl. E2EE, PendingUploads
    signups.py           # signup + reset tokens, redeem, pending invites
    delivery.py          # Delivery interface; CsvDelivery, InlineDelivery
    userbots.py          # .mbp validation, userbots API client, BotManager
    strings/             # t(key), en.py
    commands/            # base (CommandRouter: commands + uploads, @command, helpers), help, confirm,
                         # admin, course, room, invite, roster, groups, template, signup, bots
    web/                 # signup.py (handlers), pages.py (HTML), ratelimit.py
  tests/                 # pytest with maubot's fixtures (maubot[testing])
  dev/                   # setup.sh: local Synapse + Postgres (+ second maubot), standalone config
```

Server-side setup (everything outside this folder): `server/docs/admin-bot-setup.md`. Professor guide and admin runbook come in Phase 9.

**DB tables**
- `professor`, `course`, `course_staff`, `template`, `signup_token`, `pending_invite`, `plan`, `job`, `job_item`, `audit`, `user_bot`, `plugin_owner`, `bot_request`.

## 14. Phases

| # | Deliverable | Needs (§16) | Done when |
|---|---|---|---|
| 0 | Implement on the skeleton: config, DB migrations, authz, audit, strings, startup self-check, `!help`, `!whoami`; bot account bootstrap | I2, I4, I6 | Allowlisted user gets help, others are ignored, audit room receives entries, self-check is green |
| 1 | Courses & rooms, professor allowlist, TA appointment | I5 before real data | Professor creates a course in one command and appoints a TA |
| 2 | Templates (merged export, import, list, delete, `--template`) | – | Course exported and re-created identically under a new code |
| 3 | Identity + signup links + webapp + CSV/inline delivery; guests; password reset | I1 (production); I8 right after | Link → account with the expected user ID; both email domains → same account; reset link works and the user gets a DM; page reachable from outside on production; open registration switched off |
| 4 | Confirm/dry-run, job runner, `!invite` with pending invites | – | 200 addresses processed; new users land in the course after signup; restart mid-job resumes |
| 5 | Roster sync | – | Correct diff; `--remove` only affects students |
| 6 | Exercise groups | – | Groups created, assigned, per-group posts delivered (in E2EE rooms) |
| 7 | Archive & rollover | – | Old course read-only in Archive; new semester with staff carried over |
| 8 | Professor bots | I7 | `.mbp` upload → approval → bot answers in the professor's room; delete cleans up account + instance |
| 9 | Professor guide, admin runbook, Journal entry; production transfer checklist | – | A colleague can set it up on the ITS VM from the docs alone |

**Testing** (details in `README.md`)
1. **Unit tests** for the pure modules and `Store` (SQLite), with pytest on the MacBook.
2. **Command tests** with maubot's fake client (`maubot_test_bot`); Synapse calls are monkeypatched.
3. **Local dev stack:** throwaway Synapse in Docker + the plugin from source in maubot standalone mode. No build or upload, restart in seconds. No E2EE.
4. **Test node:** the built `.mbp`, with a short manual checklist in Element per phase (E2EE, nginx, Postgres).
- Production gets the same `.mbp` afterwards.

## 15. Still open

- **ITS:** do `unibas.ch` and `stud.unibas.ch` share one localpart namespace? If two different people could have `x.y@unibas.ch` and `x.y@stud.unibas.ch`, the domain merge in §4 is wrong.
- **Offboarding:** who deactivates accounts of people who left the university, and when? Without IDM, nothing tells the server.
  - A possible later extra: `!admin users stale [months]`, which lists accounts with no activity, for an admin to review.
- A public URL for the signup page on the test node (works through the SSH tunnel until then). On production it's answered by I1.
- Professor bot defaults: quota 3 and approval required. Confirm, or relax the approval requirement later.
- Whether TAs may also see `!signup list` for their course (proposal: yes, read-only).

## 16. Infrastructure changes (outside the plugin)

The reasoning is here. The step-by-step guide with exact diffs is `server/docs/admin-bot-setup.md`.
- That guide keeps changes minimal: I1, I2, I4 and I5 now, I6 by hand, I8 after Phase 3, I7 in Phase 8.
- I3 (pickle key, `registration_secrets`) is deliberately **deferred**: changing the key breaks existing maubot clients and adds little.
- I9 is optional.

**I1. Public route for the signup page** (`server/nginx/matrix.conf`)
- Host nginx sends everything matching `^(/_matrix|…)` to Synapse, so `/_matrix/maubot/...` currently returns a Synapse 404.
- Add `location ^~ /_matrix/maubot/plugin/<instance>/ { proxy_pass http://127.0.0.1:${MAUBOT_UI_PORT}; … }`. The `^~` prefix wins over the regex location.
- Only that prefix. The management API (`/_matrix/maubot/v1`) and the UI must stay unreachable from outside.
- Set `X-Forwarded-For $remote_addr`, which the rate limit relies on.
- The vhost is a symlink to the repo file (Journal 02.10), so: `git pull && sudo nginx -t && sudo systemctl reload nginx`.

**I2. Postgres for plugin DBs** (`server/maubot/config.yaml.template`)
- Set `plugin_databases.postgres: default`. Today it's `null`, so plugins get SQLite files under `./maubot/plugins`.
- Do this before Phase 0 is deployed. Switching later means migrating data.

**I3. maubot secrets** (`server/maubot/config.yaml.template`, `.env.example`, `create_env.sh`, `setup.sh`)
- Remove the `registration_secrets` block. Neither the admin bot nor the userbots maubot needs it.
- Replace `crypto_db_pickle_key: mau.crypto` (the public default) with `${MAUBOT_CRYPTO_PICKLE_KEY}` generated by `create_env.sh`.
- **Do this before the bot client is created.** Changing the key later breaks existing crypto sessions; the client then has to be re-created and re-verified.
- Small cleanup on the side: `.env.example` and `create_env.sh` define both `MAUBOT_ADMIN_PASSWORD` and an unused `MAUBOT_ADMIN_PASS`.

**I4. Pin the maubot image** (`server/docker-compose.yml`)
- Replace `dock.mau.dev/maubot/maubot:latest` with the version the API notes were checked against, the same way Synapse is pinned through `SYNAPSE_IMAGE_TAG`.

**I5. Back up the bot state** (`server/backup.sh`, `server/restore.sh`)
- `backup.sh` only runs `pg_dump` on `$POSTGRES_DB` (synapse).
- Add `pg_dump -d maubot`, plus `-d userbots` once I7 exists.
- `restore.sh` needs the matching restore steps.

**I6. Bot account bootstrap** (documentation only, `server/docs/maubot.md` + admin runbook)
1. Create `@adminbot` with `register_new_matrix_user -a`.
2. Create its client with `mbc auth --update-client`.
3. Verify it with the recovery key, as described in `maubot.md`.
4. Create the instance with type `ch.unibas.dmi.marvin.adminbot` and fill in the instance config.
- The rate-limit override is set by the bot itself (§2).

**I7. Second maubot "userbots"** (`server/docker-compose.yml`, `server/userbots/config.yaml.template`, `setup.sh`, `create_env.sh`, `.env.example`, `postgres-init.sh`, `.gitignore`)
- New service `userbots` (`container_name: matrix-userbots`), same pinned image, volume `./userbots:/data`, **no `ports:`**.
- Its own config template:
  - database `postgres://…@postgres/userbots`
  - `plugin_databases.postgres: default`
  - its own pickle key
  - no `registration_secrets`
  - `admins:` with only `adminbot: ${USERBOTS_ADMIN_PASSWORD}`
  - `api_features` with `client_auth`, `dev_open` and `instance_database` off
- `postgres-init.sh` only runs on a fresh volume. On the existing VM, run `CREATE DATABASE userbots` once by hand. Never use `down -v` for this (Journal 02.10).
- `setup.sh` renders the new template, `chown`s it like `./maubot` and adds `USERBOTS_ADMIN_PASSWORD` to `required_vars`.
- `.gitignore`: add `**/userbots/config.yaml`, `**/userbots/plugins/`, `**/userbots/trash/`.

**I8. Switch off open registration** (`server/homeserver.yaml.template`)
- `enable_registration: true` + `registration_requires_token: true` let anyone with a token pick any username.
- Keep it until Phase 3 is live, as the stopgap for staff and testers. Then set `enable_registration: false` and drop `registration_requires_token`.
- The admin API used by the bot and by Ketesa still creates accounts with registration turned off.

**I9. Docs drift** (`server/docs/startup.md`)
- It still mentions Ketesa in the stack, which was removed in `235833c`. This isn't blocking, but fix it in the same PR.

**I10. Test node**
- Same items as above. The signup page stays on the SSH tunnel URL until the test node has a reachable hostname.
