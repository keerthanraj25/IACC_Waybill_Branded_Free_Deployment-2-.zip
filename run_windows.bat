@echo off
cd /d "%~dp0"
if not exist .venv (py -3 -m venv .venv)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if "%IACC_SECRET_KEY%"=="" set IACC_SECRET_KEY=replace-this-with-a-long-random-secret
if "%IACC_ADMIN_PASSWORD%"=="" set IACC_ADMIN_PASSWORD=ChangeMe123!
python app.py
pause
