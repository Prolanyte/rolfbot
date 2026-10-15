# Controlled Vultr deployment

**Status: Gate B complete; production active and enabled at boot.**
Release **`9a4500aa1cc3272471b6492c6c4ec96527ebc7dd`** started on
**16 October 2026 at 00:09:21 Europe/Oslo (15 October, 22:09:21 UTC)**.
Discord connected and command synchronization succeeded. Historical SQLite was
transferred and validated; **production SQLite is now authoritative**. Keep the
local instance stopped. Owner live acceptance passed, followed by boot enablement
and one successful controlled restart at **00:21:28 Europe/Oslo**. Final checks
completed at **00:22:13 Europe/Oslo**. No VM reboot was performed.

The owner explicitly approved both staging and cutover, confirmed the historical
VM has been inactive for years, and confirmed clean local shutdown. Final local
and target process checks passed. The historical VM needs no further investigation.
Keep a trusted SSH connection open. Scheduling, personality customization, music
and additional guilds/channels remain disabled.

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

## Paths and service layout

The production filesystem/account layout is:

| Location | Ownership/mode and purpose |
| --- | --- |
| `rolfbot` system user/group | Locked account, `/usr/sbin/nologin`, no SSH or sudo; home `/var/lib/rolfbot` |
| `/opt/rolfbot/releases/<full SHA>/` | Root-owned code and `.venv`; directories 0755, ordinary source files 0644; service cannot write |
| `/opt/rolfbot/current` | Root-managed symlink to the reviewed release |
| `/opt/rolfbot/staging/<full SHA>/` | Root-owned non-secret artifacts, manifests and separate offline test environment |
| `/var/lib/rolfbot/` | `rolfbot:rolfbot`, 0700; only persistent runtime writable directory |
| `/var/lib/rolfbot/usage.sqlite3` | `rolfbot:rolfbot`, 0600; authoritative production accounting |
| `/var/backups/rolfbot/` | `root:root`, 0700; accounting backups, never application releases |
| `/etc/rolfbot/rolfbot.env` | Directory 0700 and file 0600, root-owned; PID 1 passes settings to the service |
| `/etc/systemd/system/rolfbot.service` | Root-owned 0644 production unit; active/enabled and identical to the reviewed staging definition |
| `/opt/rolfbot/staging/<full SHA>/rolfbot.service` | Root-owned 0644 retained, validated unit definition |

The initial runtime-mask proposal was corrected before installing anything:
systemd searches `/etc/systemd/system` before `/run/systemd/system`, so a real
unit in `/etc` would override a runtime mask. A persistent `/etc` mask was the
first VM change and stayed in place throughout staging. After approved Gate B
storage checks, the mask was removed and the reviewed definition installed in
`/etc`. The retained definition remains under `/opt`.

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

Gate B and duplicate-instance confirmation were supplied explicitly by the owner.
The following procedure was authorized for this cutover; it does not authorize
another database replacement or a new live test session later.

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

After successful live checks, enable at boot with `systemctl enable rolfbot.service`
and verify `systemctl is-enabled`, then perform one controlled graceful restart,
as requested in the Gate B authorization. Preserve the same DB and verify all
accounting/cooldowns, one startup sync per guild, no replay, no lingering process
and website health. Check bounded restart settings and `NRestarts`; do not inject
paid API failures. Stop and disable the service if acceptance or restart fails.
Do not reboot the VM without separate owner authorization.

The verification record below contains the deployed SHA/date, package versions,
artifact hashes, effective limits, live results and restart evidence. Future
releases need their own acceptance record; these results describe this release.

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

## Gate A verification record

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
  Actual provider/gateway networking and production load were deferred to Gate B.
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
- **Gate A stopped at Gate B.** At that point, final accounting transfer, live activation, owner
  acceptance, restart checks and boot enablement have not happened. No commit or
  push was made; local deployment assets and documentation remain uncommitted.
- At the end of Gate A, real gateway observation and owner confirmation of old-VM
  shutdown were pending; both are addressed in Gate B below. Integrity checks do
  not prove power-loss durability, and ongoing off-host backup retention still
  needs an operational policy.

## Gate B cutover evidence — 16 October 2026 (Europe/Oslo)

The deployment checkout was clean at
`68d937ad7de2860344f89f5e64903a471d62bc37` when this task began. That existing
commit records Gate A operational files and documentation; it changes no
application source or application dependency configuration relative to the
approved production release `9a4500aa1cc3272471b6492c6c4ec96527ebc7dd`.
The approved application SHA remains the deployed release. No commit or push is
part of this cutover task.

### Why 463 local tests and 432 target tests

The deployed application revision `9a4500aa1cc3272471b6492c6c4ec96527ebc7dd`
contains **432 application tests**. They passed locally on Python **3.12.15**
(regular wheel) and **3.13.16** (editable install), and the exact committed suite
passed during Gate A on Ubuntu **3.12.3** in a separate, unprivileged test venv
with networking disabled. The production runtime venv contains no pytest.

The **463-test** local preflight additionally included **31 deployment-helper
tests** in `tests/test_deployment.py`: artifact validation, protected environment
conversion and safe accounting backup/refusal using synthetic databases. Those
operational additions were uncommitted during preflight and were subsequently
recorded in existing commit `68d937ad7de2860344f89f5e64903a471d62bc37` before
Gate B. They are not in the immutable application's 432-test suite. No previously
completed staging suite was rerun unnecessarily during cutover.

### Accounting transfer

- Final local process/open-handle checks found no running bot or process with the
  authoritative local database open. The target was inactive and masked with no
  service-user processes. The owner confirmed all historical instances stopped.
- Source: `~/.local/state/rolfbot/usage.sqlite3`; schema **2**, full integrity **OK**,
  journal mode **DELETE**, timezone **Europe/Oslo**, **6 completed attempts**,
  **2 cooldowns**, **540 input / 67 output tokens**, no provider block.
- Source SHA-256:
  `44d54def6dd138c98fb15f45c6fe48ea9fc43bc5cdd4283004e85d90beb78910`.
- SQLite backup API snapshot, local owner-only directory and mode 0600:
  `~/.local/state/rolfbot/backups/cutover-20261015T220743Z.sqlite3`.
  Root-only VM copy:
  `/var/backups/rolfbot/cutover-20261015T220743Z.sqlite3`.
- Backup and initial installed-production SHA-256:
  `3f5ff306af04ed58ebe56b2bdd80fe012ef3e6d6d2b26e7ce2ee5cae7766f9fb`.
  Every original metadata, attempt and cooldown row matched through the full-row
  fingerprint `6cfe3bfc21b0f97f8f88cc263f7bf30320c51dba7b7aebff9e518779fb78849c`.
  The backup API snapshot has a different file checksum from the original, while
  its entire logical accounting is identical. The original local file was unchanged.
- The initial production file was installed without overwriting existing state,
  owned by `rolfbot:rolfbot`, mode **0600**, and inspected successfully as that user.
  The application's `open_store(provision=False)` passed under the real systemd
  hardening and environment, with networking disabled. Its journaled write probe
  was rolled back; all accounting rows remained identical and no sidecars remained.
  That writable validation changed the physical checksum to
  `fcf4e0c09d11f9a147c348561ddfb30aa23b4e1f5c80acb232315be87c76b517`, without changing
  the logical fingerprint. No initialization, schema migration or quota/block reset occurred.
- Production is authoritative from activation onward. Keep the recovery snapshots
  protected; never restore them automatically over newer production accounting.

### Activation and initial observations

- Activation: **00:09:21 Europe/Oslo, 16 October 2026**, initial PID **112629**.
  The service became active/running with one command sync and one Discord-ready
  event, no automatic restarts and no generation before owner acceptance testing.
- Ubuntu Python **3.12.3**; OpenAI **3.31.0**, HTTPX2 **2.13.1**, discord.py **2.7.1**,
  aiohttp **3.14.5**; the same 23 pinned runtime dependencies as staging. The 54-file
  transfer inventory, source manifest, installed modules and unit were reverified.
- Protected config remains `root:root` 0600 inside a 0700 directory. Offline
  validation confirmed one approved guild/channel, production SQLite path,
  **GPT-6 Luna**, reasoning **none**, output cap **512**, personal/global daily
  caps **20/100**, cooldown **30 seconds**. Exact source retains reservation and
  dispatch protection and `max_retries=0`. No paid smoke test or billing change.
- Effective hardening and limits match the reviewed unit: memory high/max
  **192/256 MiB**, swap max **64 MiB**, CPU quota **50%**, CPU/IO weights **10**,
  tasks **64**, descriptors **256**. Initial idle measurements: **72,712 KiB RSS**,
  cgroup current **54,444,032 bytes**, peak **54,706,176 bytes**, **3 tasks**,
  **7 descriptors**, **0.949 CPU seconds**, no memory-pressure/OOM events or
  service swap. Host available memory **561 MiB**, free disk **16 GiB**.
- Before activation: both names HTTPS **200**, verified TLS; apex **0.214 s**,
  www **4.224 s** in individual samples. After activation: apex **0.157 s**,
  www **0.168 s**, both HTTPS **200**. nginx and website recovery remain healthy;
  all nine protected configuration/automation/homepage hashes match staging.
  No new TCP/UDP listeners appeared. No unrelated infrastructure changes.
- Startup application logs contained only recognized fixed events, with no
  configured credential values, conversation transcripts or error events.
- More than six minutes of observation passed with no automatic restart or
  resource-pressure event. After three live message requests, peak RSS was
  **80,260 KiB (78.4 MiB)** and cgroup peak **62,312,448 bytes (59.4 MiB)**,
  with approximately **1.211 CPU seconds** consumed since startup. Both websites
  remained HTTPS 200 (apex **0.228 s**, www **0.248 s**).
- The owner reports successful reply continuity with memory, a fresh context for
  a new mention, and no duplicate replies. The owner waited 30 seconds between
  messages and therefore observed no cooldown denial; a live denial is not
  claimed. Logs show exactly three successful generations, all accounted as
  completed. SQLite now has **9 completed attempts**, **2 cooldowns** and cumulative
  **840 input / 122 output tokens**; the original six attempts are unchanged.
  All application log messages match fixed events/metrics, with no transcript,
  configured credential value or error event.
- Read-only accounting checks verified completed reservation/dispatch timestamps,
  output counts within 512, personal/global usage **3/3** for 16 October, and stored
  cooldown deadlines exactly 30 seconds after the latest reservation. The minimum
  observed request gap was **37.745 s**. Daily quota exhaustion was not attempted;
  enforcement is also covered by the unchanged application tests. Exactly one
  Rolfbot process exists on the VM and it matches systemd's main PID.
- The owner subsequently confirmed `/rolfbot` responded, a mention in another
  chat received no response, and an ordinary unrelated message received no
  response. This completed live acceptance. Four generation events succeeded:
  **one slash request and three mention/reply requests**, with no duplicates or
  delivery/error events. No additional paid smoke or post-restart AI test ran.

### Boot enablement, restart and final state

- After owner acceptance, `systemctl enable rolfbot.service` created the expected
  `multi-user.target.wants` link. One `systemctl restart` began at
  **00:21:27 Europe/Oslo**; the new process started at **00:21:28**, replacing PID
  **112629** with **112888**. SIGTERM shutdown/cancellation events were clean;
  the old process exited and exactly one Rolfbot process remains.
- Discord reconnected with exactly one command-sync event and one ready event
  for the new invocation. Across both starts there are two of each, with four
  generation events total: no replay or duplicate request after restart.
- Before and after restart: schema **2**, integrity **OK**, **10 completed
  attempts**, **2 cooldowns**, **920 input / 137 output tokens**, no provider block.
  Full accounting fingerprint, including metadata and cooldowns, was identical:
  `d250e61f25c745b768dcc119466c4cb8d076266c8f52149842c159978ba708c1`.
  All six historical attempt rows remain identical to the cutover snapshot;
  both original cooldown users remain present with legitimate updated deadlines.
  The current local day has **4 personal / 4 global requests**. No quota was reset.
- Final service: **active/running**, **enabled**, `Result=success`,
  `NRestarts=0`. Manual restart does not increment the automatic restart counter.
  The installed unit still matches the reviewed definition and passes validation.
  Restart policy remains on-failure, 20-second delay, three starts per five minutes,
  45-second stop timeout and control-group cleanup. No reboot was performed.
- During live checks, observed RSS reached **78.4 MiB** and cgroup peak reached
  **59.7 MiB**. At the final post-restart sample, RSS was **72,640 KiB (70.9 MiB)**,
  cgroup current **54,579,200 bytes (52.1 MiB)**, peak **54,845,440 bytes**,
  CPU consumed **0.920 s**, **4 tasks**, **7 descriptors**, no service swap or
  memory high/max/OOM events. Before restart, CPU use was about **1.27 s over ten
  minutes**. Effective limits and hardening remain unchanged. Available host
  memory was **563 MiB**, swap use **27 MiB**, free disk **16 GiB**.
- Final websites: HTTPS **200**, verified TLS, apex **0.223 s**, www **0.232 s**.
  nginx's master PID remains **16603**; website recovery service/timer are healthy.
  All nine checked protected hashes and the active website release link are
  unchanged. No new listeners or failed units exist. No nginx, Cloudflare, DNS,
  TLS, UFW, SSH, website files or existing deployment automation was modified.
- All production application journal messages since activation matched fixed
  events and bounded generation metrics. No configured credentials, conversation
  transcripts, unclassified application messages or errors were found. Neither
  credential contents nor raw user/message identifiers were printed by the audits.
- Local Rolfbot remains stopped. The unchanged local database and its private
  snapshot are recovery evidence, not the current quota authority. Production
  credentials and SQLite remain mode 0600 with their approved owners.

**No acceptance blockers remain.** The owner did not test a live cooldown denial
or exhaust daily quotas; configuration, persisted deadlines, observed spacing,
accounting continuity and existing offline tests supply that evidence. Billing
configuration/project attribution remains owner-managed and was not changed or
independently inspected. Ongoing off-host backup retention and future load growth
remain operational follow-up items. Only repository documentation changed during
Gate B; no application changes, commit or push were performed.
