# immich-tombstone

Keep checksums of photos you intentionally delete from [Immich](https://immich.app), so a later phone backup does not keep bringing them back.

[Español](README.es.md) · [Changes and upgrade notes](CHANGELOG.md) · [Security](SECURITY.md)

**Start with `archive` mode.** It keeps complete files. `tombstone` mode deliberately and irreversibly empties originals and known video transcodes, keeping previews. It requires explicit consent and working backups of both your library and database. There is no warranty against data loss. This project is AI-assisted and is not affiliated with Immich.

## How it works

1. Capture checksums from **your own internal assets** in the trash. External libraries and partners are excluded.
2. If a purged file is uploaded again, archive the new asset. Keeping its database record makes Immich's upload preflight report a duplicate.
3. In `tombstone` mode only, verify the original's SHA1 and the asset's current identity and visibility, then truncate the original and exact known transcode paths. Keep thumbnails and previews.
4. Unarchive an asset to undo the block. In `archive` mode its original is already intact. In `tombstone` mode an empty husk can be removed so the phone can upload again; asynchronous deletion is tracked until upload preflight confirms recovery. Empty Live Photos and edited assets require manual recovery rather than risking linked files.

This is a reactive workaround for the deleted-checksum problem discussed in [Immich #4282](https://github.com/immich-app/immich/discussions/4282), not an upload-blocking plugin or a promise that Immich will never change its API.

| Mode | Retains checksum | Original files | Filesystem access |
|---|---|---|---|
| `archive` — default | Yes, while the archived record exists | Preserved | Not required |
| `tombstone` — explicit opt-in | Yes, while the archived record exists | Truncated; previews kept | Required |

The old `delete` mode has been removed: deleting the checksum again recreates the upload loop.

## Requirements

- Linux, Python 3.10 or newer, and cron for scheduled operation. No pip dependencies and no need for Docker socket access.
- Immich v3. Read-only API checks have been exercised against v3.0.1 and v3.3.3; future versions and custom layouts still need validation. The installer rejects other major versions.
- A dedicated API key belonging to **the user whose assets you manage**, not a partner's key. It needs current-user read, metadata search, and asset read/update permissions. `tombstone` also needs asset delete and upload-preflight permissions. A full-permission key for that user is the simplest setup, but broader than necessary; use granular permissions when available. Never put the key in Git, shell command arguments, or issue reports.
- A reachable base URL, such as `http://127.0.0.1:2283` or your own HTTPS URL. Authentication proxies that redirect to a login page will not work: API redirects are rejected to protect the key. TLS verification stays enabled.
- For `tombstone`: direct, writable access to the real `UPLOAD_LOCATION`, using the standard `library/`, `upload/`, and `encoded-video/` layout. Symlinks and hardlinks are intentionally rejected. The execution account must have the necessary filesystem permissions; root is not otherwise required.

## Install

```bash
git clone https://github.com/JaTabs/immich-tombstone.git
cd immich-tombstone
sudo ./install.sh
```

The installer asks for the URL and a hidden API key, discovers and pins the owner ID, validates the API and a **dry-run**, then installs a cron job **every minute**. Default installation directory: `/opt/immich-tombstone`. Default mode: `archive`.

For an unprivileged API-only installation:

```bash
IT_DIR="$HOME/.local/share/immich-tombstone" ./install.sh
```

The shell entry point delegates to `install.py`, which uses only Python's standard library. Do not install from a pipe. Read the code first.

### Unattended settings

Pass secrets using an existing protected environment or secret manager, not a literal key in shell history. Do not run the installer with shell tracing enabled.

| Variable | Default / meaning |
|---|---|
| `IT_DIR` | `/opt/immich-tombstone` |
| `IT_API_URL` | Required for a new installation; base URL without `/api` |
| `IT_API_KEY` | Required for a new installation; kept out of process arguments and output |
| `IT_MODE` | `archive` or `tombstone`; default `archive` |
| `IT_ALLOW_TRUNCATE` | `1` explicitly permits truncation; required for unattended `tombstone` setup |
| `IT_MEDIA_ROOT` | Host path to `UPLOAD_LOCATION`; required for `tombstone` |
| `IT_MEDIA_PREFIX` | Container/API path prefix; default `/data` |
| `IT_NTFY_URL` | Optional notification topic URL; empty disables it |
| `IT_CRON_MINUTES` | `1`; accepts 1–59 and uses cron's `*/N` minute field |
| `IT_NO_CRON` | `1` leaves crontab unchanged |

An existing configuration is reused unless you override its values. **Pause existing jobs and wait for active runs before upgrading**; the installer refuses an active cron entry for the same installation. It backs up code, configuration, and state under its private `backups/` directory before replacing files. A failed validation does not enable a new cron job; inspect the private backup before manually reverting.

### Manual configuration and dry-run

Copy [`config.example.json`](config.example.json) into a private installation directory, edit it, and set permissions before running:

```bash
chmod 700 /path/to/private-installation
chmod 600 /path/to/private-installation/config.json
python3 -I immich_tombstone.py /path/to/private-installation/config.json --dry-run
```

Use real paths in place of the placeholders. The config and its directory must belong to the execution account; the directory must not be group/world-writable. A dry-run performs API reads and file checks but no API mutations, state/log writes, notifications, or truncation. It may create a private lock file.

`owner_id` can be empty in a new manual configuration: it is discovered from the key, then pinned in the first successful state snapshot. Subsequent runs reject an account mismatch. The installer also writes the discovered ID into its private config.

For a default layout, `media_prefix: "/data"` maps `/data/library/...` from the API to `media_root/library/...` on your host. A custom prefix is supported with a directory-boundary check. Arbitrary external-library paths are not supported.

Choose the mode before the first normal run. Only after reviewing a dry-run and testing with a **disposable** photo should you enable `tombstone` with both `"mode": "tombstone"` and `"allow_truncate": true`. If you already have an `archive` state, do not simply change that field: pause and keep the old installation, then use a different private directory for the new mode. It starts with the current trash and does not retroactively truncate every previously archived file. The installer creates a quoted, one-minute cron entry; API credentials are never part of it.

**Multiple users:** use a separate private directory, config, state, and cron entry for each user. Two configs in the same directory are not isolated. Do not reuse state with another user or silently switch an existing state between modes.

## Operation and recovery

- Delete to trash normally. Wait for a successful capture before purging; check `tombstone.log` for `OK`. New filenames and checksums stay in your private state, not in notification messages.
- A nonblocking `flock` prevents overlaps. If one execution is slow, the next skips rather than starting a second writer.
- Recent updates are searched with a five-minute overlap. A full reconciliation runs daily, including old tombstones and late transcodes. The full trash scan is retained to avoid relying on unverified trash-update timestamps.
- `state.json` is an atomic, owner/mode-bound snapshot. Pending operations are saved before remote mutations; a lost response is not blindly treated as success. HTTP 400 may mean missing **or inaccessible**, so it does not prove deletion.
- Unarchiving requests recovery. If the phone checked for duplicates while Immich was still deleting the husk, restart its backup after the pending operation completes. This script cannot undo a duplicate response the phone already received.
- Errors go to `tombstone.log`; the installed cron also captures startup errors in private `cron-errors.log`. Optional ntfy messages contain counts, not filenames or checksums. Manage log retention according to your environment.

For existing installations, read [the migration instructions](CHANGELOG.md#upgrading-from-the-original-version). Old TSV entries without provable ownership are retained in quarantine, not allowed to truncate files. Capturing a later, verified trash event can re-establish the block.

## Limits and safety

- Trashing and purging between captures is still a blind window. One minute reduces it; API outages and long runs can extend it.
- This does not intercept uploads. A re-upload can appear until the next successful run.
- Tombstoned videos will not play; thumbnails do not replace the original. Do not regenerate all thumbnails from zero-byte originals.
- The API and filesystem do not share a transaction. Revalidation, SHA1, regular-file/link checks, and descriptor-based path resolution reduce risk; they cannot eliminate races with Immich or an administrator modifying files concurrently.
- A crash after a remote operation or an ambiguous permission error can require manual intervention. Keep state backups and inspect the pending record; do not delete state to suppress an error.
- Keep originals and database backups independently. This tool is not a backup, cannot reconstruct truncated originals, and does not make the original authors responsible for data loss.

## Tests

```bash
python3 -I -W error::ResourceWarning -m unittest discover -s . -p 'test_*.py'
sh -n install.sh
```

Tests use synthetic data, temporary files, a local fake API, and an isolated fake crontab. They do not connect to a real Immich installation or require real credentials. CI runs them with multiple Python versions. A passing suite is not a guarantee that a future Immich release is compatible.

## License

[MIT](LICENSE).
