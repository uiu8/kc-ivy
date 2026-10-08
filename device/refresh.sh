#!/bin/sh
# KC 0.6.15: complete readonly snapshot; NO /change calls from this entrypoint.
set -eu
umask 077
DB='/var/local/cc.db'
MOUNT='/mnt/us'
LOCK='/tmp/kc-sync.lock'
SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
ROOT="$MOUNT/kc-sync"
SNAP_STARTED=$(date +%s)
OWN_LOCK=0
cleanup() {
    if [ "$OWN_LOCK" -eq 1 ]; then
        rm -f "$LOCK/metadata.json" "$LOCK/driveinfo.json" "$LOCK/context.json" "$LOCK/firmware.txt" "$LOCK/pid"
        rmdir "$LOCK" 2>/dev/null || :
    fi
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$LOCK" 2>/dev/null || { echo 'KC is already running or needs interrupted-run recovery.'; exit 3; }
OWN_LOCK=1
printf '%s\n' "$$" > "$LOCK/pid"
for cmd in sqlite3 uuidgen sha256sum; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "Required tool missing: $cmd"; exit 1; }
done
[ -r "$DB" ] && [ -r "$SCRIPT_DIR/export.sql" ] || exit 1
[ -d "$ROOT" ] || mkdir "$ROOT"
# Only compact extracted rows enter TEMP. Kindle's fsp mount is not a verified
# SQLite temporary-file filesystem; use the native tmpfs, not /mnt/us.
SQLITE_TMPDIR=/tmp
export SQLITE_TMPDIR
for dir in state snapshots inbox results; do
    [ -d "$ROOT/$dir" ] || mkdir "$ROOT/$dir"
done
escape_sql() { printf '%s' "$1" | sed "s/'/''/g"; }
hash_file() { sha256sum "$1" | cut -d ' ' -f 1; }
S_ROOT=$(escape_sql "$ROOT")
S_MOUNT=$(escape_sql "$MOUNT")
S_LOCK=$(escape_sql "$LOCK")
# A truncated prior state is not a fresh install.
for f in "$ROOT/state/device.json.partial" "$ROOT/state/policy.json.partial" "$ROOT/state/capabilities.json.partial"; do
    [ ! -e "$f" ] || { echo "Interrupted publication needs inspection: $f"; exit 4; }
done
if [ ! -f "$ROOT/state/device.json" ]; then
    INSTANCE=$(uuidgen)
    case "$INSTANCE" in ''|*[!0-9a-fA-F-]*) exit 1 ;; esac
    SERIAL_HASH=''
    [ ! -r /proc/usid ] || SERIAL_HASH=$(hash_file /proc/usid)
    sqlite3 -batch -bail ':memory:' "SELECT json_object('instance_id','$INSTANCE',
      'storage_uuid',CASE WHEN json_valid(CAST(readfile('$S_MOUNT/driveinfo.calibre') AS TEXT))
        THEN json_extract(CAST(readfile('$S_MOUNT/driveinfo.calibre') AS TEXT),'$.device_store_uuid') END,
      'serial_sha256',nullif('$SERIAL_HASH',''));" > "$ROOT/state/device.json.partial"
    sync
    mv "$ROOT/state/device.json.partial" "$ROOT/state/device.json"
fi
IDENTITY_OK=$(sqlite3 -batch -bail ':memory:' "SELECT CASE WHEN json_valid(CAST(readfile('$S_ROOT/state/device.json') AS TEXT)) THEN
  json_type(CAST(readfile('$S_ROOT/state/device.json') AS TEXT),'$.instance_id')='text' ELSE 0 END;")
[ "$IDENTITY_OK" = 1 ] || { echo 'Invalid device state; no snapshot published.'; exit 4; }
# Recheck observable serial/storage evidence, never silently rewrite an identity.
SERIAL_HASH=''
[ ! -r /proc/usid ] || SERIAL_HASH=$(hash_file /proc/usid)
IDENTITY_OK=$(sqlite3 -batch -bail ':memory:' "WITH x AS(SELECT CAST(readfile('$S_ROOT/state/device.json') AS TEXT) b),
 d AS(SELECT CAST(readfile('$S_MOUNT/driveinfo.calibre') AS TEXT) b)
 SELECT (json_extract(x.b,'$.serial_sha256') IS NULL OR json_extract(x.b,'$.serial_sha256')='$SERIAL_HASH')
 AND (json_extract(x.b,'$.storage_uuid') IS NULL OR CASE WHEN json_valid(d.b)
 THEN json_extract(x.b,'$.storage_uuid')=json_extract(d.b,'$.device_store_uuid') ELSE 0 END) FROM x,d;")
[ "$IDENTITY_OK" = 1 ] || { echo 'Device identity evidence changed; rebind required.'; exit 4; }
if [ ! -f "$ROOT/state/policy.json" ]; then
    printf '%s\n' '{"version":1,"protected_collections":[],"max_members":350,"max_request_bytes":262144,"max_creates":50,"max_rename_delete":10}' > "$ROOT/state/policy.json.partial"
    sync
    mv "$ROOT/state/policy.json.partial" "$ROOT/state/policy.json"
fi
MAPPING_STABLE=0
META_HASH=''
SNAP_PHASE=identity_checks
snap_phase() {
    SNAP_NOW=$(date +%s)
    printf 'SNAPSHOT_PHASE_SECONDS=%s PHASE=%s\n' "$((SNAP_NOW-SNAP_STARTED))" "$SNAP_PHASE"
    SNAP_PHASE=$1
    SNAP_STARTED=$SNAP_NOW
}
snap_phase metadata_copy_hash
DRIVE_HASH=''
if [ -r "$MOUNT/driveinfo.calibre" ]; then
    BEFORE_DRIVE=$(hash_file "$MOUNT/driveinfo.calibre")
    cp "$MOUNT/driveinfo.calibre" "$LOCK/driveinfo.json"
    DRIVE_HASH=$(hash_file "$LOCK/driveinfo.json")
else
    printf '%s\n' '{}' > "$LOCK/driveinfo.json"
fi
if [ -r "$MOUNT/metadata.calibre" ]; then
    META_BYTES=$(wc -c < "$MOUNT/metadata.calibre" | tr -d '[:space:]')
    [ "$META_BYTES" -le 33554432 ] || { echo 'Metadata exceeds the 32 MiB development export budget.'; exit 4; }
    cp "$MOUNT/metadata.calibre" "$LOCK/metadata.json"
    META_HASH=$(hash_file "$LOCK/metadata.json")
    # The private copy is hashed here; the original must match after SQL export.
    if [ -n "$DRIVE_HASH" ] \
       && [ "$BEFORE_DRIVE" = "$DRIVE_HASH" ] && [ "$(hash_file "$MOUNT/driveinfo.calibre")" = "$DRIVE_HASH" ]; then MAPPING_STABLE=1; fi
else
    printf '%s\n' '[]' > "$LOCK/metadata.json"
fi
snap_phase context_checks
if [ -r /etc/prettyversion.txt ]; then cp /etc/prettyversion.txt "$LOCK/firmware.txt";
else printf '%s' 'unknown' > "$LOCK/firmware.txt"; fi
# Only a completed explicit probe can enable native edits. A firmware or identity
# change resets to the readonly executor capability, never inherits certification.
CAP_OK=0
if [ -f "$ROOT/state/capabilities.json" ]; then
    CAP_OK=$(sqlite3 -batch -bail ':memory:' "WITH c AS(SELECT CAST(readfile('$S_ROOT/state/capabilities.json') AS TEXT) b)
      SELECT CASE WHEN json_valid(b) THEN json_extract(b,'$.firmware')=rtrim(CAST(readfile('$S_LOCK/firmware.txt') AS TEXT),char(10)||char(13))
      AND json_extract(b,'$.device.instance_id')=json_extract(CAST(readfile('$S_ROOT/state/device.json') AS TEXT),'$.instance_id') ELSE 0 END FROM c;")
fi
if [ "$CAP_OK" != 1 ]; then
    CAP_ID=$(uuidgen)
    sqlite3 -batch -bail ':memory:' "SELECT json_object('version','kc-0.6.0-$CAP_ID',
      'device',json(CAST(readfile('$S_ROOT/state/device.json') AS TEXT)),
      'firmware',rtrim(CAST(readfile('$S_LOCK/firmware.txt') AS TEXT),char(10)||char(13)),
      'verified_operations',json('[\"verify_state\"]'),'evidence','Readonly executor. Native edits require explicit device probe.');" > "$ROOT/state/capabilities.json.partial"
    sync
    mv -f "$ROOT/state/capabilities.json.partial" "$ROOT/state/capabilities.json"
fi
# Snapshot exports are reproducible. Preserve the failed export, never trust or
# promote it. State, request, result and backup partials remain protected.
PARTIAL="$ROOT/snapshots/latest.json.partial"
if [ -e "$PARTIAL" ] || [ -L "$PARTIAL" ]; then
    [ -f "$PARTIAL" ] && [ ! -L "$PARTIAL" ] || { echo 'Unexpected snapshot partial type'; exit 4; }
    [ -d "$ROOT/recovery" ] || mkdir "$ROOT/recovery"
    ARCHIVE="$ROOT/recovery/snapshot-$(date +%Y%m%d-%H%M%S)-$$.json.interrupted"
    [ ! -e "$ARCHIVE" ] || exit 4
    mv "$PARTIAL" "$ARCHIVE"
    sync
    printf 'Archived unfinished snapshot: %s\n' "$ARCHIVE"
fi
SNAPSHOT_ID=$(uuidgen)
case "$SNAPSHOT_ID" in ''|*[!0-9a-fA-F-]*) exit 1 ;; esac
sqlite3 -batch -bail ':memory:' "SELECT json_object('snapshot_id','$SNAPSHOT_ID',
 'device',json(CAST(readfile('$S_ROOT/state/device.json') AS TEXT)),
 'policy',json(CAST(readfile('$S_ROOT/state/policy.json') AS TEXT)),
 'capabilities',json_object('version',json_extract(CAST(readfile('$S_ROOT/state/capabilities.json') AS TEXT),'$.version'),
 'verified_operations',json(json_extract(CAST(readfile('$S_ROOT/state/capabilities.json') AS TEXT),'$.verified_operations')),
 'evidence',json_extract(CAST(readfile('$S_ROOT/state/capabilities.json') AS TEXT),'$.evidence')),
 'firmware',CAST(readfile('$S_LOCK/firmware.txt') AS TEXT),
 'metadata_path','$S_LOCK/metadata.json','metadata_sha256','$META_HASH','mapping_stable',$MAPPING_STABLE,
 'library_uuid',CASE WHEN json_valid(CAST(readfile('$S_LOCK/driveinfo.json') AS TEXT))
 THEN json_extract(CAST(readfile('$S_LOCK/driveinfo.json') AS TEXT),'$.last_library_uuid') END);" > "$LOCK/context.json"
snap_phase database_export
{
    printf '.timeout 1500\n'
    printf "CREATE TEMP TABLE run_context(body TEXT); INSERT INTO run_context VALUES(CAST(readfile('%s/context.json') AS TEXT));\n" "$S_LOCK"
    cat "$SCRIPT_DIR/export.sql"
} | sqlite3 -batch -bail -readonly "$DB" > "$ROOT/snapshots/latest.json.partial"
snap_phase final_hash_validation
# If mapping changed during database analysis, discard, don't publish a trusted map.
if [ "$MAPPING_STABLE" -eq 1 ] && [ "$(hash_file "$MOUNT/metadata.calibre")" != "$META_HASH" ]; then
    echo 'Calibre metadata changed during export. Retry after metadata writing stops.'
    exit 4
fi
if [ "$MAPPING_STABLE" -eq 1 ] && [ "$(hash_file "$MOUNT/driveinfo.calibre")" != "$DRIVE_HASH" ]; then
    echo 'Calibre drive information changed during export. Retry after metadata writing stops.'
    exit 4
fi
snap_phase output_validation
OUTPUT_BYTES=$(wc -c < "$ROOT/snapshots/latest.json.partial" | tr -d '[:space:]')
[ "$OUTPUT_BYTES" -le 67108864 ] || { echo 'Snapshot exceeds the protocol size budget.'; exit 4; }
VALID=$(sqlite3 -batch -bail ':memory:' "SELECT json_valid(CAST(readfile('$S_ROOT/snapshots/latest.json.partial') AS TEXT));")
[ "$VALID" = 1 ] || { echo 'Invalid snapshot; old snapshot retained.'; exit 4; }
snap_phase durable_publish
sync
mv -f "$ROOT/snapshots/latest.json.partial" "$ROOT/snapshots/latest.json"
sqlite3 -batch -bail ':memory:' "SELECT json_object('schema','kc-mtp/v1',
 'device',json(CAST(readfile('$S_ROOT/state/device.json') AS TEXT)));" > "$ROOT/state/mtp.json.partial"
mv -f "$ROOT/state/mtp.json.partial" "$ROOT/state/mtp.json"
sync
snap_phase finished
echo 'KC v2 snapshot published. Reconnect: KC++ reads it automatically.'
echo 'Refresh is readonly; no native collection edit was attempted by refresh.'
