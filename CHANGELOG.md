# Changelog

## 2026-10-18 — security hardening and one-minute scheduling

### Breaking changes

- New installations default to **`archive`**, which preserves complete files. `tombstone` requires `allow_truncate: true` or installer consent (`IT_ALLOW_TRUNCATE=1` for unattended setup).
- The `delete` mode is rejected rather than preserving the repeated-upload loop.
- State is now a single atomic **JSON schema v2** snapshot, bound to one owner and one mode. Multiple configurations need separate private state directories.
- Legacy blocklist entries are audited by asset ID. Entries whose owner/internal-library scope cannot be proven are retained in `quarantine`, with no automatic destructive action. This can reduce the set of active historical blocks; the records are not silently discarded.
- API keys and configuration must be private and owned by the execution account. Config import no longer happens before argument parsing, so `--help` works without credentials.

### Security and recovery

- Verify the current API user at the start of every run. Exclude partner assets and external libraries from both capture and actions.
- Restrict filesystem operations to the configured prefix and known original-file layout. Walk directories by file descriptor with `O_NOFOLLOW`; reject symlinks, special files, and hardlinks.
- Check original SHA1, current asset identity/visibility, file size, timestamps, and link count before truncating. These checks reduce risk but cannot create a transaction across Immich and the filesystem.
- Persist an operation's intent before archive/delete calls. Reconcile lost responses; do not blindly repeat accepted asynchronous DELETE requests.
- Confirm recovery with upload preflight, not an ambiguous HTTP 400 or a search that may exclude inaccessible/locked assets. Do not archive or truncate a checksum while its recovery is pending.
- Never delete a revived non-empty original. Empty Live Photos and edited assets require manual recovery to avoid deleting linked data.
- Reject API redirects, avoid inherited proxies for API calls, validate API-key header characters, and keep credentials out of installer arguments and output.
- State writes use a temporary private file, atomic replace, and fsync of the file and directory. Future filenames use JSON escaping instead of unescaped TSV records.

### Efficiency and scheduling

- Default cron interval changes from 15 minutes to **one minute**.
- A nonblocking `flock` prevents concurrent writers. Slow runs cause later invocations to skip, not accumulate.
- Search recent asset updates with a five-minute overlap; reconcile the full library and older tombstones daily.
- Keep the full trash scan to avoid losing captures through unverified update-timestamp semantics.
- Resolve exact transcode paths by owner and ID rather than globbing across users or matching filename prefixes.
- Reject repeated/non-progressing pagination and deduplicate returned IDs.

### Installer, documentation, and tests

- Keep `install.sh` as a compatible entry point; implement installation with the Python standard library in `install.py`.
- Validate a dry-run before enabling the job; quote paths, validate cron input, preserve unrelated jobs, and refuse upgrades over an active job for the same installation.
- Back up existing code/config/state privately and install files atomically. No real keys, state, filenames, deployment addresses, or private infrastructure settings are distributed.
- Document archive-only use without filesystem access, manual and unattended installation, multiple users, recovery, and remaining risks in English and Spanish.
- Add synthetic filesystem/API/installer/cron tests and CI across supported Python versions. The suite never needs a live Immich account.

## Upgrading from the original version

1. Pause all cron/systemd/manual runners and wait for active runs to finish. The old script did not have a lock, so do not assume a new lock stops an already-running old version.
2. Back up the **entire existing installation state and configuration**, as well as your Immich originals and database. Keep these backups private and outside Git.
3. Fetch the new source. Keep your existing private config and explicitly select the mode you used previously. If retaining `tombstone`, acknowledge truncation with `allow_truncate: true`; do not silently reinterpret old zero-byte tombstones as ordinary `archive` assets.
4. Run the new installer with `IT_DIR` pointing to the existing private installation and initially `IT_NO_CRON=1`. It reuses existing settings, validates ownership, makes private backups, and performs a dry-run. API keys must not be passed as command-line arguments.
5. Review the dry-run's audit and quarantine counts. A purged or inaccessible legacy ID cannot establish who owned it. Do not bulk-approve quarantined checksums merely to restore the old count. A later verified trash event can re-establish a block.
6. Validate with a disposable photo and your actual storage layout. Then rerun the installer with cron enabled. Its first normal run persists the migration; the preceding dry-run did not write it.

Do not keep two configs in the same state directory. Do not switch an existing v2 state between modes; use a separate private installation for a new mode and retain the old data for recovery.

**Rollback is not just replacing the script.** After migration, TSV files are historical and no longer receive new actions. Running the old script over stale TSV can re-block a photo you recovered. Restore a coherent code/config/state generation, account for actions since the snapshot, and do not assume an older script understands schema v2. A media backup, not a metadata rollback, is needed to recover truncated original bytes.
