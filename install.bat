@echo off
chcp 65001 >nul
echo.
echo ============================================
echo   🤖 Установка DepressivePasties Bot
echo ============================================
echo.

cd /d "%~dp0"

echo [1/4] Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден!
    echo 📥 Установи Python с https://www.python.org/downloads/
    pause
    exit /b 1
)
echo ✅ Python найден

echo.
echo [2/4] Установка зависимостей...
pip install -r requirements.txt
if errorlevel 1 (
    echo ❌ Ошибка установки зависимостей
    pause
    exit /b 1
)
echo ✅ Зависимости установлены

echo.
echo [3/4] Проверка файлов...
if not exist "serviceAccountKey.json" (
    echo.
    echo ⚠️  ВАЖНО: Файл serviceAccountKey.json не найден!
    echo.
    echo 📋 Как получить этот файл:
    echo    1. Открой https://console.firebase.google.com/
    echo    2. Выбери проект dpgames-66d73
    echo    3. ⚙️  Project Settings → Service Accounts
    echo    4. Click "Generate new private key"
    echo    5. Сохрани как serviceAccountKey.json в эту папку
    echo.
    pause
)

if not exist ".env" (
    echo ❌ Файл .env не найден
    pause
    exit /b 1
)

echo.
echo [4/4] Проверка настроек...
findstr /C:"CHAT_ID=-1002345678901" .env >nul
if not errorlevel 1 (
    echo.
    echo ⚠️  ВНИМАНИЕ: CHAT_ID не настроен!
    echo.
    echo 📋 Как получить CHAT_ID:
    echo    1. Создай группу в Telegram
    echo    2. Добавь бота в группу
    echo    3. Добавь @getidsbot в группу
    echo    4. Скопируй Chat ID (вида -1002345678901^)
    echo    5. Обнови .env файл
    echo.
)

echo.
echo ============================================
echo   ✅ Установка завершена!
echo ============================================
echo.
echo 📝 Для запуска бота используй:
echo    python bot.py
echo.
echo 📖 Подробная инструкция: README.md
echo.
pause
