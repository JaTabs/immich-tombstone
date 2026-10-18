# Security and data safety

## Scope

This is an unofficial, AI-assisted automation for Immich v3, not a security boundary or a backup product. `archive` is the default. `tombstone` intentionally destroys original-file contents and requires explicit consent. No test suite or review guarantees that every Immich version, custom storage layout, race, or administrator action is safe.

Use a dedicated API key for the account being managed. Keep one private state directory per account and run under the account that owns the config. Root is only needed if your chosen installation or media permissions require it. Never grant Docker socket access just to run this script.

## Protections

- Verify the current API user's ID before processing state or filesystem targets; bind state to owner and mode.
- Capture and act only on that owner's internal assets. Do not use partner or external-library trash to classify an internal copy for truncation.
- Audit legacy records and quarantine unverifiable ownership instead of trusting a checksum alone.
- Protect the config and state directory; reject symlink configs, unsafe permissions, and configurations owned by another execution account.
- Reject API redirects and untrusted proxy inheritance. HTTPS uses normal certificate verification; there is no insecure-TLS flag.
- Validate path prefixes, reject traversal, walk directory descriptors without following symlinks, and reject non-regular files or multiple hardlinks.
- Revalidate asset metadata, original SHA1, and file metadata before truncating. Use exact known transcode paths.
- Save durable operation intents and atomic state snapshots. Do not treat HTTP 400 as proof of deletion or blindly resend an accepted asynchronous DELETE.
- Serialize runs with a nonblocking lock. The installer performs a dry-run and does not overwrite another application's cron entries.

## Remaining risks

The API and filesystem are separate systems. A file or asset can change after its last validation. A privileged attacker or administrator can also change the key, state, or media. Do not restore or manipulate a tombstone concurrently with a run. In ambiguous states, stop the job, preserve its state, and investigate before making manual changes.

A phone may already have received a duplicate response while Immich was purging a husk. Restart its backup after recovery is confirmed. Do not assume empty Live Photos or edited assets can be deleted safely: linked or edited originals may need manual recovery.

Preview files cannot reconstruct a full-quality original. Do not regenerate all thumbnails from zero-byte originals. Back up both originals and the database independently and test your restore process.

## Privacy

This repository distributes no real API keys, owner IDs, private media paths, filenames, checksums, database dumps, or deployment configuration. Test IDs and files are synthetic; loopback URLs are local fixtures.

At runtime, **your** private configuration contains a key and optional notification URL; state contains owner/asset IDs, filenames, checksums, and media paths. Logs can contain asset IDs and diagnostic information. Treat configuration, state, backups, and logs as private data. Git ignores common runtime artifacts, but `.gitignore` is not a secret scanner and does not remove files already committed.

The script has no telemetry. API requests go to the server you configure. Optional ntfy requests send event counts to the notification URL you choose; leave it empty to disable them. API keys, filenames, and checksums are not included in notification bodies.

## Reporting a problem

Use the repository's private security-reporting option if GitHub offers it. If it is unavailable, open a minimal issue asking for a private reporting channel **without** posting exploit details or private data. Do not paste config files, state snapshots, database dumps, API keys, notification tokens, media files, or unredacted logs into a public issue.

For an ordinary bug report, include the software versions, selected mode, a sanitized error description, and a reproduction using synthetic or disposable data. Remove account IDs, filesystem paths, filenames, checksums, and hostnames that identify your installation.
