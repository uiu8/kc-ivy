#!/bin/sh
# KC semantic executor. cc.db is never opened writable. /change is the only writer.
set -eu
umask 077
DB='/var/local/cc.db'
MOUNT='/mnt/us'
LOCK='/tmp/kc-sync.lock'
TOKEN='/tmp/session_token'
ENDPOINT='http://127.0.0.1:9101/change'
DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
ROOT="$MOUNT/kc-sync"
PROBE=0
MODE=sync
case "${1-}" in --refresh) MODE=refresh ;; --self-test) PROBE=1; MODE=probe ;; '') ;; *) echo 'Usage: run.sh [--refresh|--self-test]'; exit 2 ;; esac
PHASE=initialization
STARTED=$(date +%s)
mkdir -p "$ROOT/logs"
REPORT="$ROOT/logs/KC运行结果-$(date +%Y%m%d-%H%M%S)-$$.txt"
(set -C; : > "$REPORT") 2>/dev/null || { echo 'Cannot create KC run report; stopped'; exit 4; }
printf 'KC 运行报告：%s\n' "$REPORT"
exec >> "$REPORT" 2>&1
printf 'KC 0.6.15 | mode=%s\n运行中；只有末尾的结果才表示本次运行已结束。\n' "$MODE"
date
# Experimental screen feedback: Chinese cards with ASCII fallback; never stop the Kindle UI/services.
SCREEN=0
SCREEN_LAST=0
SCREEN_JOBS=0
SCREEN_OK=0
SCREEN_REVIEW=0
SCREEN_JOB=''
if [ ! -e "$ROOT/state/screen-progress.off" ] && command -v eips >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
    # BusyBox timeout syntax varies. Unsupported versions remain log-only.
    if timeout -s KILL 2 sh -c ':' >/dev/null 2>&1; then SCREEN=1; fi
fi
SCREEN_CARD=0
SCREEN_DIR=''
if [ "$SCREEN" -eq 1 ] && [ -f "$DIR/kc-screen" ]; then
    if chmod u+x "$DIR/kc-screen" && SCREEN_DIR=$(mktemp -d /tmp/kc-screen.XXXXXX); then SCREEN_CARD=1; fi
fi
printf 'SCREEN_PROGRESS=%s (experimental eips; 0=log only)\n' "$SCREEN"
SCREEN_TOTAL_MS=0
SCREEN_COUNT=0
screen_clock() {
    SCREEN_UP=0.00
    if [ -r /proc/uptime ]; then read -r SCREEN_UP SCREEN_UNUSED < /proc/uptime || SCREEN_UP=0.00; fi
    SCREEN_TICKS=$(( ${SCREEN_UP%.*} * 100 + 1${SCREEN_UP#*.} - 100 ))
}
screen_cost() {
    screen_clock
    SCREEN_MS=$(( (SCREEN_TICKS-SCREEN_BEGIN) * 10 ))
    SCREEN_TOTAL_MS=$(( ${SCREEN_TOTAL_MS:-0} + SCREEN_MS ))
    SCREEN_COUNT=$(( ${SCREEN_COUNT:-0} + 1 ))
    printf 'SCREEN_CALL_MS=%s\n' "$SCREEN_MS"
}
screen_note() {
    [ "$SCREEN" -eq 1 ] || return 0
    SCREEN_NOW=$(date +%s)
    if [ "${2-}" != force ] && [ "$((SCREEN_NOW-SCREEN_LAST))" -lt 3 ]; then return 0; fi
    SCREEN_LAST=$SCREEN_NOW
    screen_clock; SCREEN_BEGIN=$SCREEN_TICKS
    # Render a fixed-size card; eips handles the firmware-specific screen update.
    if [ "${SCREEN_CARD:-0}" -eq 1 ]; then
        if SCREEN_XY=$(timeout -s KILL 2 "$DIR/kc-screen" "$SCREEN_DIR/card.png" "$1"); then
            if timeout -s KILL 2 eips -g "$SCREEN_DIR/card.png" -x "${SCREEN_XY% *}" -y "${SCREEN_XY#* }"; then
                printf 'SCREEN_CARD=%s MESSAGE=%s\n' "$SCREEN_XY" "$1"
                screen_cost
                return 0
            fi
        fi
        echo 'SCREEN_CARD_FALLBACK: centered card unavailable; using legacy text.'
        SCREEN_CARD=0
    fi
    # Unsupported devices retain the previously tested short text.
    if timeout -s KILL 2 eips 0 2 "$(printf '%-28.28s' "$1")"; then
        printf 'SCREEN_MESSAGE=%s\n' "$1"
    else
        echo 'SCREEN_PROGRESS_DISABLED: display failed or timed out; task continues.'
        SCREEN=0
    fi
    screen_cost
    return 0
}
screen_phase() {
    case "$1" in
        acquire_lock) screen_note 'KC: Checking lock' ;;
        validate_device_state|receive_mtp) screen_note 'KC: Checking tasks' ;;
        recover_backup_partials|create_backup|validate_backup) screen_note 'KC: Backing up / checking' ;;
        prepare_operation|native_submit_and_verify)
            if [ "$PROBE" -eq 1 ]; then screen_note "KC: Test step $((INDEX+1))/$COUNT"
            else screen_note "KC: Op $((INDEX+1))/$COUNT"; fi ;;
        snapshot_refresh) screen_note 'KC: Refreshing snapshot' force ;;
    esac
    return 0
}
screen_result() {
    if [ "$RUN_EXIT" -eq 5 ]; then screen_note 'KC: Snapshot failed. See PC' force
    elif [ "$RUN_EXIT" -ne 0 ]; then screen_note 'KC: Stopped. Check PC/log' force
    elif [ "$MODE" = refresh ]; then screen_note 'KC: Snapshot ready' force
    elif [ "$PROBE" -eq 1 ]; then
        if grep -q '^KC edit capability test PASSED (6/6)\.$' "$REPORT"; then
            screen_note 'KC: Test passed (6/6)' force
        else screen_note 'KC: Test NOT confirmed' force; fi
    elif [ "$SCREEN_JOBS" -eq 0 ]; then screen_note 'KC: No new tasks' force
    elif [ "$SCREEN_REVIEW" -gt 0 ]; then screen_note "KC: OK $SCREEN_OK Review $SCREEN_REVIEW" force
    else screen_note "KC: Done. OK $SCREEN_OK" force; fi
}
case "$MODE" in
    probe) screen_note 'KC: Checking edit ability' force ;;
    refresh) screen_note 'KC: Starting refresh' force ;;
    *) screen_note 'KC: Starting...' force ;;
esac
PHASE_STARTED=$STARTED
phase() {
    NOW=$(date +%s)
    printf 'PHASE_SECONDS=%s PHASE=%s\n' "$((NOW-PHASE_STARTED))" "$PHASE"
    PHASE=$1
    PHASE_STARTED=$NOW
    screen_phase "$PHASE"
}
OWN=0
cleanup() {
    if [ "$OWN" -eq 1 ]; then
        for f in "$LOCK"/*; do [ ! -f "$f" ] || rm -f "$f"; done
        rmdir "$LOCK" 2>/dev/null || :
        OWN=0
    fi
}
finish() {
    RUN_EXIT=$?
    trap - 0
    cleanup
    printf '\nEXIT_CODE=%s\nLAST_PHASE=%s\n' "$RUN_EXIT" "$PHASE"
    if [ "$RUN_EXIT" -ne 0 ]; then
        echo '本次运行未完成，请查看上方错误；不要删除待确认任务或备份。'
    elif [ "$PROBE" -eq 1 ]; then
        if grep -q '^KC edit capability test PASSED (6/6)\.$' "$REPORT"; then
            echo '编辑能力验证成功：六步全部通过。'
        else echo '本次未产生新的六步通过记录；请核对原任务结果和能力记录，不能仅凭退出码判断成功。'; fi
    elif [ "$MODE" = refresh ]; then
        echo '设备快照刷新完成。'
    else echo '本次运行结束；具体成功、冲突或跳过项目以 KC++ 接收的任务结果为准。'; fi
    phase finished
    screen_result
    ENDED=$(date +%s)
    printf 'SCREEN_TOTAL_MS=%s SCREEN_CALLS=%s\n' "$SCREEN_TOTAL_MS" "$SCREEN_COUNT"
    printf 'ELAPSED_SECONDS=%s\nRUN_FINISHED=1\n' "$((ENDED-STARTED))"
    if [ -n "$SCREEN_DIR" ]; then
        rm -f "$SCREEN_DIR/card.png" || :
        rmdir "$SCREEN_DIR" 2>/dev/null || :
    fi
    exit "$RUN_EXIT"
}
trap finish 0
# A signal never removes persistent pending. Recovery verifies, never reposts.
trap 'exit 130' INT
trap 'exit 143' TERM
if [ "$MODE" = refresh ]; then
    phase snapshot_refresh
    sh "$DIR/refresh.sh"
    exit 0
fi
phase acquire_lock
mkdir "$LOCK" 2>/dev/null || { echo 'KC is running or requires lock recovery.'; exit 3; }
OWN=1
printf '%s\n' "$$" > "$LOCK/pid"
for cmd in sqlite3 sha256sum curl stat; do command -v "$cmd" >/dev/null 2>&1 || { echo "Missing $cmd"; exit 1; }; done
phase validate_device_state
for file in device.json policy.json capabilities.json; do
    [ -f "$ROOT/state/$file" ] || { echo 'Run KC refresh first to initialize device state.'; exit 4; }
done
for sub in jobs backups; do [ -d "$ROOT/state/$sub" ] || mkdir "$ROOT/state/$sub"; done
sq() { printf '%s' "$1" | sed "s/'/''/g"; }
hash_text() { printf '%s' "$1" | sha256sum | cut -d ' ' -f 1; }
hash_state() { if [ "$1" = null ]; then printf '%s' null; else hash_text "$1"; fi; }
scalar() { printf '%s\n' "$1" | sqlite3 -batch -bail ':memory:'; }
S_ROOT=$(sq "$ROOT")
S_LOCK=$(sq "$LOCK")
. "$DIR/backup-check.sh"
POLICY=$(cat "$ROOT/state/policy.json")
CAPS=$(cat "$ROOT/state/capabilities.json")
DEVICE=$(cat "$ROOT/state/device.json")
S_POLICY=$(sq "$POLICY")
S_CAPS=$(sq "$CAPS")
S_DEVICE=$(sq "$DEVICE")
# Independent device-side guard; a PC task cannot loosen these limits.
SAFE_POLICY=$(scalar "SELECT json_valid('$S_POLICY') AND json_extract('$S_POLICY','$.max_members') BETWEEN 0 AND 350
 AND json_extract('$S_POLICY','$.max_request_bytes') BETWEEN 1 AND 262144
 AND json_extract('$S_POLICY','$.max_creates') BETWEEN 0 AND 50
 AND json_extract('$S_POLICY','$.max_rename_delete') BETWEEN 0 AND 10;")
[ "$SAFE_POLICY" = 1 ] || { echo 'Invalid device protection policy'; exit 4; }
FIRMWARE=$(cat /etc/prettyversion.txt 2>/dev/null || printf unknown)
S_FIRMWARE=$(sq "$FIRMWARE")
IDENTITY_OK=$(scalar "SELECT json_extract('$S_CAPS','$.firmware')='$S_FIRMWARE'
 AND json_extract('$S_CAPS','$.device.instance_id')=json_extract('$S_DEVICE','$.instance_id')
 AND (json_extract('$S_DEVICE','$.storage_uuid') IS NULL OR json_extract('$S_DEVICE','$.storage_uuid')=
 json_extract(CAST(readfile('$S_ROOT/../driveinfo.calibre') AS TEXT),'$.device_store_uuid'));")
[ "$IDENTITY_OK" = 1 ] || { echo 'Device or firmware changed; refresh and revalidate capabilities.'; exit 4; }
SERIAL_HASH=''
[ ! -r /proc/usid ] || SERIAL_HASH=$(sha256sum /proc/usid | cut -d ' ' -f 1)
IDENTITY_OK=$(scalar "SELECT json_extract('$S_DEVICE','$.serial_sha256') IS NULL OR json_extract('$S_DEVICE','$.serial_sha256')='$SERIAL_HASH';")
[ "$IDENTITY_OK" = 1 ] || { echo 'Device serial evidence changed'; exit 4; }
phase receive_mtp
. "$DIR/mtp.sh"
if [ "$PROBE" -eq 1 ] && [ ! -f "$ROOT/state/probe.json" ]; then
    echo 'Prepare an explicit device test in KC++ before running this entrypoint.'; exit 4
fi
MAX_BYTES=$(scalar "SELECT json_extract('$S_POLICY','$.max_request_bytes');")
MAX_NEW=$(scalar "SELECT json_extract('$S_POLICY','$.max_creates');")
MAX_EDIT=$(scalar "SELECT json_extract('$S_POLICY','$.max_rename_delete');")
BACKUP=''
CREATED=0
UPDATED=0
EDITED=0
ANOMALIES=0

json_file_field() {
    scalar "SELECT json_extract(CAST(readfile('$(sq "$1")') AS TEXT),'$2');"
}
save_progress() {
    STATUS="$1"; OBSERVED="$2"; MESSAGE="$3"
    S_PROGRESS=$(sq "$JOB_DIR/progress.json")
    if [ "$OBSERVED" = null ]; then O_SQL=NULL; else O_SQL="'$(sq "$OBSERVED")'"; fi
    scalar "SELECT json_set(CAST(readfile('$S_PROGRESS') AS TEXT),'\$[$INDEX].status','$(sq "$STATUS")',
      '\$[$INDEX].observed_digest',$O_SQL,'\$[$INDEX].message','$(sq "$MESSAGE")');" > "$JOB_DIR/progress.json.partial"
    sync
    mv -f "$JOB_DIR/progress.json.partial" "$JOB_DIR/progress.json"
}
# Resolve only exact indexed paths whose file bytes still match the submitted book.
# Bindings are persisted before the first POST and reused by pending readback.
resolve_books() {
    BINDINGS='[]'
    if [ "$(json_file_field "$JOB_DIR/request.json" '$.schema')" != kc-edit-request/v2 ]; then return; fi
    if [ -f "$JOB_DIR/book-bindings.json" ]; then
        BINDINGS=$(cat "$JOB_DIR/book-bindings.json")
        # Recompute below; an interrupted task may never silently change identities.
        SAVED_BINDINGS=$BINDINGS
    else
        SAVED_BINDINGS=''
    fi
    BINDINGS='[]';INDEX_WAIT=0
    scalar "SELECT json_extract(value,'$.alias')||char(10)||json_extract(value,'$.size')||char(10)||json_extract(value,'$.sha256')||char(10)||json_extract(value,'$.location') FROM json_each(CAST(readfile('$(sq "$JOB_DIR/request.json")') AS TEXT),'$.new_books');" > "$LOCK/new-books.txt"
    while IFS= read -r alias; do
        IFS= read -r wanted_size; IFS= read -r wanted_sha; IFS= read -r path
        [ -f "$path" ] && [ ! -L "$path" ] || continue
        [ "$(stat -c '%s' "$path")" = "$wanted_size" ] || continue
        [ "$(sha256sum "$path" | cut -d ' ' -f 1)" = "$wanted_sha" ] || continue
        uid=''
        while :; do
            uid=$(sqlite3 -batch -bail -readonly "$DB" "SELECT min(p_uuid) FROM Entries WHERE p_type='Entry:Item' AND p_location='$(sq "$path")' GROUP BY p_location HAVING count(*)=1;")
            [ -z "$uid" ] || break
            [ "$INDEX_WAIT" -lt 5 ] || break
            INDEX_WAIT=$((INDEX_WAIT+1));sleep 1
        done
        [ -n "$uid" ] || continue
        BINDINGS=$(scalar "SELECT json_insert('$(sq "$BINDINGS")','\$['||json_array_length('$(sq "$BINDINGS")')||']',json_object('alias','$(sq "$alias")','uuid','$(sq "$uid")'));")
    done < "$LOCK/new-books.txt"
    if [ -n "$SAVED_BINDINGS" ]; then
        [ "$SAVED_BINDINGS" = "$BINDINGS" ] || { echo 'New-book identity changed during interrupted task; no new writes'; exit 4; }
        return
    fi
    printf '%s' "$BINDINGS" > "$JOB_DIR/book-bindings.json.partial"
    sync
    mv "$JOB_DIR/book-bindings.json.partial" "$JOB_DIR/book-bindings.json"
}
publish_result() {
    S_JOB_DIR=$(sq "$JOB_DIR")
    RESULT=$(scalar "WITH r AS(SELECT CAST(readfile('$S_JOB_DIR/request.json') AS TEXT) b)
      SELECT json_object('device',json(json_extract(b,'$.device')),'job_id',json_extract(b,'$.job_id'),
      'operations',json(CAST(readfile('$S_JOB_DIR/progress.json') AS TEXT)),
      'request_digest',json_extract(b,'$.request_digest'),'schema','kc-edit-result/v1','snapshot_id',NULL) FROM r;")
    if [ "$(json_file_field "$JOB_DIR/request.json" '$.schema')" = kc-edit-request/v2 ]; then
        RESULT=$(scalar "SELECT json_object('book_bindings',json(CAST(readfile('$(sq "$JOB_DIR/book-bindings.json")') AS TEXT)), 'device',json(json_extract('$(sq "$RESULT")','$.device')), 'job_id','$JOB_ID', 'operations',json(json_extract('$(sq "$RESULT")','$.operations')), 'request_digest',json_extract('$(sq "$RESULT")','$.request_digest'),'schema','kc-edit-result/v2','snapshot_id',NULL);")
    fi
    RESULT_HASH=$(hash_text "$RESULT")
    scalar "SELECT json_set('$(sq "$RESULT")','$.result_digest','$RESULT_HASH');" > "$ROOT/results/$JOB_ID.json.partial"
    sync
    mv -f "$ROOT/results/$JOB_ID.json.partial" "$ROOT/results/$JOB_ID.json"
    if [ "$SCREEN_JOB" != "$JOB_ID" ]; then
        if SCREEN_COUNTS=$(scalar "SELECT sum(json_extract(value,'$.status')='confirmed')||' '||sum(json_extract(value,'$.status')<>'confirmed') FROM json_each('$(sq "$RESULT")','$.operations');"); then
            case "$SCREEN_COUNTS" in
                *' '*) SCREEN_OK=$((SCREEN_OK+${SCREEN_COUNTS% *})); SCREEN_REVIEW=$((SCREEN_REVIEW+${SCREEN_COUNTS#* }));;
            esac
        else
            SCREEN_REVIEW=$((SCREEN_REVIEW+1))
            echo 'Screen summary unavailable; inspect receipts in KC++.'
        fi
        SCREEN_JOBS=$((SCREEN_JOBS+1))
        SCREEN_JOB=$JOB_ID
    fi
}
prepare() {
    S_OP=$(sq "$OP")
    {
        printf '.timeout 1500\n'
        printf "CREATE TEMP TABLE context(body TEXT); INSERT INTO context VALUES(json_object('operation',json('%s'),'policy',json('%s'),'book_bindings',json('%s')));\n" "$S_OP" "$S_POLICY" "$(sq "$BINDINGS")"
        cat "$DIR/prepare-edit.sql"
    } | sqlite3 -batch -bail -readonly "$DB" > "$LOCK/prepared.json"
    # Serialized states contain escaped newlines, so each value occupies one line.
    scalar "WITH p AS(SELECT CAST(readfile('$S_LOCK/prepared.json') AS TEXT) b)
      SELECT json_extract(b,'$.status')||char(10)||coalesce(json_extract(b,'$.before'),'null')||char(10)||coalesce(json_extract(b,'$.after'),'null') FROM p;" > "$LOCK/prepare-fields.txt"
    { IFS= read -r READY; IFS= read -r BEFORE_JSON; IFS= read -r AFTER_JSON; } < "$LOCK/prepare-fields.txt"
    [ -n "$BEFORE_JSON" ] || BEFORE_JSON=null
    [ -n "$AFTER_JSON" ] || AFTER_JSON=null
    BEFORE_HASH=$(hash_state "$BEFORE_JSON")
    AFTER_HASH=$(hash_state "$AFTER_JSON")
    scalar "SELECT coalesce(json_extract('$S_OP','$.before'),'null')||char(10)||coalesce(json_extract('$S_OP','$.after'),'null');" > "$LOCK/expected-fields.txt"
    { IFS= read -r EXPECT_BEFORE; IFS= read -r EXPECT_AFTER; } < "$LOCK/expected-fields.txt"
    [ "$BEFORE_HASH" = "$EXPECT_BEFORE" ] || READY=PRECONDITION_CHANGED
    [ "$AFTER_HASH" = "$EXPECT_AFTER" ] || READY=TARGET_DIGEST_MISMATCH
    scalar "SELECT json_extract(CAST(readfile('$S_LOCK/prepared.json') AS TEXT),'$.payload');" > "$LOCK/payload.json"
    BYTES=$(wc -c < "$LOCK/payload.json" | tr -d '[:space:]')
    [ "$BYTES" -le "$MAX_BYTES" ] || READY=REQUEST_TOO_LARGE
}
backup() {
    # Preserve and promote old incomplete backups only after full validation.
    # A damaged/unknown partial stops before any native POST.
    phase recover_backup_partials
    echo '备份阶段：检查遗留备份（如有）。'
    recover_backup_partials || { echo 'Resolve incomplete database backup before editing'; exit 4; }
    BACKUP="$ROOT/state/backups/cc-$(date +%Y%m%d-%H%M%S)-$$.db"
    [ ! -e "$BACKUP" ] && [ ! -e "$BACKUP.partial" ] || { echo 'Backup collision'; exit 4; }
    phase create_backup
    sqlite3 -batch -bail -readonly "$DB" '.timeout 1500' ".backup \"$BACKUP.partial\""
    phase validate_backup
    validate_backup "$BACKUP.partial" || { echo 'Backup failed validation'; exit 4; }
    echo '备份校验通过。'
    sync
    mv "$BACKUP.partial" "$BACKUP"
    # Only our own backups; no pending exists when starting a new write batch.
    ls -1t "$ROOT/state/backups"/cc-*.db | (
        n=0
        while IFS= read -r file; do n=$((n+1)); [ "$n" -le 5 ] || rm -f "$file"; done
    )
}
capture_files() {
    : > "$LOCK/files.jsonl"
    # SQL performs JSON escaping once for the whole set; shell never evaluates paths.
    # prepare-edit.sql rejects CR/LF paths before this line-oriented interchange.
    scalar "SELECT json_object('path',json_extract(j.value,'$.location'))||char(10)||json_extract(j.value,'$.location') FROM json_each(CAST(readfile('$S_LOCK/prepared.json') AS TEXT),'$.expected_books') j
      WHERE json_type(j.value,'$.location')='text' ORDER BY json_extract(j.value,'$.uuid');" > "$LOCK/paths.txt"
    while IFS= read -r record; do
        IFS= read -r path || return 1
        if [ -f "$path" ]; then size=$(stat -c '%s' "$path"); else size=-1; fi
        printf '%s,"size":%s}\n' "${record%?}" "$size" >> "$LOCK/files.jsonl"
    done < "$LOCK/paths.txt"
}
verify_files() {
    # Two lines per entry preserves spaces, tabs, quotes and shell metacharacters.
    scalar "SELECT json_extract(value,'$.size')||char(10)||json_extract(value,'$.path') FROM json_each(CAST(readfile('$S_ROOT/state/pending.json') AS TEXT),'$.files');" > "$LOCK/check-files.txt" || return 1
    FILE_OK=1
    while IFS= read -r want_size; do
        IFS= read -r path || { FILE_OK=0; return 1; }
        if [ -f "$path" ]; then size=$(stat -c '%s' "$path") || return 1; else size=-1; fi
        [ "$size" = "$want_size" ] || FILE_OK=0
    done < "$LOCK/check-files.txt"
}
verify_pending() {
    tries=0
    while [ "$tries" -le 5 ]; do
        if {
            printf '.timeout 1500\n'
            printf "CREATE TEMP TABLE context(body TEXT); INSERT INTO context VALUES(CAST(readfile('%s/state/pending.json') AS TEXT));\n" "$S_ROOT"
            cat "$DIR/verify-edit.sql"
        } | sqlite3 -batch -bail -readonly "$DB" > "$LOCK/observed.json"; then
            scalar "WITH o AS(SELECT CAST(readfile('$S_LOCK/observed.json') AS TEXT) b),
              p AS(SELECT CAST(readfile('$S_ROOT/state/pending.json') AS TEXT) b)
              SELECT coalesce(json_extract(o.b,'$.state'),'null')||char(10)||json_extract(o.b,'$.valid')||char(10)||coalesce(json_extract(p.b,'$.operation.after'),'null') FROM o,p;" > "$LOCK/observed-fields.txt" || return 1
            { IFS= read -r OBSERVED_STATE; IFS= read -r CHECK; IFS= read -r WANT; } < "$LOCK/observed-fields.txt" || return 1
            OBSERVED_HASH=$(hash_state "$OBSERVED_STATE")
            if [ "$CHECK" = 1 ] && [ "$OBSERVED_HASH" = "$WANT" ]; then
                verify_files || return 1
                [ "$FILE_OK" -ne 1 ] || return 0
            fi
        fi
        tries=$((tries+1)); [ "$tries" -gt 5 ] || sleep 1
    done
    return 1
}
if [ -f "$ROOT/state/pending.json.partial" ]; then echo 'Interrupted pending publication: inspect before proceeding.'; exit 4; fi
if [ -f "$ROOT/state/pending.json" ]; then
    JOURNAL_HASH=$(json_file_field "$ROOT/state/pending.json" '$.journal_digest')
    JOURNAL_BODY=$(scalar "SELECT json_remove(CAST(readfile('$S_ROOT/state/pending.json') AS TEXT),'$.journal_digest');")
    [ "$(hash_text "$JOURNAL_BODY")" = "$JOURNAL_HASH" ] || { echo 'Corrupt pending journal'; exit 4; }
    JOB_ID=$(json_file_field "$ROOT/state/pending.json" '$.job_id')
    case "$JOB_ID" in ''|*[!0-9a-fA-F-]*) echo 'Corrupt pending job ID'; exit 4 ;; esac
    JOB_DIR="$ROOT/state/jobs/$JOB_ID"
    INDEX=$(json_file_field "$ROOT/state/pending.json" '$.index')
    case "$INDEX" in ''|*[!0-9]*) exit 4 ;; esac
    [ -f "$JOB_DIR/request.json" ] && [ -f "$JOB_DIR/progress.json" ] || { echo 'Missing pending journal'; exit 4; }
    MATCH=$(scalar "WITH p AS(SELECT CAST(readfile('$S_ROOT/state/pending.json') AS TEXT) b),
      r AS(SELECT CAST(readfile('$(sq "$JOB_DIR/request.json")') AS TEXT) b) SELECT
      json_extract(p.b,'$.request_digest')=json_extract(r.b,'$.request_digest') AND
      json_extract(p.b,'$.operation')=json_extract(r.b,'$.operations[$INDEX]') FROM p,r;")
    [ "$MATCH" = 1 ] || { echo 'Pending does not match its original request'; exit 4; }
    if verify_pending; then
        save_progress confirmed "$OBSERVED_HASH" 'Recovered by readback; not reposted'
        mv "$ROOT/state/pending.json" "$JOB_DIR/op-$INDEX.confirmed.json"
        publish_result
    else
        save_progress pending null 'Unknown native outcome; no request replayed'
        publish_result
        echo 'Pending remains unresolved; stopped without new writes.'; exit 4
    fi
fi
. "$DIR/policy.sh"
for INPUT in "$ROOT/inbox"/*.json; do
    [ -f "$INPUT" ] || continue
    if [ "$PROBE" -eq 1 ]; then
        PROBE_ID=$(json_file_field "$ROOT/state/probe.json" '$.job_id')
        [ "$INPUT" = "$ROOT/inbox/$PROBE_ID.json" ] || continue
    elif [ -f "$ROOT/state/probe.json" ]; then
        PROBE_ID=$(json_file_field "$ROOT/state/probe.json" '$.job_id')
        [ "$INPUT" != "$ROOT/inbox/$PROBE_ID.json" ] || continue
    fi
    INPUT_BYTES=$(wc -c < "$INPUT" | tr -d '[:space:]')
    [ "$INPUT_BYTES" -le 67108864 ] || { echo 'Task too large'; exit 4; }
    cp "$INPUT" "$LOCK/input.json"
    VALID=$({ printf "CREATE TEMP TABLE input(path TEXT); INSERT INTO input VALUES('%s/input.json');\n" "$S_LOCK"; cat "$DIR/validate-request.sql"; } | sqlite3 -batch -bail ':memory:')
    [ "$VALID" = VALID ] || { echo "$VALID"; exit 4; }
    REQUEST_HASH=$(json_file_field "$LOCK/input.json" '$.request_digest')
    UNSIGNED=$(scalar "SELECT json_remove(CAST(readfile('$S_LOCK/input.json') AS TEXT),'$.request_digest');")
    [ "$(hash_text "$UNSIGNED")" = "$REQUEST_HASH" ] || { echo 'Request digest mismatch; canonical wire JSON required'; exit 4; }
    JOB_ID=$(json_file_field "$LOCK/input.json" '$.job_id')
    case "$JOB_ID" in ''|*[!0-9a-fA-F-]*) echo 'Invalid job ID'; exit 4 ;; esac
    [ "${#JOB_ID}" -eq 36 ] || exit 4
    JOB_DIR="$ROOT/state/jobs/$JOB_ID"
    # A published result closes this run (including unrun items). Only pending
    # recovery above can change it. A fresh preview is needed for remaining work.
    if [ -f "$ROOT/results/$JOB_ID.json" ] && [ -f "$JOB_DIR/request.json" ]; then
        [ "$(json_file_field "$JOB_DIR/request.json" '$.request_digest')" = "$REQUEST_HASH" ] || { echo 'Reused completed job ID'; exit 4; }
        continue
    fi
    BOUND=$(scalar "WITH r AS(SELECT CAST(readfile('$S_LOCK/input.json') AS TEXT) b) SELECT
      json_extract(b,'$.device.instance_id')=json_extract('$S_DEVICE','$.instance_id') AND
      json_extract(b,'$.device.serial_sha256') IS json_extract('$S_DEVICE','$.serial_sha256') AND
      json_extract(b,'$.device.storage_uuid') IS json_extract('$S_DEVICE','$.storage_uuid') AND
      json_extract(b,'$.policy_version')=json_extract('$S_POLICY','$.version') AND
      json_extract(b,'$.capabilities_version')=json_extract('$S_CAPS','$.version') FROM r;")
    [ "$BOUND" = 1 ] || { echo 'Wrong device, capability or protection version'; exit 4; }
    if [ "$PROBE" -eq 1 ]; then
        PROBE_OK=$(scalar "WITH r AS(SELECT CAST(readfile('$S_LOCK/input.json') AS TEXT) b)
          SELECT json_array_length(b,'$.operations')=6 AND
          (SELECT group_concat(json_extract(value,'$.kind'),',') FROM json_each(b,'$.operations'))=
          'create_collection,rename_collection,add_members,remove_members,add_members,delete_collection' AND
          (SELECT count(DISTINCT json_extract(value,'$.collection_uuid')) FROM json_each(b,'$.operations'))=1 AND
          json_extract(b,'$.operations[0].args.name') LIKE 'KC-PROBE-%' AND
          json_extract(b,'$.operations[1].args.name') LIKE 'KC-PROBE-%' AND
          json_array_length(b,'$.operations[2].args.members')=1 AND
          json_extract(b,'$.operations[2].args.members')=json_extract(b,'$.operations[3].args.members') AND
          json_extract(b,'$.operations[2].args.members')=json_extract(b,'$.operations[4].args.members') FROM r;")
        [ "$PROBE_OK" = 1 ] || { echo 'Probe is not the fixed six-step isolated test'; exit 4; }
        if [ ! -d "$JOB_DIR" ]; then
            PROBE_BOOK=$(json_file_field "$LOCK/input.json" '$.operations[2].args.members[0]')
            PROBE_CID=$(json_file_field "$LOCK/input.json" '$.operations[0].collection_uuid')
            PROBE_OK=$(sqlite3 -batch -bail -readonly "$DB" "SELECT
              EXISTS(SELECT 1 FROM Entries WHERE p_uuid='$(sq "$PROBE_BOOK")' AND p_type='Entry:Item' AND p_collectionCount=0)
              AND NOT EXISTS(SELECT 1 FROM Collections WHERE i_member_uuid='$(sq "$PROBE_BOOK")')
              AND NOT EXISTS(SELECT 1 FROM Entries WHERE p_uuid='$(sq "$PROBE_CID")');")
            [ "$PROBE_OK" = 1 ] || { echo 'Probe requires an uncollected test book and a fresh collection UUID'; exit 4; }
        fi
    fi
    JOB_DIR="$ROOT/state/jobs/$JOB_ID"
    if [ -d "$JOB_DIR" ]; then
        [ -f "$JOB_DIR/request.json" ] && [ -f "$JOB_DIR/progress.json" ] || { echo 'Interrupted task initialization'; exit 4; }
        [ "$(json_file_field "$JOB_DIR/request.json" '$.request_digest')" = "$REQUEST_HASH" ] || { echo 'Reused job ID with changed content'; exit 4; }
    else
        mkdir "$JOB_DIR"
        cp "$LOCK/input.json" "$JOB_DIR/request.json"
        scalar "SELECT json_group_array(json_object('message','','observed_digest',NULL,'op_id',json_extract(value,'$.op_id'),'status','not_run'))
          FROM json_each(CAST(readfile('$S_LOCK/input.json') AS TEXT),'$.operations');" > "$JOB_DIR/progress.json"
        sync
    fi
    [ ! -e "$JOB_DIR/progress.json.partial" ] || { echo 'Interrupted progress publication'; exit 4; }
    resolve_books
    COUNT=$(scalar "SELECT json_array_length(CAST(readfile('$(sq "$JOB_DIR/request.json")') AS TEXT),'$.operations');")
    INDEX=0
    while [ "$INDEX" -lt "$COUNT" ]; do
        PREVIOUS=$(json_file_field "$JOB_DIR/progress.json" "\$[$INDEX].status")
        if [ "$PREVIOUS" = confirmed ]; then INDEX=$((INDEX+1)); continue; fi
        # A terminal conflict/failed task isn't retried just because KC is run again.
        if [ "$PREVIOUS" != not_run ]; then INDEX=$((INDEX+1)); continue; fi
        OP=$(json_file_field "$JOB_DIR/request.json" "\$.operations[$INDEX]")
        S_OP=$(sq "$OP")
        MISSING=$(scalar "SELECT count(*) FROM json_each('$S_OP','$.args.members') WHERE value LIKE 'kc-new-%' AND value NOT IN(SELECT json_extract(value,'$.alias') FROM json_each('$(sq "$BINDINGS")'));")
        if [ "$MISSING" -gt 0 ]; then save_progress conflict null '新书尚未索引、路径不唯一或文件已变化；重新读取后恢复剩余操作'; INDEX=$((INDEX+1)); continue; fi
        KIND=$(scalar "SELECT json_extract('$S_OP','$.kind');")
        CAN_RUN=$(scalar "SELECT ($PROBE=1 OR EXISTS(SELECT 1 FROM json_each('$S_CAPS','$.verified_operations') WHERE value='$KIND')) AND NOT EXISTS(
          SELECT 1 FROM json_each('$S_OP','$.depends_on') d WHERE NOT EXISTS(SELECT 1 FROM json_each(CAST(readfile('$(sq "$JOB_DIR/progress.json")') AS TEXT)) p
           WHERE json_extract(p.value,'$.op_id')=d.value AND json_extract(p.value,'$.status')='confirmed')) AND
          ('$KIND'='verify_state' OR NOT EXISTS(SELECT 1 FROM json_each('$S_POLICY','$.protected_collections') WHERE value=json_extract('$S_OP','$.collection_uuid')));")
        if [ "$CAN_RUN" != 1 ]; then save_progress conflict null 'Capability, protection or dependency blocked'; INDEX=$((INDEX+1)); continue; fi
        if [ "$KIND" = create_collection ] && [ "$CREATED" -ge "$MAX_NEW" ]; then save_progress conflict null 'Single-run create budget reached'; INDEX=$((INDEX+1)); continue; fi
        case "$KIND" in rename_collection|delete_collection)
            if [ "$EDITED" -ge "$MAX_EDIT" ]; then save_progress conflict null 'Single-run rename/delete budget reached'; INDEX=$((INDEX+1)); continue; fi ;; esac
        phase prepare_operation
        printf 'OPERATION=%s INDEX=%s\n' "$KIND" "$INDEX"
        prepare
        if [ "$READY" != READY ]; then save_progress conflict null "$READY"; INDEX=$((INDEX+1)); continue; fi
        if [ "$KIND" = verify_state ]; then save_progress confirmed "$AFTER_HASH" 'Read-only state verified'; INDEX=$((INDEX+1)); continue; fi
        if [ -z "$BACKUP" ]; then
            backup
            prepare
            if [ "$READY" != READY ]; then save_progress conflict null "$READY"; INDEX=$((INDEX+1)); continue; fi
        fi
        [ -r "$TOKEN" ] || { echo 'Session token missing; no POST attempted'; exit 4; }
        AUTH=$(cat "$TOKEN")
        case "$AUTH" in ''|*"$(printf '\r')"*|*'
'*) echo 'Invalid session token'; exit 4 ;; esac
        capture_files
        # The pending document includes exact submitted payload and all count/file expectations.
        scalar "SELECT json_object('job_id','$JOB_ID','index',$INDEX,'request_digest','$REQUEST_HASH','backup','$(sq "$BACKUP")',
          'operation',json('$S_OP'),'prepared',json(CAST(readfile('$S_LOCK/prepared.json') AS TEXT)),
          'files',json('['||replace(rtrim(coalesce(CAST(readfile('$S_LOCK/files.jsonl') AS TEXT),''),char(10)),char(10),',')||']'));" > "$LOCK/pending-draft.json"
        PENDING_BODY=$(cat "$LOCK/pending-draft.json")
        PENDING_HASH=$(hash_text "$PENDING_BODY")
        scalar "SELECT json_set('$(sq "$PENDING_BODY")','$.journal_digest','$PENDING_HASH');" > "$ROOT/state/pending.json.partial"
        sync
        mv "$ROOT/state/pending.json.partial" "$ROOT/state/pending.json"
        sync
        phase native_submit_and_verify
        HTTP=$(curl --noproxy '*' --connect-timeout 3 --max-time 20 -sS -o "$LOCK/response.txt" -w '%{http_code}' \
          -H "AuthToken: $AUTH" -H 'Content-Type: application/json' --data-binary @"$LOCK/payload.json" "$ENDPOINT") || HTTP=000
        if [ "$HTTP" = 200 ]; then ANOMALIES=0; else ANOMALIES=$((ANOMALIES+1)); fi
        if verify_pending; then
            save_progress confirmed "$OBSERVED_HASH" 'Exact state, counts and existing file sizes verified'
            mv "$ROOT/state/pending.json" "$JOB_DIR/op-$INDEX.confirmed.json"
            case "$KIND" in
                create_collection) CREATED=$((CREATED+1)); [ $((CREATED%5)) -ne 0 ] || sleep 1 ;;
                rename_collection|delete_collection) EDITED=$((EDITED+1)); sleep 1 ;;
                *) UPDATED=$((UPDATED+1)); [ $((UPDATED%10)) -ne 0 ] || sleep 1 ;;
            esac
        else
            save_progress pending null 'Native result not fully verified; later writes stopped'
            publish_result; echo 'Unknown native result; rerun only performs pending verification.'; exit 4
        fi
        if [ "$ANOMALIES" -ge 2 ]; then publish_result; echo 'Repeated service anomalies; stopped'; exit 4; fi
        INDEX=$((INDEX+1))
    done
    publish_result
    if [ "$PROBE" -eq 1 ]; then
        PASSED=$(scalar "SELECT count(*)=6 AND sum(json_extract(value,'$.status')='confirmed')=6 FROM json_each(CAST(readfile('$(sq "$JOB_DIR/progress.json")') AS TEXT));")
        if [ "$PASSED" = 1 ]; then
            scalar "SELECT json_object('version','kc-0.6.4-probe-$JOB_ID','device',json('$S_DEVICE'),'firmware','$S_FIRMWARE',
              'verified_operations',json('[\"create_collection\",\"add_members\",\"remove_members\",\"rename_collection\",\"delete_collection\",\"verify_state\"]'),
              'evidence','Six-step device database/count/file-size verification passed. Probe job: $JOB_ID');" > "$ROOT/state/capabilities.json.partial"
            sync
            mv -f "$ROOT/state/capabilities.json.partial" "$ROOT/state/capabilities.json"
            echo 'KC edit capability test PASSED (6/6).'
        fi
    fi
done
cleanup
phase snapshot_refresh
if ! sh "$DIR/refresh.sh"; then
    echo 'Confirmed results are saved, but complete snapshot refresh failed. Rerun refresh.'; exit 5
fi
echo 'KC finished. Reconnect for KC++ to receive results automatically.'
