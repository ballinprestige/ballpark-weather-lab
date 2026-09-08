# Runtime hosting and recovery runbook

This runbook qualifies application commands and configuration for a continuously running runtime
service. It does not provision Render, create a monitor, send notifications, upload a backup, or
publish a release. Those are operator actions after a reviewed image is available.

## Service configuration

`render.yaml` defines one Docker web service with the `1c-2g` plan, `autoDeployTrigger: off`, and
a persistent disk mounted at `/var/lib/ballpark`. Its runtime is deliberately a child directory,
`/var/lib/ballpark/runtime`, because `runtime-restore` atomically replaces its destination through a
sibling temporary directory and must never target the persistent-disk mount root itself. The Render
environment overrides the image defaults to persist mutable state in that child:

- `runtime/state/`: `runtime-state.sqlite3`, SQLite sidecars, and bounded maintenance history.
- `runtime/cache/`: durable source snapshots, private ESPN raw-response receipts, official bindings,
  normalized last-good markets, and original acquisition clocks.
- `runtime/publication/`: `catalog.sqlite3`, accepted/rollback history, immutable CAS objects, and
  private source/model input objects.

The compiled UI and `/data/*` endpoints expose only accepted public payloads, releases, and archive
records. The raw source cache, ledger, catalog, private CAS receipts, monitor ping URL, and backup
manifest are private operational data.

The checked-in 10 GB disk value is a pending capacity proposal, not a provisioned spend or retention
guarantee. A live-mount sample at
2026-09-08T18:22Z was 8.74 MB (7,810,504 B publication, 919,580 B cache, 9,763 B state), after a
2,752,264 B stopped-state sample at 09:11Z and a 7.5-hour host-sleep gap. Those observations do not
measure active-day growth. Keep every accepted object; do not prune history to fit the disk. The
reviewable initial capacity proposal is 10 GB with observed growth/headroom alerts, estimated at
$2.50/month at the recorded Render disk rate in addition to the $25/month `1c-2g` compute plan.
It is a pending operator cost decision, not a provisioned disk or spending authorization.

Keep Render's `healthCheckPath` as `/healthz`. It checks the web process is live. `/readiness` is a
separate freshness signal: it reports stale worker receipts and source failures without making a
provider outage restart the worker/server pair. `/source-health` publishes only current source and
maintenance timestamps/states, so an external monitor can detect a retained maintenance failure
without receiving private paths, errors, raw responses, bindings, or digests. The supervisor exits
only when either process exits.

Record the final immutable image identity in the release receipt. Recovery commands require an
`image@sha256:<64 lowercase hexadecimal characters>` value and reject mutable tags. The existing
image defaults are:

```text
# Image defaults for local root-mounted use:
BALLPARK_STATE_DIR=/var/lib/ballpark/state
BALLPARK_CACHE_DIR=/var/lib/ballpark/cache
BALLPARK_PUBLICATION_DIR=/var/lib/ballpark/publication
BALLPARK_HOST=0.0.0.0

# Render template overrides under the persistent-disk root:
BALLPARK_STATE_DIR=/var/lib/ballpark/runtime/state
BALLPARK_CACHE_DIR=/var/lib/ballpark/runtime/cache
BALLPARK_PUBLICATION_DIR=/var/lib/ballpark/runtime/publication
```

Render supplies `PORT`. Do not put a monitor ping URL in `render.yaml`, logs, a command line,
public release, or repository variable.

## Independent freshness and dead-man monitor

Run this command from a separate monitored scheduler, never from the Ballpark service container.
Schedule it every five minutes with a seven-minute grace period. A monitor account, alert recipient,
and secret ping URL are required before this becomes an operating detector.

```bash
export BALLPARK_MONITOR_PING_URL='https://monitor.example/secret-ping-url'
ballpark runtime-monitor \
  --url 'https://ballpark-runtime.example/' \
  --expected-date 2026-09-08 \
  --timeout-seconds 10
```

The secret URL is read only from `BALLPARK_MONITOR_PING_URL`; the command has no ping-URL argument
and never prints it. A run makes at most five bounded service GETs (`/healthz`, `/readiness`,
`/data/release.json`, `/data/data.json`, `/source-health`) and, only after all checks pass, one
bounded no-redirect HTTPS ping. Each request has a 1–30 second timeout and response bodies are
limited to 2 MiB. A monotonic deadline covers each complete request, including a slow response body.
The packaged `runtime-monitor` CLI runs those requests in an isolated child process; a parent
60-second monotonic watchdog kills that child at the whole-run deadline even when DNS or response
headers block below Python's socket timeout mechanism. Its bounded reaping step is at most 0.2 seconds,
so a stalled child cannot overlap the next scheduled run. Set `--max-run-seconds` only from 5 through
300. It requires current payload generation, latest ESPN attempt, and latest publication-maintenance
outcome to be no more than ten minutes old by default, and rejects timestamps more than five minutes
in the future. Set
`--max-publication-age-seconds`, `--max-source-attempt-age-seconds`, or
`--max-maintenance-age-seconds` only to another bounded value from 60 through 86,400.

Set `--expected-date` to the scheduler's current `America/New_York` calendar date.

The packaged image has an explicit `monitor` entrypoint mode, so it can run as a separate Render
Cron Job without accessing the web service disk. The reviewable configuration is a `*/5 * * * *`
cron schedule, `dockerCommand: monitor --timeout-seconds 10`,
`BALLPARK_RUNTIME_URL=https://<web-service>/`, and a secret
`BALLPARK_MONITOR_PING_URL=https://<receiver-secret>`. Render Cron Jobs cannot mount the web
service disk; this monitor needs only the public runtime URL and its own secret receiver URL. Render
lists a minimum $1/month Cron Job charge billed for active seconds. Provision neither the Cron Job
nor the receiver until the operator approves that account/spend boundary. The intended Healthchecks
receiver retains at most 100 check logs, about eight hours at this cadence; those receiver logs are
short-lived diagnostics, are not Render logs, and are not seven-day detector evidence.

| Result | Meaning | Restart behavior |
| --- | --- | --- |
| `unhealthy` | Liveness/readiness failed, date is stale, payload `generated_at` is stale/future, or release/payload binding is invalid. | External signal only. |
| `source_degraded` | Required weather is unavailable, latest ESPN/DraftKings attempt failed, or catalog-maintenance is failed/stale. Retained prices stay honestly labeled. | External signal only. |
| `monitor_ping_failed` | Service checks passed but the receiver did not accept a heartbeat. | External signal only. |
| `monitor_not_configured` | Service checks passed but the secret environment value is absent. | Monitoring is absent. |
| `ready` | Date, contract, release hash, readiness, and required source lanes passed; receiver accepted heartbeat. | None. |

`no_quote` is a healthy ESPN result. `/source-health` exposes latest-attempt and last-good capture
clocks/status but never raw responses, bindings, or digests. The monitor does not contact providers
or mutate runtime state, so it cannot turn a provider outage into a restart loop.
The receiver detects a missing scheduler invocation after its seven-minute grace. No receiver or
account is configured by this repository.

## Stopped-state backup

Take a backup only after an externally controlled maintenance stop. The command refuses a live
ledger lease or an active ledger/catalog writer, then holds exclusive ledger/catalog locks while it
copies. It snapshots each SQLite database through SQLite's consistent view and excludes ephemeral
WAL/SHM/journal sidecars. It copies the whole durable mount content, including all accepted history,
private source receipts, original clocks, and CAS objects; it never prunes accepted data. The command
cannot prove that another process will not write arbitrary cache files, so it is not a replacement
for the maintenance stop.

Choose an empty destination outside the mount. A same-disk directory is a rehearsal copy only; an
operator-controlled off-host destination is needed for recovery from host or disk loss.

```bash
ballpark runtime-backup --stopped \
  --mount /var/lib/ballpark/runtime \
  --destination /tmp/ballpark-2026-09-08 \
  --image-ref 'ballpark-runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

ballpark runtime-verify-backup \
  --backup /tmp/ballpark-2026-09-08 \
  --image-ref 'ballpark-runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
```

The private `BALLPARK_BACKUP_MANIFEST.json` records the immutable image digest, capture time,
catalog schema/current/rollback identities, accepted-release count, and SHA-256/size of every file.
Verification rejects missing, added, altered, duplicate, traversal, reparse-point, or hash-mismatched
entries before a restore writes anything. The transport manifest is not restored into the runtime
mount, so a recovered mount can immediately be backed up again.

When using the packaged image rather than an installed `ballpark` command, its entrypoint accepts
`backup`, `verify-backup`, and `restore` modes and forwards the documented arguments to the same
CLI. Mount the stopped source/backup/recovery directories deliberately; none of these modes contacts
providers, publishes a release, or starts the worker.

### Render maintenance route — packaged SSH prerequisite, remote access pending

Render Cron Jobs cannot mount the web disk and an ordinary stopped web service has no shell for the
copy. The image runs as `root` and sets root's shadow passwd field to the literal nonempty `NP` marker
at build time. The [OpenSSH authentication documentation](https://raw.githubusercontent.com/openssh/openssh-portable/master/sshd.8) documents `NP` as a password-authentication-disabled, public-key-compatible
field: it is neither blank nor a usable password and it does not make the account locked. Root keeps
its UID, so `/var/lib/ballpark` mount ownership and the runtime CLI are unchanged. The image adds no
SSH daemon, listener, public key, or secret. The image qualification checks `/bin/bash`, UID 0, the
exact `NP` marker without printing a password/hash, write access to a mounted runtime volume, and a
packaged stopped backup/restore rehearsal.

Render SSH key provisioning, the remote service shell, backup destination, and an actual server-only
maintenance deployment remain operator-controlled remote acceptance steps; none is configured by this
repository. Once those steps are separately approved and verified, the proposed route is two bounded
deployments of the **same immutable image**: first change
the web service `dockerCommand` from `service` to `server`, then deploy it. `server` runs HTTP only;
it does not launch the worker. Keep the platform health check on `/healthz` and pause the external
freshness monitor for the declared maintenance interval, so no readiness probe opens the SQLite
ledger during the backup window. Wait for the previous worker lease to expire and run
`runtime-backup` in the reviewed server-only service shell. The command must accept the stopped
mount; a live lease or writer lock is a failed maintenance stop, not a reason to copy anyway.

Transfer the verified private backup to the operator-selected private destination, then restore the
web service `dockerCommand: service` and redeploy the same digest. Wait for `/healthz`, `/readiness`,
release/payload hash agreement, source-health clocks, and retained archive readback before ending the
maintenance window. This is worker downtime but preserves the read-only server path during the transfer. It remains a
proposed procedure until remote SSH and the server-only deployment are independently proven. It is not
an authorization to run deployments, create a destination, or upload private receipts. A recovery
restore follows the separate empty-volume procedure below; it never overwrites the original mount.

## Restore and same-image rollback

Keep the original mount unchanged. On a new empty persistent disk mounted at `/var/lib/ballpark`, use
an absent or empty child `/var/lib/ballpark/runtime`; do not restore into `/var/lib/ballpark` itself.
Copy the verified stopped backup into the server's ephemeral `/tmp` directory, then use the same
immutable image digest:

```bash
ballpark runtime-restore --stopped \
  --backup /tmp/ballpark-2026-09-08 \
  --destination /var/lib/ballpark/runtime \
  --image-ref 'ballpark-runtime@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
```

The destination must be absent or an empty real directory on the same persistent filesystem as its
sibling temporary directory. Restore verifies the backup first, writes to that sibling, revalidates
all bytes and catalog summary, then replaces the empty child. A bad backup leaves both original mount
and recovery destination unchanged.

For a first seed, deploy `dockerCommand: server` with the Render child-path environment values above,
leave the external freshness monitor unconfigured, and wait for the old service to be absent. SCP the
verified private backup into `/tmp`, restore it into the child directory, then change back to
`dockerCommand: service`. Only configure the external monitor after `/healthz`, `/readiness`, release
binding, source-health, and archive readback succeed on that seeded runtime.

Launch the recovery service with its normal `service` entrypoint against the recovered directories
on an isolated loopback port. Check `/healthz`, `/readiness`, current data/release SHA-256, retained
archive dates, current and rollback identities, and private receipts locally. Run one controlled
next worker pass and confirm it retains rather than invents capture clocks. If recovery is rejected,
stop the recovery instance and continue from the untouched original mount/image.

Production cutover, off-host upload, hosting/disk purchase, monitor account, alert recipient, and
secret configuration require a separate operator decision.
