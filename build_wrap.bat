@echo off
cd /d C:\Progetti\IACallMinute
call build_exe.bat > build_log.txt 2>&1
echo EXITCODE=%ERRORLEVEL% >> build_log.txt
