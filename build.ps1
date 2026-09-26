# CodeMethod — 一键构建 Windows 可执行程序
#
#   .\build.ps1                 单文件 dist\CodeMethod.exe
#   .\build.ps1 -OneDir         目录版 dist\CodeMethod\
#   .\build.ps1 -All -Zip       两种都构建, 并产出 dist\CodeMethod-<版本>-win64.zip
#   .\build.ps1 -Tests -Clean   构建前跑测试并清理旧产物
#   .\build.ps1 -NoSelfTest     跳过构建后的自动自检
#
# 会自动使用仓库内的 .venv (如果存在)。

param(
    [switch]$OneDir,
    [switch]$All,
    [switch]$Zip,
    [switch]$Tests,
    [switch]$Clean,
    [switch]$Ico,
    [switch]$NoSelfTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host "使用解释器: $python" -ForegroundColor Cyan

$arguments = @("build.py")
if ($OneDir)     { $arguments += "--onedir" }
if ($All)        { $arguments += "--all" }
if ($Zip)        { $arguments += "--zip" }
if ($Tests)      { $arguments += "--tests" }
if ($Clean)      { $arguments += "--clean" }
if ($Ico)        { $arguments += "--ico" }
if ($NoSelfTest) { $arguments += "--no-selftest" }

& $python @arguments
exit $LASTEXITCODE
