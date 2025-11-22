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

import firebase_admin
from firebase_admin import credentials, db

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
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

# Эмодзи из сайта (те же 18 что на сайте)
SITE_EMOJIS = [
    '👍', '👎', '❤️', '😂', '😮', '😢', 
    '🔥', '🤡', '🤬', '🍷', '🧐', '💃',
    '🚩', '🤷‍♂️', '🙄', '💔', '🤯', '🔔'
]

# Глобальные переменные
firebase_listener = None
last_processed_message = {}


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
            if link_data.get('tgUserId') == tg_user_id:
                return link_data
    except Exception as e:
        print(f"❌ Ошибка get_link_by_tg_id: {e}")
    return None


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
        link = get_link_by_tg_id(tg_user_id)
        
        if not link:
            await update.message.reply_text(
                "ℹ️ Твой Telegram не привязан ни к какому аккаунту."
            )
            return
        
        # Удаляем привязку
        links_ref = db.reference(LINKS_REF)
        links_ref.child(link['siteUserId']).delete()
        
        await update.message.reply_text(
            f"✅ Отвязано от аккаунта **{link['siteName']}**\n\n"
            f"Теперь твои сообщения будут идти как [TG] сообщения.",
            parse_mode='Markdown'
        )
        
        print(f"✅ Отвязка: {update.effective_user.first_name} от {link['siteName']}")
        
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
        await update.message.reply_text(f"✅ Реакция отправлена: {emoji}")
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
    await query.answer()
    
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
    
    await query.edit_message_text(
        f"✅ **Реакция отправлена!**\n\n{emoji}\n\n"
        f"Все на сайте её увидят! 🎬"
    )


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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычных текстовых сообщений из группы"""
    
    # Игнорируем сообщения не из группы
    if update.message.chat.type not in ['group', 'supergroup']:
        return
    
    # Игнорируем сообщения бота
    if update.message.from_user.is_bot:
        return
    
    tg_user = update.message.from_user
    text = update.message.text
    
    # Проверяем привязку
    link = get_link_by_tg_id(tg_user.id)
    
    try:
        if link:
            # Привязанный пользователь - отправляем с данными с сайта
            message_data = {
                'uid': link['siteUserId'],
                'name': link['siteName'],
                'color': link['siteColor'],
                'text': text,
                't': int(time.time() * 1000),
                'fromTelegram': True
            }
        else:
            # Непривязанный - отправляем как TG пользователя
            message_data = {
                'uid': f"tg_{tg_user.id}",
                'name': f"[TG] {tg_user.first_name}",
                'color': '#00a0e9',
                'text': text,
                't': int(time.time() * 1000),
                'fromTelegram': True
            }
        
        # Отправляем в Firebase
        chat_ref = db.reference(CHAT_REF)
        chat_ref.push(message_data)
        
        print(f"📱→🌐 {message_data['name']}: {text[:50]}")
        
    except Exception as e:
        print(f"❌ Ошибка handle_message: {e}")


# ============= СЛУШАТЕЛЬ FIREBASE =============

async def firebase_listener_task(app):
    """Слушает новые сообщения из Firebase и отправляет в Telegram"""
    print("👂 Запуск слушателя Firebase...")
    
    def on_message_added(event):
        """Callback при добавлении нового сообщения"""
        try:
            if not event.data:
                return
            
            msg = event.data
            msg_key = event.path.strip('/')
            
            # Игнорируем сообщения от Telegram (чтобы не было дубликатов)
            if msg.get('fromTelegram'):
                return
            
            # Игнорируем уже обработанные
            msg_time = msg.get('t', 0)
            last_time = last_processed_message.get('time', 0)
            if msg_time <= last_time:
                return
            
            last_processed_message['time'] = msg_time
            
            # Формируем текст для Telegram
            name = msg.get('name', 'Гость')
            text = msg.get('text', '')
            color_indicator = '🎨'  # можно добавить цветной кружок
            
            # Проверяем, привязан ли этот пользователь
            link = get_link_by_site_uid(msg.get('uid', ''))
            if link:
                # Привязанный пользователь
                telegram_text = f"{color_indicator} **{name}**: {text}"
            else:
                # Непривязанный - добавляем [WEB]
                telegram_text = f"[WEB] **{name}**: {text}"
            
            # Отправляем в Telegram группу
            asyncio.create_task(
                app.bot.send_message(
                    chat_id=CHAT_ID,
                    text=telegram_text,
                    parse_mode='Markdown'
                )
            )
            
            print(f"🌐→📱 {name}: {text[:50]}")
            
        except Exception as e:
            print(f"❌ Ошибка в firebase_listener: {e}")
    
    # Подписываемся на новые сообщения
    try:
        chat_ref = db.reference(CHAT_REF)
        chat_ref.listen(on_message_added)
        print("✅ Слушатель Firebase запущен")
    except Exception as e:
        print(f"❌ Ошибка запуска слушателя: {e}")


# ============= MAIN =============

def main():
    """Запуск бота"""
    
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
    
    # Регистрируем команды
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("link", link_command))
    app.add_handler(CommandHandler("unlink", unlink_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("r", reaction_command))
    app.add_handler(CommandHandler("reaction", reaction_command))
    
    # Обработчик callback кнопок
    app.add_handler(CallbackQueryHandler(reaction_callback, pattern="^react_"))
    
    # Обработчик обычных сообщений из группы
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_message
    ))
    
    # Запускаем слушатель Firebase
    asyncio.create_task(firebase_listener_task(app))
    
    # Запускаем бота
    print("✅ Бот запущен! Нажми Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
