# ✅ ЧЕКЛИСТ ПЕРЕД ДЕПЛОЕМ

## Файлы (все должны быть на месте)

- [x] `bot.py` - исправлена ошибка с event loop
- [x] `requirements.txt` - все зависимости
- [x] `runtime.txt` - версия Python (python-3.11.9)
- [x] `Procfile` - команда запуска (worker: python bot.py)
- [x] `render.yaml` - конфигурация для Render (опционально)
- [ ] `.env` - локальные переменные (НЕ ЗАГРУЖАТЬ на GitHub!)
- [ ] `serviceAccountKey.json` - Firebase ключ (НЕ ЗАГРУЖАТЬ на GitHub!)

## Environment Variables (обязательно добавить в Render)

- [ ] `BOT_TOKEN` - токен твоего бота от @BotFather
- [ ] `CHAT_ID` - ID группы (начинается с `-`)
- [ ] `FIREBASE_DATABASE_URL` - URL Firebase Database
- [ ] `FIREBASE_KEY_JSON` - полный JSON из serviceAccountKey.json

## Проверка перед деплоем

- [ ] Код работает локально (запусти `python bot.py`)
- [ ] Firebase подключается
- [ ] Бот отвечает на команды
- [ ] Сообщения синхронизируются с сайтом
- [ ] `.env` и `serviceAccountKey.json` в `.gitignore`

## После деплоя на Render

- [ ] Логи показывают "✅ Firebase подключен"
- [ ] Логи показывают "✅ Бот запущен!"
- [ ] Логи показывают "✅ Firebase слушатель запущен"
- [ ] Бот отвечает на `/start` в Telegram
- [ ] Сообщения из Telegram появляются на сайте
- [ ] Сообщения с сайта приходят в Telegram

## Быстрый тест

1. Отправь `/start` боту - должен ответить
2. Напиши в группу - должно появиться на сайте
3. Напиши на сайте - должно прийти в группу
4. Попробуй `/r` - должно показать меню реакций

## Если что-то не работает

1. Проверь логи в Render Dashboard → Logs
2. Проверь все Environment Variables
3. Убедись что CHAT_ID правильный (начинается с `-`)
4. Проверь что бот добавлен в группу
5. Проверь FIREBASE_KEY_JSON (должен быть полный JSON)

## Типичные ошибки

❌ `RuntimeError: no running event loop`
✅ Исправлено в новой версии bot.py

❌ `Firebase ошибка`
✅ Проверь FIREBASE_KEY_JSON - должен содержать полный JSON

❌ `BOT_TOKEN не найден`
✅ Добавь BOT_TOKEN в Environment Variables

❌ Бот не отправляет в группу
✅ Проверь CHAT_ID и права бота в группе
