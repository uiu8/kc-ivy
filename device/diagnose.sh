#!/bin/sh
# Shareable allowlisted environment report. Never prints tokens, identifiers,
# book paths/titles, catalogue rows, process arguments or mount tables.
set -u
umask 077
DB='/var/local/cc.db'
MOUNT='/mnt/us'
DIR=$(CDPATH= cd -P "$(dirname "$0")" && pwd) || exit 1
mkdir -p "$MOUNT/kc-sync/logs" || exit 1
OUT="$MOUNT/kc-sync/logs/KC环境检查-$(date +%Y%m%d-%H%M%S)-$$.txt"
(set -C; : > "$OUT") 2>/dev/null || exit 1
printf '环境检查报告：%s\n' "$OUT"
exec >> "$OUT" 2>&1
echo 'KC_ENVIRONMENT_V1 | KC 0.7.0 | kc-ivy-kpw3 soft-float | no native edits'
printf 'UTC='; date -u '+%Y-%m-%dT%H:%M:%SZ'
printf 'MACHINE='; uname -m
printf 'KERNEL='; uname -r
printf 'FIRMWARE_NUMBERS='
if [ -r /etc/prettyversion.txt ]; then tr -cd '0-9. ()\n' < /etc/prettyversion.txt; fi
printf '\n'
for cmd in sh sqlite3 curl sha256sum stat uuidgen timeout eips lipc-get-prop; do
    if command -v "$cmd" >/dev/null 2>&1; then printf 'COMMAND_%s=present\n' "$cmd"; else printf 'COMMAND_%s=missing\n' "$cmd"; fi
done
if [ -r /tmp/session_token ]; then echo 'SESSION_TOKEN=readable (content omitted)'; else echo 'SESSION_TOKEN=missing'; fi
if [ -r /lib/ld-linux.so.3 ]; then echo 'ARMEL_LOADER=present'; fi
if [ -r /lib/ld-linux-armhf.so.3 ]; then echo 'ARMHF_LOADER=present'; fi
OK=1
if [ -f "$DIR/kc-backup-check" ] && chmod u+x "$DIR/kc-backup-check" && "$DIR/kc-backup-check" --self-test; then
    echo 'BACKUP_HELPER=passed'
else echo 'BACKUP_HELPER=failed'; OK=0; fi
. "$DIR/runtime-env.sh"
if kc_env_init "$DIR" "$DB"; then
    printf 'SQLITE_VERSION='; sqlite3 -batch -bail ':memory:' 'SELECT sqlite_version();'
    # Fixed expected field names only, no user/schema-defined identifiers or SQL.
    echo 'CATALOG_REQUIRED_FIELDS=passed'
else echo 'ENVIRONMENT_READY=failed'; OK=0; fi
echo 'NATIVE_CHANGE_SERVICE=not tested by diagnostics; explicit six-step test required'
echo '此报告不包含书名、路径、设备序列号或认证令牌。诊断通过不等于收藏夹编辑验证通过。'
printf 'ENVIRONMENT_PASS=%s\nRUN_FINISHED=1\n' "$OK"
[ "$OK" -eq 1 ]
