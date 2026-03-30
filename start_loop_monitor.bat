@echo off
echo ========================================
echo   OSCAR2 Loop Monitor
echo   Advisor Relay Watchdog
echo ========================================
echo.
cd /d "C:\Users\koji3\OneDrive\デスクトップ\oscar2"
echo Starting loop monitor...
echo Press Ctrl+C to stop.
echo.
python loop_monitor.py
pause
