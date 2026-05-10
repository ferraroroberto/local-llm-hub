@echo off
REM ==========================================================
REM  local-llm-hub - start every enabled backend in its
REM  own console window. Close each window individually or run
REM  stop_all.bat (TODO) to shut them down.
REM ==========================================================
cd /d "%~dp0.."

REM Hub now lives at the repo root as run_hub.bat (same Python under
REM the hood). Keep launching it via run_backend so this script stays
REM self-contained and behaves identically to the per-model lines.
start "Local LLM Hub - hub"             cmd /k .venv\Scripts\python.exe -m src.run_backend hub
start "Local LLM Hub - qwen"            cmd /k .venv\Scripts\python.exe -m src.run_backend qwen
start "Local LLM Hub - glm"             cmd /k .venv\Scripts\python.exe -m src.run_backend glm
start "Local LLM Hub - qwen3.5-4b"      cmd /k .venv\Scripts\python.exe -m src.run_backend qwen35_4b
start "Local LLM Hub - gemma4-e4b"      cmd /k .venv\Scripts\python.exe -m src.run_backend gemma4_e4b
start "Local LLM Hub - gemma4-26b-a4b"  cmd /k .venv\Scripts\python.exe -m src.run_backend gemma4_26b
start "Local LLM Hub - whisper"         cmd /k .venv\Scripts\python.exe -m src.run_backend whisper
start "Local LLM Hub - whisper-translate" cmd /k .venv\Scripts\python.exe -m src.run_backend whisper_translate

echo Launched hub + qwen + glm + qwen3.5-4b + gemma4-e4b + gemma4-26b-a4b + whisper + whisper-translate in separate windows.
echo (If a model is not enabled on this host its window will exit immediately.)
