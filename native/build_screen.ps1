param([string]$ZigPath=$env:KC_ZIG)
$ErrorActionPreference='Stop'
if (-not $ZigPath) { $ZigPath=(Get-Command zig -ErrorAction Stop).Source }
$projectRoot=Split-Path $PSScriptRoot -Parent
$env:ZIG_GLOBAL_CACHE_DIR=Join-Path $projectRoot '.zig-cache-global'
$env:ZIG_LOCAL_CACHE_DIR=Join-Path $projectRoot '.zig-cache-local'
$source=Join-Path $PSScriptRoot 'kc_screen.c'
$arm=Join-Path $projectRoot 'device/kc-screen'
$hostExe=Join-Path $PSScriptRoot 'kc-screen-test.exe'
& $ZigPath cc -Os -fno-sanitize=all -target arm-linux-musleabi -mcpu=cortex_a9 -static $source '-Wl,-s' -o $arm
if ($LASTEXITCODE -ne 0) { throw 'ARM screen build failed' }
& $ZigPath cc -Os -target x86_64-windows-gnu $source -o $hostExe
if ($LASTEXITCODE -ne 0) { throw 'Host screen build failed' }
$info=@{
    target='arm-linux-musleabi';cpu='cortex_a9';static=$true;float_abi='soft';zig=(& $ZigPath version)
    zig_path=(Split-Path $ZigPath -Leaf)
    zig_sha256=(Get-FileHash -LiteralPath $ZigPath).Hash.ToLowerInvariant()
    build_script_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLowerInvariant()
    source_sha256=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant()
    glyphs_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'screen_glyphs.h')).Hash.ToLowerInvariant()
    arm_sha256=(Get-FileHash -LiteralPath $arm).Hash.ToLowerInvariant()
    host_sha256=(Get-FileHash -LiteralPath $hostExe).Hash.ToLowerInvariant()
}
$info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'screen-build.json') -Encoding utf8
$info | ConvertTo-Json
