# 🚂 Деплой бота на Railway.app

## Шаг 1: Подготовка (на компе)

1. Создай файл `.gitignore` в папке `DP_bot`:

```
serviceAccountKey.json
.env
__pycache__/
*.pyc
token.txt
```

2. Переименуй `.env` в `.env.example` и создай новый `.env` с фейковыми данными:

```env
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
CHAT_ID=YOUR_CHAT_ID_HERE
FIREBASE_DATABASE_URL=https://dpgames-66d73-default-rtdb.europe-west1.firebasedatabase.app
```

3. Создай файл `Procfile` (без расширения) в папке `DP_bot`:

```
worker: python bot.py
```

4. Создай `railway.json` в папке `DP_bot`:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

---

## Шаг 2: Создай GitHub репозиторий

1. Открой https://github.com/new
2. Назови: `dp-telegram-bot`
3. Private
4. Create repository

5. В терминале (в папке `DP_bot`):

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/ТвойUsername/dp-telegram-bot.git
git push -u origin main
```

---

## Шаг 3: Деплой на Railway

1. Открой https://railway.app/
2. Sign up with GitHub
3. New Project → Deploy from GitHub repo
4. Выбери `dp-telegram-bot`
5. Railway автоматически начнёт деплой

---

## Шаг 4: Добавь переменные окружения

1. В Railway проекте → Settings → Variables
2. Добавь:

```
BOT_TOKEN = 8497776722:AAEUeizrcfNiuqpgfGKIApH5nzVXYUkPH-4
CHAT_ID = -1002345678901  (замени на свой!)
FIREBASE_DATABASE_URL = https://dpgames-66d73-default-rtdb.europe-west1.firebasedatabase.app
```

---

## Шаг 5: Добавь Firebase ключ

В Railway:
1. Settings → Variables
2. Добавь переменную `FIREBASE_KEY_JSON`
3. Значение: скопируй ВЕСЬ содержимое `serviceAccountKey.json`

Затем обнови `bot.py`:

```python
# БЫЛО:
cred = credentials.Certificate('serviceAccountKey.json')

# СТАЛО:
import json
firebase_key = os.getenv('FIREBASE_KEY_JSON')
if firebase_key:
    cred = credentials.Certificate(json.loads(firebase_key))
else:
    cred = credentials.Certificate('serviceAccountKey.json')
```

---

## Шаг 6: Проверь логи

1. В Railway проекте → Deployments → View Logs
2. Должно быть:
```
✅ Firebase подключен
🚀 Запуск бота
✅ Бот запущен!
```

---

## ✅ Готово!

Теперь бот работает 24/7 в облаке!

**Цена:** $0 (хватит бесплатных $5 кредитов)

---

## 🔄 Обновление бота:

```bash
git add .
git commit -m "Update"
git push
```

Railway автоматически пересоберёт и запустит!
