@echo off
chcp 65001 >nul
echo.
echo ============================================
echo   🤖 DepressivePasties Bot
echo ============================================
echo.

cd /d "%~dp0"

echo Запуск бота...
echo.
python bot.py

if errorlevel 1 (
    echo.
    echo ❌ Бот завершился с ошибкой
    echo.
    pause
)
