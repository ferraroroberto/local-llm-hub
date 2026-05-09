@echo off
REM ==========================================================
REM  local-llm-hub - Streamlit control panel
REM  Welcome / Server / Testing / Playground
REM ==========================================================
title Local LLM Hub - app
cd /d "%~dp0"

echo ============================================================
echo   Local LLM Hub - Streamlit control panel
echo   URL will print below (default http://localhost:8501)
echo ============================================================
echo.

.venv\Scripts\python.exe -m streamlit run app\app.py
pause
