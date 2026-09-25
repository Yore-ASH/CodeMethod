# CodeMethod — 一键构建 Windows 可执行程序
#
#   .\build.ps1              单文件 dist\CodeMethod.exe
#   .\build.ps1 -OneDir      目录模式 dist\CodeMethod\
#   .\build.ps1 -Tests       构建前先跑测试
#   .\build.ps1 -Clean       先清理 build\ 与 dist\
#
# 会自动使用仓库内的 .venv (如果存在)。

param(
    [switch]$OneDir,
    [switch]$Tests,
    [switch]$Clean,
    [switch]$Ico
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host "使用解释器: $python" -ForegroundColor Cyan

$args = @("build.py")
if ($OneDir) { $args += "--onedir" }
if ($Tests)  { $args += "--tests" }
if ($Clean)  { $args += "--clean" }
if ($Ico)    { $args += "--ico" }

& $python @args
exit $LASTEXITCODE
