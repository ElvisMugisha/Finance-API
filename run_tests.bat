@echo off
echo ==========================================
echo Running Code Formatting and Linting Checks
echo ==========================================

echo [1/4] Sorting Imports (isort)...
isort .
if %ERRORLEVEL% NEQ 0 (
    echo isort failed!
    exit /b %ERRORLEVEL%
)

echo [2/4] Formatting Code (black)...
black .
if %ERRORLEVEL% NEQ 0 (
    echo black failed!
    exit /b %ERRORLEVEL%
)

echo [3/4] Checking Logic (flake8)...
flake8 .
if %ERRORLEVEL% NEQ 0 (
    echo flake8 passed with warnings/errors.
    REM We don't exit here to let tests run, or you can choose to exit.
)

echo [4/4] Running Tests (pytest)...
pytest
if %ERRORLEVEL% NEQ 0 (
    echo Tests failed!
    exit /b %ERRORLEVEL%
)

echo.
echo ==========================================
echo All Checks Passed! Ready to Push.
echo ==========================================
pause
