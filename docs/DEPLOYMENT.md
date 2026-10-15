# Controlled Vultr deployment

**Status: Gate A approved and staging complete; Gate B is NOT approved.** Release
**`9a4500aa1cc3272471b6492c6c4ec96527ebc7dd`** was installed on 15 October 2026;
final staging checks completed around **21:33 UTC**. Rolfbot remains stopped,
not enabled at boot and persistently masked. Protected credentials are installed;
the production database is absent. The unchanged local database remains
authoritative. No live Discord or OpenAI connections were made during staging.
Owner-reported local live checks are separate from pending production acceptance.

The owner's deployment request requires explicit approval before VM changes and
a second approval immediately before live cutover. This document is a staged
procedure, not authorization to execute all its commands. Keep a trusted SSH
connection open. Scheduling, personality customization and music remain disabled.

## Pre-staging baseline (Phase A)

These observations were collected before Gate A approval. The Discord/OpenAI
connectivity probes below were not repeated during staging; application checks
ran offline with networking disabled.

- Clean `HEAD`, `main` and the local `origin/main` reference all matched
  **`9a4500aa1cc3272471b6492c6c4ec96527ebc7dd`** before deployment preparation.
  A read-only `git ls-remote` over strictly verified SSH also confirmed upstream
  `refs/heads/main` at that exact SHA before gate A.
  No application source has changed. New deployment helpers/docs are separate,
  uncommitted operational material and are not silently included in that release.
- VM: `root@70.34.206.254`, hostname `nilsherman`, Vultr VC2, x86-64,
  Ubuntu **24.04.5 LTS**, kernel `6.8.0-142-generic`, systemd **255.4-1ubuntu8.17**.
  Authentication used `~/.ssh/nilsherman_vultr` and the existing known-host entries
  with strict host-key checking. No host key was added or verification bypassed.
- One vCPU; 955 MiB RAM, approximately 594 MiB available at inspection; 2,399 MiB
  swap with 27 MiB used. Load averages were 0.00. Root storage is local ext4,
  23 GiB total with about 16 GiB free; inode usage 3%.
- Ubuntu Python **3.12.3**, package `3.12.3-1ubuntu0.17`, was installed. `venv` was
  importable but `ensurepip` was absent. Simulated installation added only
  `python3.12-venv`, `python3-pip-whl`, and `python3-setuptools-whl`: zero upgrades
  or removals. Unrelated pending OS updates are outside this task.
- No kernel I/O/ext4/read-only-filesystem errors matched the current boot log
  inspection. UFW remains active with incoming deny, outgoing allow and web
  access restricted to the documented Cloudflare ranges; SSH remains unchanged.
- No failed systemd units. nginx active/enabled, about 6.6 MiB cgroup memory.
  `nilsherman-deploy-recover.timer` is active/enabled; its latest oneshot completed
  successfully. `nginx -t` passes. Public listeners are TCP 22/80/443, plus the
  existing resolver/DHCP sockets. No Rolfbot account, unit, process or installation
  was found in the inspected standard locations.
- The website documentation is older than the live state: its current release is
  `20261015T155615Z-c066d654843867dfca237e1f91109a402a7932c8-38764390073-1`.
  The website repository is clean at `c066d654843867dfca237e1f91109a402a7932c8`.
  Keep its receiver, deployment account, workflow, directories and recovery timer
  entirely separate from Rolfbot.
- Both public names returned HTTPS **200**, certificate verification successful:
  `nilsherman.no` about 0.234 s; `www.nilsherman.no` about 0.220 s. Direct loopback
  origin HTTPS with its existing CA returned 200 in about 0.005 s. These are
  individual baseline samples, not a performance guarantee.
- DNS resolution and verified outbound HTTPS to Discord/PyPI work. A verified
  TLS 1.3 handshake to `api.openai.com:443` succeeded **without sending HTTP or an
  OpenAI API request**. Real Discord login remains a gate-B action.
- No local Rolfbot process was detected at inspection. Recheck immediately before
  cutover. Only the owner can confirm whether another, older VM still runs it.

## Approved Gate A layout

The staged filesystem/account changes are:

| Location | Ownership/mode and purpose |
| --- | --- |
| `rolfbot` system user/group | Locked account, `/usr/sbin/nologin`, no SSH or sudo; home `/var/lib/rolfbot` |
| `/opt/rolfbot/releases/<full SHA>/` | Root-owned code and `.venv`; directories 0755, ordinary source files 0644; service cannot write |
| `/opt/rolfbot/current` | Root-managed symlink to the reviewed release |
| `/opt/rolfbot/staging/<full SHA>/` | Root-owned non-secret artifacts, manifests and separate offline test environment |
| `/var/lib/rolfbot/` | `rolfbot:rolfbot`, 0700; only persistent runtime writable directory |
| `/var/lib/rolfbot/usage.sqlite3` | `rolfbot:rolfbot`, 0600; installed only during gate-B cutover |
| `/var/backups/rolfbot/` | `root:root`, 0700; accounting backups, never application releases |
| `/etc/rolfbot/rolfbot.env` | Directory 0700 and file 0600, root-owned; PID 1 passes settings to the service |
| `/etc/systemd/system/rolfbot.service` | Persistent mask: symlink to `/dev/null`; no boot enablement links |
| `/opt/rolfbot/staging/<full SHA>/rolfbot.service` | Root-owned 0644 candidate, validated and retained until Gate B |

The initial runtime-mask proposal was corrected before installing anything:
systemd searches `/etc/systemd/system` before `/run/systemd/system`, so a real
unit in `/etc` would override a runtime mask. A persistent `/etc` mask was the
first VM change and stayed in place throughout staging. The candidate definition
remains under `/opt`; it must only replace the mask after Gate B approval.

Install only the three simulated venv packages with
`NEEDRESTART_MODE=l apt-get install --no-install-recommends python3.12-venv`, after reviewing the actual
transaction again. Do not upgrade unrelated packages or change global pip.
Use needrestart's list-only mode so package installation does not restart existing
services. If a wider transaction or service restart is required, stop for review.
If repository metadata needs refreshing, report the new package transaction;
do not accept a wider installation silently.

The runtime contains the application and **23 pinned dependencies** on Python
3.12, including OpenAI 3.31.0, HTTPX2 2.13.1, discord.py 2.7.1 and aiohttp 3.14.5.
[The runtime lock](../deploy/requirements-runtime.lock) is derived from the exact
release's development lock and installed dependency metadata. It excludes pytest
and the build backend. The conditional audioop dependency applies only to Python
3.13. Build the application wheel locally from the exact Git source; production
installs wheels with `--no-index --no-deps`, then runs `pip check`.

Local runtime-only installation occupies about **75 MiB**; dependency wheels are
about **9.7 MiB**. Budget approximately **300 MiB** for the first release, manifests,
wheels and a separate temporary test environment. Future retained releases/backups
need their own space; never delete accounting to reclaim space.

The [proposed unit](../deploy/rolfbot.service) has no inbound listener. It uses
`NoNewPrivileges`, a read-only system, hidden home directories, private temporary
storage/devices, no capabilities, restricted namespaces/address families and only
`/var/lib/rolfbot` as an explicit writable system path. It runs Python isolated,
unbuffered, without bytecode writes. DNS, IPv4/IPv6 HTTPS/WebSockets, AF_UNIX and
SQLite journal creation remain possible. The 45-second SIGTERM stop deadline
exceeds the 30-second API deadline and permits cleanup. Restart delay is 20 seconds,
with at most three starts per five minutes; exits 130/143 are clean shutdowns.

Initial limits are **MemoryHigh=192 MiB, MemoryMax=256 MiB, swap maximum=64 MiB,
CPUQuota=50% of one CPU, CPUWeight=10, IOWeight=10, TasksMax=64, LimitNOFILE=256**.
A network-blocked local runtime probe with two concurrent mocked generations and
64 populated snapshots measured **75.0 MiB peak RSS, 3 threads and 8 descriptors**.
These limits leave substantial margin over that probe and preserve website
headroom. Its Ubuntu repeat passed during staging (recorded below). It does not
include a real Discord gateway; observe actual service memory before accepting
production. Stop Rolfbot if
website performance degrades. [systemd resource controls](https://manpages.ubuntu.com/manpages/noble/man5/systemd.resource-control.5.html).

Gate A also covers preparing the production environment from the existing private
local file over the verified SSH connection. Preserve the one currently configured
guild/channel and the existing model/reasoning settings; do not add a guild.
The owner approves this specific source and transfer, without displaying secrets.
No downtime is required for staging. Final cutover should take roughly **2–5
minutes** after stopping the local instance; allow longer for investigation rather
than bypassing a failed check. Conversation memory will be lost intentionally.

## Artifact provenance and validation

`deploy/release.py build REPO FULL_SHA OUTPUT_DIRECTORY` reads Git blobs directly,
not working-tree files. The allowlist contains only `src/rolfbot/*.py`, `README.md`,
`pyproject.toml` and the committed dependency lock. Its 16-file tar has no Git
metadata, secrets, databases, virtual environments, caches or local test output.
The manifest records every file's size/hash and the full commit. Verification
rejects unexpected/duplicate paths, traversal, links, devices, PAX overrides,
oversized content, missing files and checksum mismatches before writing anything.
Extraction requires a new directory and verifies the files again afterward.

Prepared locally under `/tmp/rolfbot-deploy-9a4500/`:

- `artifact/source.tar`: **153,600 bytes**, SHA-256
  `9651e595075b2112e1f74e6306c7c44c449fa34e1ae1751fd7e787ea41d49749`.
- `artifact/manifest.json`: exact commit/file manifest.
- `app-wheel/rolfbot-0.1.0-py3-none-any.whl`: SHA-256
  `a227e83de7dabe3f139c11cd5c20e8f6ca3fc54eaf3682bd3a57009787a374b0`.
  Every packaged Python file was compared against its committed-source hash.
- `runtime-wheels/`: pinned CPython 3.12/Linux x86-64 wheels, downloaded over HTTPS.
- `runtime-venv/`: isolated local verification installation; never transfer a venv.
- `transfer/`: **54 explicitly inventoried files, 13,068,452 bytes**, containing
  the above artifacts, operational helpers, separate test wheels and original
  committed staging tests. Its `SHA256SUMS` digest is
  `6acf5c99ae26bb0906e9d68be92ec787e2cf1e29e11b3d7f247c2d59c0a9d0bb`.
  No credentials, accounting database or local virtual environment is included.

Deployment helpers/unit/lock are reviewed operational assets, separate from the
immutable application archive. Hash them and every wheel in a transfer inventory;
record that inventory's digest before transfer. Transfer only the explicitly
inventoried files over strict SSH, verify the inventory on the VM, then run the
source verifier with the approved commit and archive digest. Do not use the website
receiver or account. Never overwrite an existing release directory.

For later releases, rebuild from a new reviewed full SHA and create a fresh
manifest/wheel inventory. Regenerated wheel hashes can differ; review and record
them rather than claiming an old artifact digest. Keep the previous release.

## Phase B: completed staging procedure

1. Recheck website health, disk/RAM, SSH identity, the package simulation and
   absence of conflicting Rolfbot paths/accounts. Refuse unexpected existing state.
2. Persistently mask `rolfbot.service` first. Install the approved venv packages
   and create the isolated account/directories. Keep the actual unit definition
   in the staging directory; do not replace the mask or enable the service.
3. Transfer/verify the allowlisted archive, manifests, wheels and operational
   assets. Extract the application into the new full-SHA release directory.
4. Create its venv with Ubuntu Python 3.12. Install the pinned runtime wheels and
   reviewed application wheel offline, then `pip check`, import and compile.
   Verify installed application-file hashes against the source manifest.
5. Prepare a separate staging venv for the original **432-test** suite exported
   from that exact commit. Install pinned test dependencies there, never into the
   service venv. Run as an unprivileged user with existing network guards and
   temporary databases; production state does not exist yet.
6. Run `deploy/offline_probe.py` using the service venv under the proposed systemd
   restrictions. It uses only synthetic settings, a fresh temporary database,
   mocked HTTP and an audit hook rejecting network calls. Record RSS, threads,
   descriptors, permissions and cleanup on Ubuntu.
7. Transfer the production settings as described below; run offline `check-config`
   with systemd EnvironmentFile loading. No Discord login or smoke test.
8. Set the root-controlled `current` link and run `systemd-analyze verify` on
   `/opt/rolfbot/staging/<full SHA>/rolfbot.service`. Check that the service remains
   stopped, without boot enablement links and persistently masked. Validate
   resource and write permissions through the separate offline probe, not by
   starting the bot.
9. Compare listeners, website-related configuration hashes, service health and
   both public HTTPS checks against Phase A. Investigate any difference; never
   restart nginx or change website configuration to accommodate Rolfbot.
10. Present staged results and **request gate B**. Do not copy final accounting,
    stop a live local instance, unmask/start Rolfbot, or send live tests yet.

The retained local SSH control socket is
`/tmp/rolfbot-deploy-9a4500/ssh-control`. If it expires, reconnect with the same
personal key, `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`,
`UserKnownHostsFile=~/.ssh/known_hosts`, `UpdateHostKeys=no`, and no agent forwarding.
A changed host key requires owner verification, never an insecure bypass.

## Credentials and rotation

The local source `~/.config/rolfbot/local.env` was inspected only through a parser
that reports non-sensitive metadata. It is mode 0600 and contains one guild and
one channel. No credential value was displayed or included in artifacts.

`deploy/prepare_env.py SOURCE` validates without showing values. Its `--emit`
mode is **only for a direct SSH stdin pipe**, with local shell tracing off and
pipeline failure checking. Never invoke it standalone in a tool or terminal that
captures stdout. The remote receiver writes a new temporary file within the
root-only `/etc/rolfbot` directory, validates its syntax and settings without
printing values, and fsyncs it. Initial staging used an atomic hard-link creation
of `rolfbot.env` that refuses an existing destination, then removed the temporary
name and fsynced the directory. No secrets appear in argv or the unit.
Validate before replacing an existing file; preserve a protected previous version
for rotation rollback.

The helper accepts simple, whitelisted shell assignments without executing them,
escapes systemd quoted values and replaces the database setting with the literal
`/var/lib/rolfbot/usage.sqlite3`. systemd EnvironmentFile does not expand `$HOME`.
The service receives credentials from PID 1; it need not read the root-only file.
For rotation, prepare/validate a replacement through the same protected path,
stop the service, atomically replace the environment file, start and verify it.
Never rebuild the application for key rotation or modify billing settings.

## Gate B: one-instance accounting cutover

Before requesting gate B, show the exact staged SHA, tests, permissions, website
health and service state. Obtain explicit approval to stop the local bot, transfer
final SQLite state, activate production and perform a few owner-driven Discord
checks. **Ask the owner to confirm every old VM instance is stopped.** Local
inspection cannot prove the absence of a bot on an unrelated host.

After approval:

1. Stop the known local process gracefully with SIGTERM, if running; wait for it
   to exit. Check for remaining current/legacy Rolfbot processes locally and on
   the target. Confirm no startup supervisor can bring a duplicate back. Keep
   the local bot stopped for the whole transfer and afterward.
2. Reinspect `~/.local/state/rolfbot/usage.sqlite3`. At Phase A it was 32 KiB,
   schema 2, integrity OK, DELETE journal mode, Europe/Oslo, six completed attempts,
   two cooldowns, 540 input/67 output tokens and no provider block. These numbers
   are a baseline, not final cutover totals. No sidecars existed.
3. Use `deploy/state.py inspect SOURCE` for a transaction-consistent aggregate
   summary and hash of **all** accounting rows, including cooldowns and block
   metadata. It does not print raw user IDs or attempt rows.
4. In an owner-only backup directory, use
   `deploy/state.py backup SOURCE NEW_BACKUP --confirmed-stopped`. The helper
   checks for sidecars, incompatible state and competing SQLite locks, then uses
   SQLite's backup API. It compares all rows/metadata before and after, makes a
   mode-0600 backup, fsyncs file/directory, and prints its SHA-256. The flag is an
   operator attestation that all instances are stopped. Never delete a journal
   or force recovery if a check fails. [SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup).
5. Keep that protected local backup. Transfer it through strict SSH to the new
   root-only backup directory on the VM. Verify its SHA-256 and logical summary
   there. Install into `/var/lib/rolfbot/usage.sqlite3` only if no production
   database already exists; set `rolfbot:rolfbot`, mode 0600. Never overwrite
   unexpected existing accounting.
6. Verify integrity, schema, all-row fingerprint and totals again as `rolfbot`.
   Validate through the application's existing `runtime.open_store()` with
   production settings, using a separate one-shot process. It checks compatibility,
   write access and provider blocks without constructing network clients.
   A systemd transient unit can load the root-only EnvironmentFile without
   exposing credentials in arguments. Do not substitute `init-db`.
7. `PRAGMA synchronous` is a **connection setting**, not persistent metadata:
   the application explicitly sets EXTRA (3) on its SQLite connections. The
   persisted journal mode must remain DELETE. A standalone inspection connection's
   default synchronous value does not describe the application's connection.
8. Recheck duplicate instances. Unmask `rolfbot.service`, install the reviewed
   staging definition as root-owned mode 0644 at
   `/etc/systemd/system/rolfbot.service`, reload systemd, verify the installed unit,
   then start it. These actions require Gate B. Verify startup,
   command synchronization and ready events. Observe resource usage and website
   health before requesting owner Discord interactions.

If anything is ambiguous, leave the bot stopped and request operator review.
After the first production request, the VM database is authoritative. Returning
to local execution requires stopping production and transferring its **latest**
consistent accounting back; the pre-cutover backup cannot reopen old quota.

## Acceptance, restart and boot

Observe the running service for at least five minutes, checking safe logs, restarts,
memory/CPU and both public websites. Owner performs one slash command, one mention,
one reply-chain follow-up, an ordinary message that receives no response and an
unconfigured-channel check if convenient. Respect cooldowns and existing quotas;
do not deliberately exhaust limits. Record safe outcomes, not conversation text.

Compare aggregate production usage/token counts with the cutover summary. The
owner checks the Rolfbot OpenAI project dashboard after reporting delay; a running
systemd process alone does not prove API acceptance or project attribution.

After successful live checks, perform a controlled graceful stop/start, preserve
the same DB, check cooldown/quota continuity, one startup sync per guild, no replay,
no lingering process and website health. Verify bounded restart settings and
`NRestarts`; do not inject paid API failures. Enable at boot only after successful
validation, using `systemctl enable rolfbot.service` and `systemctl is-enabled`.
Do not reboot the VM without separate owner authorization.

Record exact deployed SHA/date, Ubuntu Python/package versions, artifacts' hashes,
actual limits, offline/live results, restart observations and any deviations here.
Until then, these remain pending acceptance items.

## Operations, rollback and backups

Commands on the target, after deployment:

```sh
systemctl status rolfbot.service --no-pager
journalctl -u rolfbot.service --since '15 minutes ago' --no-pager
systemctl show rolfbot.service -p ActiveState -p SubState -p NRestarts -p MemoryCurrent -p MemoryPeak -p TasksCurrent
systemctl restart rolfbot.service
# Emergency stop, including first-deployment rollback:
systemctl disable --now rolfbot.service
# Preserve the installed definition before replacing it with a persistent mask.
cp -a /etc/systemd/system/rolfbot.service /opt/rolfbot/staging/rolfbot.service.before-mask
ln -s /dev/null /etc/systemd/system/rolfbot.service.mask
mv -T /etc/systemd/system/rolfbot.service.mask /etc/systemd/system/rolfbot.service
systemctl daemon-reload
systemctl is-enabled rolfbot.service # expected: masked (nonzero exit status)
```

Inspect logs locally; do not dump EnvironmentFile contents, process environments,
raw credentials or unrelated services into reports. Fixed Rolfbot events are safe.

On first deployment there is no previous production release: rollback means stop,
disable and mask Rolfbot while preserving its database, backups and evidence.
If the website slows down, stop Rolfbot immediately and remeasure public health;
do not reload nginx, alter Cloudflare or change website deployment files.

For future upgrades, prepare/test a new immutable SHA, preserve the previous
release, stop the service, back up accounting, switch the root-managed `current`
link atomically, reload a reviewed unit if changed, then start/verify. Roll back
**code only** to a compatible retained release; do not restore older usage data.
Dependency changes require regenerated pins/wheel hashes and offline testing on
both supported Python versions plus Ubuntu staging. Explicit schema migrations
require separate review, a stopped service and a reconciled recovery plan.

Backups contain private usage metadata: keep root-only on the VM and owner-only
off-host. Make consistent backups while stopped, verify integrity/fingerprints,
and periodically test restoration to a separate private scratch location without
starting Discord. No automatic backup timer is installed by this task. A missing,
corrupt or incompatible DB keeps AI disabled; never run `init-db` or clear service
blocks as recovery. A stale backup cannot safely replace newer accounting without
operator reconciliation. Establish off-host backup retention after deployment.

If a duplicate instance is discovered, stop it immediately, then stop production
if separate databases may have accepted requests. Preserve both databases; do not
merge counts or restart until accounting is reconciled. Never resume the local bot
while production remains active.

## Verification record and remaining gates

- Phase A: exact clean application commit verified; **432 original offline tests
  passed on Python 3.12.15 and 3.13.16**. Runtime-only installation, `pip check`,
  compilation, source/wheel provenance, safe environment parsing and the two-call
  network-blocked resource probe passed locally.
- **463 total tests pass on both Python versions**: the 432 application tests plus
  31 deployment-preparation tests for archive boundaries, environment conversion,
  protected backup preservation and ambiguous-state refusal. Compilation and
  `git diff --check` passed during preparation. Tests use synthetic accounting.
- Gate A was explicitly approved, including protected credential transfer. The
  VM received exactly the 54 inventoried non-secret files; all hashes matched.
  All 16 extracted source files and 13 installed Python modules matched the
  reviewed release manifest. `current` points to that full-SHA release.
- Only `python3.12-venv` **3.12.3-1ubuntu0.17**, `python3-pip-whl`
  **24.0+dfsg-1ubuntu1.3**, and `python3-setuptools-whl` **68.1.2-2ubuntu1.2**
  were installed: zero upgrades/removals. Needrestart used list-only mode;
  its suggested systemd-logind restart was deferred. No existing service was
  restarted, and no VM reboot occurred.
- Ubuntu **Python 3.12.3**: **432 original application tests passed in 26.32 s**,
  as `rolfbot` in a separate staging venv, with private networking and existing
  test network guards. Runtime and test `pip check` passed; compilation passed.
  Runtime has the 23 pinned dependencies, without pytest or the build backend.
- The candidate unit passes `systemd-analyze verify`. A transient offline probe
  copied its hardening/resource settings, adding `PrivateNetwork=yes`. Two
  concurrent mocked generations and 64 memory snapshots passed, including SQLite
  dispatch and cleanup using a disposable database in private temporary storage.
  It measured **74,164 KiB (72.4 MiB) peak RSS, 3 threads and 9 descriptors**.
  In-process cgroup peak was **57,069,568 bytes (54.4 MiB)**; shared memory accounting
  differs from RSS. The cgroup had no high/max/oom events. The transient runner's
  post-exit memory summary was smaller, so the in-process measurements are used.
- Effective cgroup values matched **192/256 MiB memory high/max, 64 MiB swap max,
  50% CPU, CPUWeight=10, IOWeight=10 and TasksMax=64**; both descriptor limits were
  256. The probe confirmed no effective capabilities, `NoNewPrivileges`, read-only
  system mounts, protected code/config/backups and an accessible state directory.
  Actual provider/gateway networking and production load still require Gate B.
- Credentials were streamed directly through verified SSH stdin. The directory
  is `root:root` **0700**, the file `root:root` **0600**, and the service user cannot
  read it. A second transient process loaded it through systemd EnvironmentFile
  under the same hardening and private networking. Required credentials, one
  guild/channel, approved model/reasoning/timezone and the production path passed
  offline validation and `check-config`, without displaying private values.
- Final state: `LoadState=masked`, `ActiveState=inactive`, `SubState=dead`,
  `MainPID=0`, `NRestarts=0`, no start timestamp and no wants/requires links.
  `is-enabled` reports **masked**, which also prevents boot activation. All
  staging transient units were collected; no service-user processes remain.
- `/var/lib/rolfbot` and `/var/backups/rolfbot` are empty, with modes 0700 and
  the approved owners. No production SQLite was transferred, initialized,
  migrated or modified. The local authoritative file remained byte-for-byte
  unchanged, with no sidecars. No accounting backup has been made yet.
- Final HTTPS checks returned **200**, with TLS verification successful:
  apex **0.223 s**, www **0.229 s**. nginx remains healthy with its original
  master PID; website recovery service/timer remain healthy. All nine checked
  nginx/SSH/UFW/website automation/homepage hashes and the website release link
  match the baseline. No website, Cloudflare, firewall, SSH or deployment
  automation changes were made.
- No new listening ports or unexpected running services appeared. Existing TCP
  22/80/443, local resolver sockets and DHCP remain; every running service predates
  staging. No failed units remain. Available RAM was **568 MiB**, swap use unchanged
  at **27 MiB**, free disk **16 GiB**, inode use **3%**. `/opt/rolfbot` uses **192 MiB**,
  including the separate test environment. Capacity is sufficient for the proposed
  cap, subject to observing real gateway use after approval.
- **Stopped at Gate B.** Final accounting transfer, live activation, owner
  acceptance, restart checks and boot enablement have not happened. No commit or
  push was made; local deployment assets and documentation remain uncommitted.
- Remaining operational limits: real gateway resource use needs observation;
  filesystem/power-loss durability is not proven by integrity checks; off-host
  backup retention and owner confirmation of old-VM shutdown remain necessary.
