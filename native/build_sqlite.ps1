param(
    [string]$ZigPath=$env:KC_ZIG,
    [string]$SqliteSourceRoot=$env:KC_SQLITE_SOURCE
)
$ErrorActionPreference='Stop'
if (-not $ZigPath) { $ZigPath=(Get-Command zig -ErrorAction Stop).Source }
if (-not $SqliteSourceRoot) { throw 'Specify -SqliteSourceRoot (SQLite amalgamation 3.26.0) or KC_SQLITE_SOURCE.' }
$projectRoot=Split-Path $PSScriptRoot -Parent
$env:ZIG_GLOBAL_CACHE_DIR=Join-Path $projectRoot '.zig-cache-global'
$env:ZIG_LOCAL_CACHE_DIR=Join-Path $projectRoot '.zig-cache-local'
$sqliteSource=Join-Path $SqliteSourceRoot 'sqlite3.c'
$shellSource=Join-Path $SqliteSourceRoot 'shell.c'
$header=Join-Path $SqliteSourceRoot 'sqlite3.h'
if ((Get-Content -LiteralPath $header -Raw) -notmatch '#define SQLITE_VERSION\s+"3\.26\.0"') {
    throw 'SQLite 3.26.0 sources required'
}
$generatedRoot=Join-Path $env:ZIG_LOCAL_CACHE_DIR 'kc-sqlite'
New-Item -ItemType Directory -Path $generatedRoot -Force | Out-Null
$source=Join-Path $generatedRoot 'kc_sqlite_shell.c'
# Keep the supplied amalgamation unchanged. Patch only the generated shell:
# every filename connection is read-only and receives the missing ICU guard.
$wrapper=@'
#include "sqlite3.h"
#include <stdio.h>
static int kc_icu_refused;
static int kc_refuse_compare(void *ctx, int na, const void *a,
                             int nb, const void *b) {
    (void)na; (void)a; (void)nb; (void)b;
    kc_icu_refused = 1;
    fputs("KC_SQLITE: ICU collation unavailable; comparison refused\n", stderr);
    sqlite3_interrupt((sqlite3 *)ctx);
    return 0;
}
static void kc_collation_needed(void *ctx, sqlite3 *db, int enc,
                                const char *name) {
    (void)ctx; (void)enc;
    if (sqlite3_stricmp(name, "icu") == 0)
        sqlite3_create_collation(db, name, SQLITE_UTF8, db, kc_refuse_compare);
}
static void kc_register(sqlite3 *db) {
    sqlite3_collation_needed(db, 0, kc_collation_needed);
}
'@
$shell=(Get-Content -LiteralPath $shellSource -Raw)
$shell=$shell.Replace('sqlite3_open(p->zDbFilename, &p->db);','sqlite3_open_v2(p->zDbFilename, &p->db, SQLITE_OPEN_READONLY, 0);')
$shell=$shell.Replace('globalDb = p->db;','globalDb = p->db; kc_register(p->db);')
[IO.File]::WriteAllText($source, $wrapper + "`r`n" + $shell, [Text.UTF8Encoding]::new($false))
$arm=Join-Path $projectRoot 'device/kc-sqlite3'
$hostExe=Join-Path $PSScriptRoot 'kc-sqlite3-test.exe'
$defines=@('-DSQLITE_THREADSAFE=0','-DSQLITE_ENABLE_JSON1=1','-DSQLITE_OMIT_LOAD_EXTENSION=1','-DSQLITE_DEFAULT_MEMSTATUS=0')
$common=@('-Os','-fno-sanitize=all') + $defines + @('-I',$SqliteSourceRoot,$source,$sqliteSource,'-lm')
& $ZigPath cc -target arm-linux-musleabi -mcpu=cortex_a9 -static @common '-Wl,-s' -o $arm
if ($LASTEXITCODE -ne 0) { throw 'ARM SQLite shell build failed' }
& $ZigPath cc -target x86_64-windows-gnu @common -o $hostExe
if ($LASTEXITCODE -ne 0) { throw 'Host SQLite shell build failed' }
$info=@{
    target='arm-linux-musleabi';cpu='cortex_a9';static=$true;float_abi='soft';sqlite='3.26.0';zig=(& $ZigPath version)
    zig_path=(Split-Path $ZigPath -Leaf);sqlite_source_root=(Split-Path $SqliteSourceRoot -Leaf);defines=$defines
    json1=$true;readfile=$true;load_extension=$false;missing_icu='interrupt on comparison; reject interrupted rows'
    zig_sha256=(Get-FileHash -LiteralPath $ZigPath).Hash.ToLowerInvariant()
    build_script_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLowerInvariant()
    source_sha256=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant()
    shell_sha256=(Get-FileHash -LiteralPath $shellSource).Hash.ToLowerInvariant()
    sqlite_sha256=(Get-FileHash -LiteralPath $sqliteSource).Hash.ToLowerInvariant()
    sqlite_header_sha256=(Get-FileHash -LiteralPath $header).Hash.ToLowerInvariant()
    arm_sha256=(Get-FileHash -LiteralPath $arm).Hash.ToLowerInvariant()
    host_sha256=(Get-FileHash -LiteralPath $hostExe).Hash.ToLowerInvariant()
}
$info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'sqlite-build.json') -Encoding utf8
$info | ConvertTo-Json
