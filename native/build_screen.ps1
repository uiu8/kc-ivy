param([string]$ZigPath=$env:KC_ZIG)
$ErrorActionPreference='Stop'
if (-not $ZigPath) { $ZigPath=(Get-Command zig -ErrorAction Stop).Source }
$projectRoot=Split-Path $PSScriptRoot -Parent
$source=Join-Path $PSScriptRoot 'kc_screen.c'
$arm=Join-Path $projectRoot 'device/kc-screen'
$hostExe=Join-Path $PSScriptRoot 'kc-screen-test.exe'
& $ZigPath cc -Os -fno-sanitize=all -target arm-linux-musleabihf -mcpu=cortex_a8 -static $source '-Wl,-s' -o $arm
if ($LASTEXITCODE -ne 0) { throw 'ARM screen build failed' }
& $ZigPath cc -Os -target x86_64-windows-gnu $source -o $hostExe
if ($LASTEXITCODE -ne 0) { throw 'Host screen build failed' }
@{target='arm-linux-musleabihf';cpu='cortex_a8';static=$true;zig=(& $ZigPath version);source_sha256=(Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant();glyphs_sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'screen_glyphs.h')).Hash.ToLowerInvariant();arm_sha256=(Get-FileHash -LiteralPath $arm).Hash.ToLowerInvariant()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'screen-build.json') -Encoding utf8
