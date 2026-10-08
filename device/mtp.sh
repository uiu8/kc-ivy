#!/bin/sh
# Sourced with KC's native executor lock held. No /change calls here.
# A ready marker authenticates completeness (SHA-256), not user authority.
# Existing executor validation, identity, policy and explicit probe gates remain.
mtp_publish() {
    MP_FROM=$1; MP_TO=$2
    if [ -f "$MP_TO" ]; then
        cmp -s "$MP_FROM" "$MP_TO" || { echo 'MTP task ID collision'; exit 4; }
    else
        if [ -e "$MP_TO.partial" ]; then
            cmp -s "$MP_FROM" "$MP_TO.partial" || { echo 'MTP interrupted publication differs'; exit 4; }
        else cp "$MP_FROM" "$MP_TO.partial"; fi
        cmp -s "$MP_FROM" "$MP_TO.partial" || exit 4
        sync
        mv "$MP_TO.partial" "$MP_TO"
        sync
    fi
}
mtp_receive() {
    [ -d "$ROOT/mtp-inbox" ] || return 0
    # Finish cleanup after a crash between removing the marker and payload.
    for MP_FILE in "$ROOT/mtp-inbox"/*.json; do
        [ -f "$MP_FILE" ] || continue
        MP_ID=${MP_FILE##*/}; MP_ID=${MP_ID%.json}
        [ ! -e "$ROOT/mtp-inbox/$MP_ID.ready" ] || continue
        for MP_BASE in inbox policy-inbox; do
            if [ -f "$ROOT/$MP_BASE/$MP_ID.json" ] && cmp -s "$MP_FILE" "$ROOT/$MP_BASE/$MP_ID.json"; then
                rm "$MP_FILE"; break
            fi
        done
    done
    for MP_READY in "$ROOT/mtp-inbox"/*.ready; do
        [ -f "$MP_READY" ] || continue
        MP_ID=${MP_READY##*/}; MP_ID=${MP_ID%.ready}
        printf '%s\n' "$MP_ID" | grep -Eq '^[0-9a-fA-F-]{36}$' || { echo 'Invalid MTP job name'; exit 4; }
        MP_TICKET=$(cat "$MP_READY")
        printf '%s\n' "$MP_TICKET" | grep -Eq '^[0-9a-f]{64} (edit|probe|policy)$' || { echo 'Incomplete MTP marker; retry the original transfer in KC++'; exit 4; }
        MP_HASH=${MP_TICKET%% *}; MP_MODE=${MP_TICKET#* }
        if [ "$MP_MODE" = probe ]; then
            [ "$PROBE" -eq 1 ] || { echo 'Run KC capability test -MTP for the queued probe'; exit 4; }
        else
            [ "$PROBE" -eq 0 ] || { echo 'Run KC execute -MTP for the queued task'; exit 4; }
        fi
        MP_FILE="$ROOT/mtp-inbox/$MP_ID.json"
        [ -f "$MP_FILE" ] || { echo 'MTP payload missing; retry original transfer'; exit 4; }
        [ "$(wc -c < "$MP_FILE")" -le 67108864 ] || exit 4
        cp "$MP_FILE" "$LOCK/mtp-input.json"
        [ "$(sha256sum "$LOCK/mtp-input.json" | cut -d ' ' -f 1)" = "$MP_HASH" ] || { echo 'MTP payload checksum mismatch'; exit 4; }
        MP_VALID=$(scalar "WITH r AS(SELECT CAST(readfile('$S_LOCK/mtp-input.json') AS TEXT) b)
          SELECT json_valid(b) AND json_extract(b,'$.job_id')='$MP_ID'
          AND json_extract(b,'$.device.instance_id')=json_extract('$S_DEVICE','$.instance_id')
          AND json_extract(b,'$.device.storage_uuid') IS json_extract('$S_DEVICE','$.storage_uuid')
          AND json_extract(b,'$.device.serial_sha256') IS json_extract('$S_DEVICE','$.serial_sha256')
          AND (json_extract(b,'$.schema')=CASE WHEN '$MP_MODE'='policy' THEN 'kc-policy-request/v1' ELSE 'kc-edit-request/v1' END OR ('$MP_MODE'='edit' AND json_extract(b,'$.schema')='kc-edit-request/v2')) FROM r;")
        [ "$MP_VALID" = 1 ] || { echo 'MTP identity or envelope mismatch'; exit 4; }
        MP_BASE=inbox
        [ "$MP_MODE" != policy ] || MP_BASE=policy-inbox
        mkdir -p "$ROOT/$MP_BASE"
        if [ "$MP_MODE" = probe ] && [ -f "$ROOT/state/probe.json" ]; then
            MP_OLD=$(scalar "SELECT json_extract(CAST(readfile('$S_ROOT/state/probe.json') AS TEXT),'$.job_id');")
            printf '%s\n' "$MP_OLD" | grep -Eq '^[0-9a-fA-F-]{36}$' || exit 4
            if [ "$MP_OLD" != "$MP_ID" ]; then
                MP_DONE=$(scalar "WITH r AS(SELECT CAST(readfile('$S_ROOT/results/$MP_OLD.json') AS TEXT) b)
                  SELECT json_valid(b) AND json_extract(b,'$.job_id')='$MP_OLD'
                  AND json_array_length(b,'$.operations')=6
                  AND NOT EXISTS(SELECT 1 FROM json_each(b,'$.operations') WHERE json_extract(value,'$.status')<>'confirmed') FROM r;")
                [ "$MP_DONE" = 1 ] || { echo 'Previous probe not confirmed'; exit 4; }
                mkdir -p "$ROOT/state/probe-history"
                mtp_publish "$ROOT/state/probe.json" "$ROOT/state/probe-history/$MP_OLD.json"
            fi
        fi
        mtp_publish "$LOCK/mtp-input.json" "$ROOT/$MP_BASE/$MP_ID.json"
        if [ "$MP_MODE" = probe ]; then
            printf '{"job_id":"%s"}\n' "$MP_ID" > "$LOCK/mtp-probe.json"
            cp "$LOCK/mtp-probe.json" "$ROOT/state/probe.json.partial"
            sync
            mv -f "$ROOT/state/probe.json.partial" "$ROOT/state/probe.json"
            sync
        fi
        rm "$MP_READY"
        rm "$MP_FILE"
        echo "MTP transfer verified and queued: $MP_ID ($MP_MODE)"
    done
}
mtp_receive
