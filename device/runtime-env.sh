# KC 0.7.0 soft-float runtime. Source with kc_env_init RUNTIME_DIR CC_DB.
# Prefer the firmware CLI when it has every SQL feature we actually need.
# The bundled static CLI supplies JSON1/readfile on older user spaces.
kc_env_init() {
    KC_RUNTIME_DIR=$1
    KC_DATABASE=$2
    KC_SQLITE=system
    KC_SQL_TEST="SELECT json_extract('{\"v\":[7]}','$.v[0]')=7 AND (SELECT count(*) FROM json_each('[1,2]'))=2 AND (SELECT count(*) FROM json_tree('[1]'))=2 AND typeof(readfile('/__kc_missing_readfile_probe__'))='null';"
    if ! KC_SQL_OK=$(command sqlite3 -batch -bail ':memory:' "$KC_SQL_TEST" 2>/dev/null) || [ "$KC_SQL_OK" != 1 ]; then
        KC_SQLITE="$KC_RUNTIME_DIR/kc-sqlite3"
        [ -f "$KC_SQLITE" ] && chmod u+x "$KC_SQLITE" || { echo 'KC_SQLITE_MISSING: 请重新安装完整的 kc-ivy-kpw3 安装包。'; return 1; }
        KC_SQL_OK=$("$KC_SQLITE" -batch -bail ':memory:' "$KC_SQL_TEST") || {
            echo 'KC_SQLITE_START_FAILED: 配套 SQLite 无法运行，请返回 KC检查运行环境报告。'; return 1;
        }
        [ "$KC_SQL_OK" = 1 ] || { echo 'KC_SQLITE_FEATURES_FAILED'; return 1; }
    fi
    sqlite3() {
        if [ "$KC_SQLITE" = system ]; then command sqlite3 "$@"; else "$KC_SQLITE" "$@"; fi
    }
    # /proc provides random UUIDs without a separately installed uuidgen utility.
    if ! command -v uuidgen >/dev/null 2>&1; then
        [ -r /proc/sys/kernel/random/uuid ] || { echo 'KC_UUID_UNAVAILABLE'; return 1; }
        uuidgen() { cat /proc/sys/kernel/random/uuid; }
    fi
    SQLITE_TMPDIR=/tmp
    export SQLITE_TMPDIR
    for KC_CMD in sha256sum stat sed cut tr wc date sync mktemp; do
        command -v "$KC_CMD" >/dev/null 2>&1 || { echo "KC_TOOL_MISSING=$KC_CMD"; return 1; }
    done
    [ -r "$KC_DATABASE" ] || { echo 'KC_DATABASE_UNREADABLE'; return 1; }
    # Compile only: no user rows, database writes, fake UUIDs or substituted counts.
    if ! sqlite3 -batch -bail -readonly "$KC_DATABASE" "SELECT p_uuid,p_type,p_titles_0_nominal,p_location,p_cdeKey,p_cdeType,p_collectionCount FROM Entries LIMIT 0; SELECT i_collection_uuid,i_member_uuid,i_member_cde_type,i_member_cde_key,i_member_is_present,i_is_sideloaded,i_order FROM Collections LIMIT 0;"; then
        echo 'KC_SCHEMA_UNSUPPORTED: 当前数据库字段不兼容，已停止；请返回环境检查报告。'
        return 1
    fi
    printf 'KC_SQLITE_PROVIDER=%s\n' "$KC_SQLITE"
}
