@echo off
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python 3.11 was not found at:
    echo %PYTHON_EXE%
    echo Please install Python 3.11.9 as required by the installation guide.
    pause
    exit /b 1
)

echo ==============================================
echo Installing project dependencies...
echo ==============================================
if not exist "%~dp0requirements-lock.txt" (
    echo ERROR: requirements-lock.txt is missing.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "%~dp0requirements-lock.txt"

if errorlevel 1 (
    echo ==============================================
    echo Installation failed. Review the error above.
    echo ==============================================
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m pip show openai-whisper pyModbusTCP >nul
if errorlevel 1 (
    echo ERROR: Core package verification failed.
    pause
    exit /b 1
)

echo ==============================================
echo Installation completed!
echo ==============================================
pause
