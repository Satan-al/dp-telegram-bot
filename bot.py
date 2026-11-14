"""
DepressivePasties Telegram Bot
Синхронизация чата между сайтом и Telegram
"""

import os
import asyncio
import random
import string
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

from messenger_links import matches as telegram_link_matches, unlink as unlink_telegram
import firebase_admin
from firebase_admin import credentials, db

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
RAT_CHAT_ID = "-1002378701536"  # ID группы для RAT режима
FIREBASE_DATABASE_URL = os.getenv('FIREBASE_DATABASE_URL')

# Firebase инициализация
# Поддержка Railway: может работать с переменной FIREBASE_KEY_JSON или файлом
try:
    import json
    firebase_key_json = os.getenv('FIREBASE_KEY_JSON')
    
    if firebase_key_json:
        # Railway/облако - используем переменную окружения
        print("🔧 Используем Firebase ключ из переменной окружения")
        firebase_key = json.loads(firebase_key_json)
        cred = credentials.Certificate(firebase_key)
    else:
        # Локально - используем файл
        print("🔧 Используем Firebase ключ из файла")
        cred = credentials.Certificate('serviceAccountKey.json')
    
    firebase_admin.initialize_app(cred, {
        'databaseURL': FIREBASE_DATABASE_URL
    })
    print("✅ Firebase подключен")
except Exception as e:
    print(f"❌ Ошибка подключения к Firebase: {e}")
    print("📌 Проверь FIREBASE_KEY_JSON или serviceAccountKey.json")

# Путь к данным в Firebase
BASE_PATH = 'sessions/DepressivePasties'
CHAT_REF = f'{BASE_PATH}/chat'
LINKS_REF = f'{BASE_PATH}/telegram_links'
CODES_REF = f'{BASE_PATH}/link_codes'
REACTIONS_REF = f'{BASE_PATH}/reactions'
RAT_MODE_REF = f'{BASE_PATH}/rat_mode'  # Флаг RAT режима

# Эмодзи из сайта (те же 18 что на сайте)
SITE_EMOJIS = [
    '👍', '👎', '❤️', '😂', '😮', '😢', 
    '🔥', '🤡', '🤬', '🍷', '🧐', '💃',
    '🚩', '🤷‍♂️', '🙄', '💔', '🤯', '🔔'
]

# Глобальные переменные
firebase_listener = None
last_processed_message = {}
message_queue = None  # Будет создана в main()


# ============= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =============

def generate_link_code():
    """Генерирует уникальный код для привязки"""
    return 'LINK-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))


def get_link_by_site_uid(site_uid):
    """Получить привязку по UID с сайта"""
    try:
        ref = db.reference(LINKS_REF)
        links = ref.get() or {}
        for link_id, link_data in links.items():
            if link_data.get('siteUserId') == site_uid:
                return link_data
    except Exception as e:
        print(f"❌ Ошибка get_link_by_site_uid: {e}")
    return None


def get_link_by_tg_id(tg_user_id):
    """Получить привязку по Telegram ID"""
    try:
        ref = db.reference(LINKS_REF)
        links = ref.get() or {}
        for link_id, link_data in links.items():
            if telegram_link_matches(link_id, link_data, tg_user_id):
                return link_data
    except Exception as e:
        print(f"❌ Ошибка get_link_by_tg_id: {e}")
    return None


def is_rat_mode_active():
    """Проверить активен ли RAT режим"""
    try:
        ref = db.reference(RAT_MODE_REF)
        rat_data = ref.get() or {}
        return rat_data.get('active', False)
    except Exception as e:
        print(f"❌ Ошибка is_rat_mode_active: {e}")
        return False


# ============= КОМАНДЫ БОТА =============

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    welcome_text = """
🎮 **DepressivePasties Bot**

Этот бот синхронизирует чат между сайтом и Telegram!

**Команды:**
/link CODE - Привязать аккаунт с сайта
/unlink - Отвязать аккаунт
/whoami - Проверить свою привязку
/r или /reaction - Отправить реакцию
/help - Помощь

**Как привязать:**
1. Зайди на сайт и нажми "🔗 Связать Telegram"
2. Скопируй код (вида LINK-XXXX)
3. Отправь сюда: `/link LINK-XXXX`

После привязки твои сообщения будут отображаться с твоим именем и цветом с сайта! 🎨
"""
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
📖 **Помощь**

**Основные команды:**
• `/link CODE` - Привязать аккаунт
• `/unlink` - Отвязать аккаунт  
• `/whoami` - Твой статус
• `/r` или `/reaction` - Меню реакций

**Как работает:**
✅ Привязанные пользователи - сообщения идут с именем/цветом с сайта
⚪ Без привязки - сообщения идут с префиксом [TG]

**Реакции:**
Используй `/r` чтобы открыть меню с эмодзи. Можешь отправить любой эмодзи командой:
`/r 🎉` или `/reaction ❤️`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /link CODE"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажи код!\n\nИспользуй: `/link LINK-XXXX`\n\n"
            "Код можно получить на сайте, нажав кнопку '🔗 Связать Telegram'",
            parse_mode='Markdown'
        )
        return
    
    code = context.args[0].upper()
    tg_user = update.effective_user
    
    try:
        # Проверяем, не привязан ли уже этот TG аккаунт
        existing_link = get_link_by_tg_id(tg_user.id)
        if existing_link:
            await update.message.reply_text(
                f"⚠️ Твой Telegram уже привязан к аккаунту **{existing_link['siteName']}**\n\n"
                f"Сначала отвяжись командой `/unlink`",
                parse_mode='Markdown'
            )
            return
        
        # Проверяем код
        codes_ref = db.reference(CODES_REF)
        code_data = codes_ref.child(code).get()
        
        if not code_data:
            await update.message.reply_text(
                "❌ Неверный код!\n\n"
                "Проверь, правильно ли ты скопировал код с сайта."
            )
            return
        
        # Проверяем срок действия (5 минут)
        if code_data.get('expiresAt', 0) < time.time() * 1000:
            await update.message.reply_text(
                "⏰ Код истёк!\n\n"
                "Код действует только 5 минут. Сгенерируй новый на сайте."
            )
            # Удаляем устаревший код
            codes_ref.child(code).delete()
            return
        
        # Проверяем, не использован ли код
        if code_data.get('used'):
            await update.message.reply_text("❌ Этот код уже использован!")
            return
        
        # Создаём привязку
        link_data = {
            'siteUserId': code_data['userId'],
            'siteName': code_data['name'],
            'siteColor': code_data['color'],
            'tgUserId': tg_user.id,
            'tgUsername': tg_user.username or '',
            'tgFirstName': tg_user.first_name,
            'linkedAt': int(time.time() * 1000),
            'linkCode': code
        }
        
        # Сохраняем в Firebase
        links_ref = db.reference(LINKS_REF)
        links_ref.child(code_data['userId']).set(link_data)
        
        # Помечаем код как использованный
        codes_ref.child(code).update({'used': True})
        
        await update.message.reply_text(
            f"✅ **Успешно привязано!**\n\n"
            f"Теперь ты **{code_data['name']}** 🎨\n\n"
            f"Твои сообщения будут отображаться с этим именем и цветом на сайте!",
            parse_mode='Markdown'
        )
        
        print(f"✅ Привязка создана: {tg_user.first_name} → {code_data['name']}")
        
    except Exception as e:
        print(f"❌ Ошибка в link_command: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при привязке. Попробуй ещё раз."
        )


async def unlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /unlink"""
    tg_user_id = update.effective_user.id
    
    try:
        removed = unlink_telegram(db.reference(LINKS_REF), tg_user_id)
        if not removed:
            await update.message.reply_text(
                "ℹ️ Твой Telegram не привязан ни к какому аккаунту."
            )
            return
        
        link = removed[0]
        
        await update.message.reply_text(
            f"✅ Отвязано от аккаунта {link.get('siteName') or 'сайта'}.\n\n"
            "Теперь твои сообщения будут идти как [TG] сообщения."
        )
        
        print("Telegram unlink completed")
        
    except Exception as e:
        print(f"❌ Ошибка в unlink_command: {e}")
        await update.message.reply_text("❌ Ошибка при отвязке.")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /whoami"""
    tg_user = update.effective_user
    link = get_link_by_tg_id(tg_user.id)
    
    if link:
        text = (
            f"✅ **Ты привязан!**\n\n"
            f"👤 Имя на сайте: **{link['siteName']}**\n"
            f"🎨 Цвет: `{link['siteColor']}`\n"
            f"🔗 Привязано: {datetime.fromtimestamp(link['linkedAt']/1000).strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Твои сообщения отображаются с этим именем и цветом!"
        )
    else:
        text = (
            f"⚪ **Не привязан**\n\n"
            f"👤 Telegram: {tg_user.first_name}\n"
            f"🆔 ID: `{tg_user.id}`\n\n"
            f"Твои сообщения идут с префиксом [TG].\n"
            f"Используй `/link CODE` чтобы привязать аккаунт с сайта."
        )
    
    await update.message.reply_text(text, parse_mode='Markdown')


async def reaction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /r или /reaction - меню реакций"""
    
    # Если указан эмодзи напрямую: /r ❤️
    if context.args and len(context.args) > 0:
        emoji = ' '.join(context.args)
        await send_reaction_to_firebase(update.effective_user, emoji)
        
        # Удаляем сообщение пользователя с командой через 0.5 сек
        await asyncio.sleep(0.5)
        try:
            await update.message.delete()
        except:
            pass  # Игнорируем если нет прав на удаление
        return
    
    # Создаём inline клавиатуру с эмодзи
    keyboard = []
    row = []
    for i, emoji in enumerate(SITE_EMOJIS):
        row.append(InlineKeyboardButton(emoji, callback_data=f"react_{emoji}"))
        if (i + 1) % 6 == 0:  # По 6 эмодзи в ряд
            keyboard.append(row)
            row = []
    if row:  # Добавляем остаток
        keyboard.append(row)
    
    # Кнопка для кастомного эмодзи
    keyboard.append([
        InlineKeyboardButton("✨ Свой эмодзи", callback_data="react_custom")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎭 Выбери реакцию:\n\n"
        "Или отправь командой: `/r 🎉`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def reaction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки реакций"""
    query = update.callback_query
    await query.answer()  # Просто подтверждаем нажатие без текста
    
    data = query.data
    
    if data == "react_custom":
        await query.edit_message_text(
            "✨ **Отправь любой эмодзи!**\n\n"
            "Просто напиши эмодзи в чат или используй:\n"
            "`/r 🎉` (твой эмодзи)",
            parse_mode='Markdown'
        )
        return
    
    # Извлекаем эмодзи из callback_data
    emoji = data.replace('react_', '')
    
    # Отправляем реакцию в Firebase
    await send_reaction_to_firebase(query.from_user, emoji)
    
    # Просто удаляем меню без уведомления
    try:
        await query.message.delete()
    except:
        pass  # Игнорируем если нет прав


async def send_reaction_to_firebase(tg_user, emoji):
    """Отправляет реакцию в Firebase"""
    try:
        # Проверяем привязку
        link = get_link_by_tg_id(tg_user.id)
        
        if link:
            # Привязанный пользователь - используем его цвет с сайта
            color = link['siteColor']
            uid = link['siteUserId']
        else:
            # Непривязанный - дефолтный Telegram цвет
            color = '#00a0e9'
            uid = f"tg_{tg_user.id}"
        
        # Создаём реакцию
        reaction_data = {
            'uid': uid,
            'color': color,
            'emoji': emoji,
            'emo': emoji,  # для совместимости
            't': int(time.time() * 1000),
            'id': f"tg_{int(time.time() * 1000000)}",
            'fromTelegram': True
        }
        
        # Отправляем в Firebase
        reactions_ref = db.reference(REACTIONS_REF)
        reactions_ref.push(reaction_data)
        
        print(f"✅ Реакция отправлена: {emoji} от {tg_user.first_name}")
        
    except Exception as e:
        print(f"❌ Ошибка send_reaction_to_firebase: {e}")


# ============= ОБРАБОТКА СООБЩЕНИЙ =============
async def delete_any_slash_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.startswith('/') and update.message.chat.type in ['group', 'supergroup']:
        try:
            await update.message.delete()
            print(f"🗑️ Удалено сообщение с /: {update.message.text[:50]}")
        except Exception as e:
            print(f"⚠️ Ошибка удаления: {e} — дай боту права, мать его!")
        
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычных текстовых сообщений из целевых групп — с RAT-магией и автоудалением"""
    
    print(f"📨 Получено сообщение из чата {update.message.chat.id} (тип: {update.message.chat.type})")
    print(f"🔍 Целевой CHAT_ID: {CHAT_ID}")
    print(f"🔍 RAT_CHAT_ID: {RAT_CHAT_ID}")
    print(f"🔍 Сравнение: '{str(update.message.chat.id)}' vs '{CHAT_ID}' или '{RAT_CHAT_ID}'")
    
    chat_id = str(update.message.chat.id)
    if chat_id not in [CHAT_ID, RAT_CHAT_ID]:
        print(f"⚠️ Игнорируем: чат {chat_id} — чужак!")
        return
    
    rat_active = is_rat_mode_active()
    if chat_id == RAT_CHAT_ID and not rat_active:
        print(f"⚠️ Игнорируем RAT группу когда режим off — свобода спит!")
        return
    
    if update.message.from_user.is_bot:
        print(f"⚠️ Игнорируем бота")
        return
    
    text = update.message.text
    if text and text.startswith('/'):
        try:
            await update.message.delete()
            print(f"🗑️ Удалена команда: {text[:50]}")
        except Exception as e:
            print(f"⚠️ Ошибка удаления: {e} — дай боту права админа с delete!")
        return
    
    tg_user = update.message.from_user
    print(f"✅ Обрабатываем от {tg_user.first_name}: {text[:50]}")
    
    link = get_link_by_tg_id(tg_user.id)
    
    try:
        message_data = {
            'uid': link['siteUserId'] if link else f"tg_{tg_user.id}",
            'name': link['siteName'] if link else f"[TG] {tg_user.first_name}",
            'color': link['siteColor'] if link else '#00a0e9',
            'text': text,
            't': int(time.time() * 1000),
            'fromTelegram': True
        }
        
        # firebase_callback ignores fromTelegram, so inbound messages are not echoed.
        new_msg_ref = db.reference(CHAT_REF).push(message_data)
        if rat_active:
            asyncio.create_task(delayed_delete(f"{CHAT_REF}/{new_msg_ref.key}", 300))

        if rat_active and chat_id == CHAT_ID:
            telegram_text = f"🎨 **{message_data['name']}**: {text}" if link else f"**{message_data['name']}**: {text}"
            await context.bot.send_message(
                chat_id=RAT_CHAT_ID,
                text=telegram_text,
                parse_mode='Markdown'
            )
            print(f"🐀 Дубли в RAT: {message_data['name']}: {text[:50]}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")


# ============= СЛУШАТЕЛЬ FIREBASE =============

async def delayed_delete(path, seconds):
    await asyncio.sleep(seconds)
    try:
        await asyncio.to_thread(db.reference(path).delete)
    except Exception as error:
        print(f"RAT message cleanup failed: {type(error).__name__}")


def firebase_callback(event):
    """Синхронный callback для Firebase - добавляет в очередь с key"""
    try:
        if not event.data:
            return
        
        msg = event.data
        
        if msg.get('fromTelegram'):
            return
        
        msg_time = msg.get('t', 0)
        last_time = last_processed_message.get('time', 0)
        if msg_time <= last_time:
            return
        
        last_processed_message['time'] = msg_time
        
        msg_key = event.path[1:] if event.path.startswith('/') else event.path  # Ключ сообщения
        
        try:
            message_queue.put_nowait((msg, msg_key))
        except:
            pass
            
    except Exception as e:
        print(f"❌ Ошибка в firebase_callback: {e}")


async def process_firebase_messages(app):
    """Асинхронная обработка сообщений из очереди"""
    print("🔄 Запуск обработчика сообщений Firebase...")
    
    while True:
        try:
            msg, msg_key = await message_queue.get()  # Теперь с key
            
            name = msg.get('name', 'Гость')
            text = msg.get('text', '')
            
            link = get_link_by_site_uid(msg.get('uid', ''))
            telegram_text = f"🎨 **{name}**: {text}" if link else f"**{name}**: {text}"
            
            target_chat = RAT_CHAT_ID if is_rat_mode_active() else CHAT_ID
            await app.bot.send_message(
                chat_id=target_chat,
                text=telegram_text,
                parse_mode='Markdown'
            )
            print(f"🌐→📱 {name}: {text[:50]} в чат {target_chat}")
            
            if is_rat_mode_active():
                ref_path = f"{CHAT_REF}/{msg_key}"
                asyncio.create_task(delayed_delete(ref_path, 300))
                print(f"⏳ Запланировано удаление {ref_path} через 5 мин")
            
        except Exception as e:
            print(f"❌ Ошибка обработки сообщения: {e}")
            await asyncio.sleep(1)


def start_firebase_listener():
    """Запускает Firebase слушатель (синхронный)"""
    try:
        chat_ref = db.reference(CHAT_REF)
        chat_ref.listen(firebase_callback)
        print("✅ Firebase слушатель подключен")
        return True
    except Exception as e:
        print(f"❌ Ошибка запуска Firebase слушателя: {e}")
        return False


# ============= MAIN =============

def main():
    """Запуск бота"""
    global message_queue
    
    if not BOT_TOKEN:
        print("❌ Не найден BOT_TOKEN в .env файле!")
        return
    
    if not CHAT_ID or CHAT_ID == "-1002345678901":
        print("⚠️  ВАЖНО: Не указан CHAT_ID в .env!")
        print("📌 Добавь бота в группу и узнай ID группы")
        print("📌 Для получения ID используй @getidsbot")
    
    print("🚀 Запуск DepressivePasties Bot...")
    
    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем команды (работают везде - в ЛС и группах)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("link", link_command))
    app.add_handler(CommandHandler("unlink", unlink_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("r", reaction_command))
    app.add_handler(CommandHandler("reaction", reaction_command))
    
    # Обработчик callback кнопок
    app.add_handler(CallbackQueryHandler(reaction_callback, pattern="^react_"))
    
    # Обработчик обычных сообщений из целевой группы (НЕ команды!)
    # Внутри handle_message проверяется CHAT_ID
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))
    
    # Запускаем Firebase слушатель и обработчик после старта event loop
    async def post_init(application):
        """Инициализация после запуска event loop"""
        global message_queue
        
        # Создаём очередь для сообщений (внутри event loop!)
        message_queue = asyncio.Queue()
        from messenger_startup import start_messenger
        application.bot_data['messenger_runtime'] = start_messenger(application, [CHAT_ID, RAT_CHAT_ID])
        
        # Запускаем синхронный Firebase слушатель в отдельном потоке
        import threading
        firebase_thread = threading.Thread(target=start_firebase_listener, daemon=True)
        firebase_thread.start()
        
        # Запускаем асинхронный обработчик сообщений
        asyncio.create_task(process_firebase_messages(application))
        print("✅ Система синхронизации запущена")
    
    app.post_init = post_init

    async def post_shutdown(application):
        runtime = application.bot_data.get('messenger_runtime')
        if runtime:
            server, transport = runtime
            if transport and transport.task:
                transport.task.cancel()
            server.close()
    app.post_shutdown = post_shutdown
    
    # Запускаем бота
    print("✅ Бот запущен! Нажми Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

