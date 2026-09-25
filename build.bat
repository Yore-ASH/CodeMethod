@echo off
REM CodeMethod - 一键构建 Windows 可执行程序
REM   build.bat            单文件 dist\CodeMethod.exe
REM   build.bat --onedir   目录模式
REM   build.bat --tests    构建前跑测试
setlocal
cd /d "%~dp0"

set PYTHON=%~dp0.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

echo 使用解释器: %PYTHON%
"%PYTHON%" build.py %*
exit /b %ERRORLEVEL%
