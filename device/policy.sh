# Sourced under run.sh's unified lock. No database mutation, backup or /change.
for POLICY_INPUT in "$ROOT/policy-inbox"/*.json; do
    [ -f "$POLICY_INPUT" ] || continue
    [ ! -f "$ROOT/state/pending.json" ] || { echo 'Resolve pending before policy changes'; exit 4; }
    for BUSINESS_INPUT in "$ROOT/inbox"/*.json; do
        [ -f "$BUSINESS_INPUT" ] || continue
        BID=$(json_file_field "$BUSINESS_INPUT" '$.job_id')
        case "$BID" in ''|*[!0-9a-fA-F-]*) exit 4 ;; esac
        [ -f "$ROOT/results/$BID.json" ] || { echo 'Policy cannot mix with business tasks'; exit 4; }
    done
    PPATH=$(sq "$POLICY_INPUT")
    PVALID=$(scalar "WITH r AS(SELECT CAST(readfile('$PPATH') AS TEXT) b) SELECT
      json_valid(b) AND json_extract(b,'$.schema')='kc-policy-request/v1'
      AND json_extract(b,'$.kind')='set_protection_policy'
      AND (SELECT count(*) FROM json_each(b))=7
      AND NOT EXISTS(SELECT 1 FROM json_tree(b) WHERE key IS NOT NULL GROUP BY parent,key HAVING count(*)>1)
      AND json_extract(b,'$.device.instance_id')=json_extract('$S_DEVICE','$.instance_id')
      AND json_extract(b,'$.device.storage_uuid') IS json_extract('$S_DEVICE','$.storage_uuid')
      AND json_extract(b,'$.device.serial_sha256') IS json_extract('$S_DEVICE','$.serial_sha256')
      AND json_type(b,'$.before_version')='integer'
      AND json_extract(b,'$.policy.version')=json_extract(b,'$.before_version')+1
      AND (SELECT count(*) FROM json_each(b,'$.policy'))=6
      AND json_type(b,'$.policy.protected_collections')='array'
      AND NOT EXISTS(SELECT 1 FROM json_each(b,'$.policy.protected_collections') WHERE type<>'text' OR length(value)=0)
      AND json_extract(b,'$.policy.max_members')=json_extract('$S_POLICY','$.max_members')
      AND json_extract(b,'$.policy.max_request_bytes')=json_extract('$S_POLICY','$.max_request_bytes')
      AND json_extract(b,'$.policy.max_creates')=json_extract('$S_POLICY','$.max_creates')
      AND json_extract(b,'$.policy.max_rename_delete')=json_extract('$S_POLICY','$.max_rename_delete') FROM r;")
    [ "$PVALID" = 1 ] || { echo 'Invalid separate protection task'; exit 4; }
    KNOWN=$(printf "SELECT NOT EXISTS(SELECT 1 FROM json_each(CAST(readfile('%s') AS TEXT),'\$.policy.protected_collections') p WHERE NOT EXISTS(SELECT 1 FROM Entries e WHERE e.p_uuid=p.value AND e.p_type='Collection'));\n" "$PPATH" | sqlite3 -batch -bail -readonly "$DB")
    [ "$KNOWN" = 1 ] || { echo 'Protection includes unknown collection'; exit 4; }
    PBODY=$(scalar "SELECT json_remove(CAST(readfile('$PPATH') AS TEXT),'$.request_digest');")
    [ "$(hash_text "$PBODY")" = "$(json_file_field "$POLICY_INPUT" '$.request_digest')" ] || exit 4
    PID=$(json_file_field "$POLICY_INPUT" '$.job_id')
    case "$PID" in ''|*[!0-9a-fA-F-]*) exit 4 ;; esac
    NEW_POLICY=$(json_file_field "$POLICY_INPUT" '$.policy')
    SAME=$(scalar "SELECT json('$S_POLICY')=json('$(sq "$NEW_POLICY")');")
    if [ "$SAME" != 1 ]; then
        [ "$(json_file_field "$POLICY_INPUT" '$.before_version')" = "$(json_file_field "$ROOT/state/policy.json" '$.version')" ] || { echo 'Protection version conflict'; exit 4; }
        [ ! -e "$ROOT/state/policy.json.partial" ] || exit 4
        printf '%s\n' "$NEW_POLICY" > "$ROOT/state/policy.json.partial"
        sync
        mv -f "$ROOT/state/policy.json.partial" "$ROOT/state/policy.json"
    fi
    [ -d "$ROOT/policy-results" ] || mkdir "$ROOT/policy-results"
    mv "$POLICY_INPUT" "$ROOT/policy-results/$PID.json"
    cleanup
    exec sh "$DIR/refresh.sh"
done
