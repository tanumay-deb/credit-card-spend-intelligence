@echo off
rem cards: the one command for this project. See "cards --help".
pushd "%~dp0"
".venv\Scripts\python.exe" -m creditcard %*
set CARDS_EXIT=%ERRORLEVEL%
popd
exit /b %CARDS_EXIT%
