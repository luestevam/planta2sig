@echo off
rem GeoDoc SIG - Modulo 01: inicia backend + interface em http://127.0.0.1:8000
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente Python e instalando dependencias...
  python -m venv .venv || (echo Python nao encontrado. Instale o Python 3.10+ & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
rem Opcional: agente de IA multimodal (a chave fica so no backend)
rem set AI_API_KEY=sua-chave
rem set AI_MODEL=gpt-4.1-mini
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
