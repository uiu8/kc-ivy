# Sourced by run.sh. Never opens a database writable or changes its schema.
validate_backup() {
    CHECK_DB="$1"
    [ -s "$CHECK_DB" ] || { echo 'Backup is missing or empty'; return 1; }
    # One process and one quick_check per backup, including ICU schema parsing.
    # chmod affects only our shipped helper; USB copies may lose execute bits.
    [ -f "$DIR/kc-backup-check" ] || { echo 'Backup checker missing; reinstall KC 0.6.4'; return 1; }
    if [ ! -x "$DIR/kc-backup-check" ]; then
        chmod u+x "$DIR/kc-backup-check" || { echo 'Cannot enable backup checker execution'; return 1; }
    fi
    if ! "$DIR/kc-backup-check" "$CHECK_DB" \
        > "$LOCK/backup-check.out" 2> "$LOCK/backup-check.err"; then
        cat "$LOCK/backup-check.err"; echo 'Standalone backup check failed; no new POST'; return 1
    fi
    [ "$(cat "$LOCK/backup-check.out")" = "$(printf 'KC_SCHEMA_OK\nok\nKC_BACKUP_CHECK_OK')" ] || {
        echo 'Backup validation did not produce all required success markers'; return 1;
    }
}

recover_backup_partials() {
    for unfinished in "$ROOT/state/backups"/cc-*.db.partial; do
        [ -f "$unfinished" ] || continue
        finished=${unfinished%.partial}
        [ ! -e "$finished" ] || { echo 'Backup recovery target already exists'; return 1; }
        validate_backup "$unfinished" || return 1
        sync
        mv "$unfinished" "$finished" || return 1
        sync
    done
}
