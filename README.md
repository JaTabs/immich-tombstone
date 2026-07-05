# immich-tombstone

Stop [Immich](https://immich.app) from re-uploading photos you permanently deleted.

> ⚠️ **Read the [warnings](#%EF%B8%8F-warnings--disclaimer) before using this.** This tool intentionally destroys file contents. The code is AI-generated. Use at your own risk.

*¿Español? Lee el [README.es.md](README.es.md).*

## The problem

When you permanently delete a photo in Immich (emptying the trash, or after the 30-day trash period), the server erases **every record** of the asset — including its SHA1 checksum. On its next backup cycle, the mobile app compares your phone's library against the server, doesn't find the photo, and **uploads it again**. Delete it again, it comes back again. Forever, as long as the photo exists on your phone.

This has been an open request since 2023 ([discussion #4282](https://github.com/immich-app/immich/discussions/4282), see also [#23897](https://github.com/immich-app/immich/issues/23897), [#22507](https://github.com/immich-app/immich/discussions/22507)). There is no native fix. On Android there is an experimental *Sync Remote Deletions* option (which deletes the photo from your phone); on iOS there is nothing.

## How this works

A small zero-dependency Python script runs from cron and keeps a **blocklist of checksums**:

1. **Capture** — every asset sitting in the trash gets its SHA1 checksum recorded. Deleting to trash = "I don't want this on the server".
2. **Control** — when an active asset appears whose checksum is blocklisted (= the app re-uploaded it), the script turns it into a **tombstone**:
   - The asset is **archived** (`visibility: archive`): it disappears from the timeline, but its checksum **stays in the database**. The app's pre-upload check now answers `duplicate` — **it stops re-uploading that file forever**.
   - The original file (and the transcoded video, usually the biggest offender) is **truncated to 0 bytes** on disk. The already-generated thumbnails are kept, so the tombstone is still recognizable in the Archive view at preview quality while using almost no space.
3. **Recovery** — want a photo back? Un-archive it in the Immich UI. The script unblocks the checksum and deletes the empty husk; your phone re-uploads the full-quality version on its next backup.
4. Restoring a photo from the trash (before purging) also unblocks it — the script only ever touches checksums that went through the trash.

Why truncation works: Immich computes the checksum **once, at upload time**, and never re-verifies file contents. Truncating instead of deleting keeps the *Repair* page free of missing-file warnings.

### Modes

| mode (config) | re-upload loop stops | disk space freed | needs filesystem access |
|---|---|---|---|
| `tombstone` (default) | ✅ | ✅ (thumbnails remain, ~150 KB/photo) | ✅ runs on the host that stores the media |
| `archive` | ✅ | ❌ (full files remain, hidden in Archive) | ❌ API only, runs anywhere |
| `delete` | ❌ (infinite loop) | ✅ | ❌ API only |

## Requirements

- Immich **v3** (tested on v3.0.1; needs the `visibility` field and the `trashedAfter` search filter)
- `python3` (any recent version, standard library only) and cron on the machine that runs the script
- For `tombstone` mode: run it on the machine that has direct access to Immich's `UPLOAD_LOCATION` (the Docker host, or a host mounting it via NFS/SMB)
- An Immich **API key** for your user (Account Settings → API Keys) with asset read/update/delete and search permissions (or full access)

## Install

```bash
git clone https://github.com/JaTabs/immich-tombstone.git
cd immich-tombstone
sudo ./install.sh
```

The installer asks for everything (API URL, key, mode, paths), validates API access, and sets up a cron job (default: every 15 minutes). Unattended install is possible via `IT_*` environment variables — see the header of [`install.sh`](install.sh).

Manual install: copy `immich_tombstone.py` somewhere, create a `config.json` next to it (see [`config.example.json`](config.example.json), `chmod 600`), and add a cron line:

```
*/15 * * * * /usr/bin/python3 /opt/immich-tombstone/immich_tombstone.py >/dev/null 2>&1
```

**Multiple Immich users**: the API key scopes everything to one user. Create one config file per user and pass its path as the first argument: `immich_tombstone.py /etc/immich-tombstone/alice.json` (state files live next to each config).

## Day-to-day

- Delete photos normally (trash → empty trash, or let the 30 days pass). Nothing else to do — re-uploads become tombstones within one cron interval.
- Tombstones live in the **Archive** view. To *really* recover one, un-archive it and wait for the next phone backup.
- The log (`tombstone.log`, next to the config) tells you everything: `BLOCKED`, `TOMBSTONED`, `UNBLOCKED`, plus a per-run `OK` summary line.
- Optional [ntfy](https://ntfy.sh) notifications when something is tombstoned or revived.

## Caveats and limitations

- **Blind window**: if you trash *and* purge a photo within one cron interval (default 15 min), its checksum is never captured and that photo will keep re-uploading. Empty the trash at least 15 minutes after deleting, or lower the interval.
- Re-uploads are visible in the timeline for up to one cron interval before being tombstoned (the approach is reactive — nothing can reject the upload itself server-side).
- Tombstoned **videos** won't play (only their thumbnail survives). Photos remain viewable at preview quality.
- Assumes Immich's standard storage layout (`library/`, `encoded-video/`, `thumbs/` under `UPLOAD_LOCATION`).
- The script talks to undocumented-ish API surface (`search/metadata` quirks change between majors — e.g. v3 silently ignores `isTrashed`). A future Immich version may need adjustments.
- If Immich ever gains a native deleted-assets registry, use that instead of this.

## ⚠️ Warnings / disclaimer

- **This tool permanently and irreversibly destroys the content of files it tombstones.** That is its purpose. If it misidentifies an asset (bug, API change, race condition), that photo's full-quality content is **gone** unless it still exists on a phone or backup.
- **The author is not responsible for any data loss.** No warranty of any kind. Use with caution, at your own risk, and only if you understand what it does. Test it with a throwaway photo first (upload → trash → empty trash → let it re-upload → watch the log).
- **This code was generated by an AI** (Claude) under human direction and tested against a single real-world instance (Immich v3.0.1, ~15k assets). Review it before running it — it's ~250 lines of straightforward Python.
- Keep **backups** of your Immich library and database. You should be doing this anyway.
- Not affiliated with or endorsed by the Immich project.

## License

[MIT](LICENSE)
