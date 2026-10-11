param(
    [string]$ZigPath=$env:KC_ZIG,
    [string]$SqliteSourceRoot=$env:KC_SQLITE_SOURCE
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
if (-not $ZigPath) { $ZigPath=(Get-Command zig -ErrorAction Stop).Source }
if (-not $SqliteSourceRoot) { throw 'Specify -SqliteSourceRoot (SQLite amalgamation 3.26.0) or KC_SQLITE_SOURCE.' }
$zig=$ZigPath
$sqliteRoot=$SqliteSourceRoot
$env:ZIG_GLOBAL_CACHE_DIR=Join-Path $projectRoot '.zig-cache-global'
$env:ZIG_LOCAL_CACHE_DIR=Join-Path $projectRoot '.zig-cache-local'
$source=Join-Path $PSScriptRoot 'kc_backup_check.c'
$sqliteSource=Join-Path $sqliteRoot 'sqlite3.c'
$common=@('-Os','-fno-sanitize=all','-DSQLITE_THREADSAFE=0','-DSQLITE_OMIT_LOAD_EXTENSION=1','-DSQLITE_DEFAULT_MEMSTATUS=0','-I',$sqliteRoot,$source,$sqliteSource,'-lm')
& $zig cc -target arm-linux-musleabi -mcpu=cortex_a9 -static @common '-Wl,-s' -o (Join-Path $projectRoot 'device/kc-backup-check')
if ($LASTEXITCODE -ne 0) { throw 'ARM checker build failed' }
& $zig cc -target x86_64-windows-gnu @common -o (Join-Path $PSScriptRoot 'kc-backup-check-test.exe')
if ($LASTEXITCODE -ne 0) { throw 'Host checker build failed' }
$info=@{
    target='arm-linux-musleabi';cpu='cortex_a9';static=$true;float_abi='soft';sqlite='3.26.0';zig=(& $zig version)
    zig_path=(Split-Path $zig -Leaf);sqlite_source_root=(Split-Path $sqliteRoot -Leaf)
    zig_sha256=(Get-FileHash -LiteralPath $zig).Hash.ToLowerInvariant()
    build_script_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLowerInvariant()
    sqlite_header_sha256=(Get-FileHash -LiteralPath (Join-Path $sqliteRoot 'sqlite3.h')).Hash.ToLowerInvariant()
    source_sha256=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant()
    sqlite_sha256=(Get-FileHash -LiteralPath $sqliteSource).Hash.ToLowerInvariant()
    arm_sha256=(Get-FileHash -LiteralPath (Join-Path $projectRoot 'device/kc-backup-check')).Hash.ToLowerInvariant()
    host_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'kc-backup-check-test.exe')).Hash.ToLowerInvariant()
}
$info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'check-build.json') -Encoding utf8
$info | ConvertTo-Json
