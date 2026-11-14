"""Telegram transport and the operator-exit setup gate."""
import asyncio
import io
import logging
import re

from telegram import ChatPermissions, Update
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    ApplicationHandlerStop, CallbackQueryHandler, ChatJoinRequestHandler,
    ChatMemberHandler, MessageHandler, filters,
)

from messenger_avatar import render_avatar
from messenger_core import MessengerError, now_ms

LOG = logging.getLogger(__name__)


class MessengerTelegram:
    def __init__(self, service, application, protected_chat_ids=()):
        self.service = service
        self.application = application
        self.bot = application.bot
        self.protected = {str(value) for value in protected_chat_ids if value}
        self.task = None
        self.activation_lock = asyncio.Lock()

    async def call(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def get(self, suffix):
        return await self.call(self.service.store.get, "messenger/" + suffix)

    async def update(self, suffix, value):
        await self.call(self.service.store.update, "messenger/" + suffix, value)

    async def linked_room(self, chat_id):
        return await self.get("telegram_groups/" + str(chat_id))

    async def operator_request(self):
        setup = await self.call(self.service.queued_setup)
        if not setup:
            return
        active, room = setup
        telegram = room["telegram"]
        if telegram.get("operatorMessageId"):
            return
        short = active["requestId"][-6:]
        message = await self.bot.send_message(
            chat_id=int(self.service.operator_id),
            text=(f"Новая заявка №{short}.\n"
                  f"Создай пустую группу с названием «DP {short}», добавь меня администратором "
                  "с правами приглашать, менять оформление и назначать администраторов "
                  "и напиши в этой группе «готово».\n"
                  "После моего подтверждения выйди из группы. Только тогда я открою приглашения.\n"
                  "Пригласительную ссылку можно прислать ответом сюда, но я могу создать её сам.\n"
                  "Для отмены ответь на это сообщение: отмена."))
        await self.update("rooms/" + active["roomId"] + "/telegram",
                          {"operatorMessageId": message.message_id, "state": "assigned"})

    async def private_message(self, update, context):
        message = update.effective_message
        if not message or str(update.effective_user.id) != self.service.operator_id:
            return
        setup = await self.call(self.service.queued_setup)
        if not setup or not message.reply_to_message:
            return
        active, room = setup
        if message.reply_to_message.message_id != room["telegram"].get("operatorMessageId"):
            return
        text = (message.text or "").strip()
        if text.casefold() in {"отмена", "cancel"} and room["telegram"].get("state") != "awaiting_exit":
            await self.update("", {"rooms/" + active["roomId"] + "/telegram": {"state": "off"},
                                   "setup_active": None})
            await message.reply_text("Заявка отменена.")
            raise ApplicationHandlerStop
        if re.fullmatch(r"https://t\.me/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+)", text):
            await self.update("rooms/" + active["roomId"] + "/telegram", {"operatorInvite": text})
            await message.reply_text("Принято. Добавь меня администратором и напиши «готово» в новой группе.")
            raise ApplicationHandlerStop

    async def group_message(self, update, context):
        message = update.effective_message
        if not message:
            return
        chat_id = message.chat_id
        room_id = await self.linked_room(chat_id)
        text = (message.text or "").strip()
        if str(update.effective_user.id) == self.service.operator_id and text.casefold() == "готово":
            if str(chat_id) in self.protected:
                return
            try:
                myself = await self.bot.get_chat_member(chat_id, self.bot.id)
                operator = await self.bot.get_chat_member(chat_id, update.effective_user.id)
                if (myself.status != "administrator" or not myself.can_invite_users
                        or not myself.can_change_info or not myself.can_promote_members
                        or not getattr(myself, "can_restrict_members", False) or operator.status != "creator"):
                    raise MessengerError("Нужны права: приглашения, изменение группы, удаление участников и назначение администраторов.")
                if await self.bot.get_chat_member_count(chat_id) != 2:
                    raise MessengerError("Нужна новая пустая группа: пока только ты и бот.")
                room_id, _ = await self.call(self.service.bind_group, update.effective_user.id, chat_id)
                await message.reply_text("Группа привязана. Теперь выйди из неё. Приглашения пока закрыты.")
            except MessengerError as error:
                await message.reply_text(str(error))
            raise ApplicationHandlerStop
        if not room_id:
            return
        room = await self.get("rooms/" + room_id)
        if not room or room["telegram"].get("state") != "ready" or update.effective_user.is_bot:
            raise ApplicationHandlerStop
        uid = await self.call(self.service.site_uid_for_telegram, update.effective_user.id)
        if not uid or not room["members"].get(uid):
            await message.reply_text("Сначала привяжи Telegram к аккаунту сайта и получи доступ к этой беседе.")
            raise ApplicationHandlerStop
        client_id = f"tg:{chat_id}:{message.message_id}"
        try:
            if message.photo:
                photo = await message.photo[-1].get_file()
                raw = bytes(await photo.download_as_bytearray())
                await self.call(self.service.image_message, uid, room_id, client_id, raw,
                                message.caption or "", "telegram", message.message_id)
            elif message.document and message.document.mime_type in {"image/png", "image/jpeg", "image/webp"}:
                if message.document.file_size and message.document.file_size > 8 * 1024 * 1024:
                    raise MessengerError("Картинка должна быть меньше 8 МБ.")
                file = await message.document.get_file()
                raw = bytes(await file.download_as_bytearray())
                await self.call(self.service.image_message, uid, room_id, client_id, raw,
                                message.caption or "", "telegram", message.message_id)
            elif text:
                await self.call(self.service.append, uid, room_id, client_id, text, None,
                                "telegram", message.message_id)
            else:
                await message.reply_text("Пока поддерживаются текст и картинки.")
        except MessengerError as error:
            await message.reply_text(str(error))
        raise ApplicationHandlerStop

    async def finalize(self, room_id, group_id):
        async with self.activation_lock:
            room = await self.get("rooms/" + room_id)
            telegram = (room or {}).get("telegram", {})
            if telegram.get("state") != "awaiting_exit" or not telegram.get("exitConfirmed"):
                return
            # A real Telegram service event is required; a user's 'ready' is never evidence of exit.
            if await self.bot.get_chat_member_count(group_id) != 1:
                return
            myself = await self.bot.get_chat_member(group_id, self.bot.id)
            if (myself.status != "administrator" or not myself.can_invite_users
                    or not myself.can_change_info or not myself.can_promote_members
                    or not getattr(myself, "can_restrict_members", False)):
                return
            profiles = await self.call(self.service.room_profiles, room)
            await self.bot.set_chat_permissions(group_id, ChatPermissions(
                can_send_messages=True, can_send_photos=True, can_send_other_messages=False,
                can_add_web_page_previews=True, can_invite_users=False))
            await self.bot.set_chat_title(group_id, (room.get("title") or " · ".join(p["name"] for p in profiles))[:128])
            avatar = await self.call(render_avatar, profiles)
            await self.bot.set_chat_photo(group_id, io.BytesIO(avatar))
            invite = await self.bot.create_chat_invite_link(group_id, creates_join_request=True)
            await self.call(self.service.activate_group, self.service.operator_id, group_id, invite.invite_link)

    async def service_message(self, update, context):
        message = update.effective_message
        if not message:
            return
        room_id = await self.linked_room(message.chat_id)
        if not room_id:
            return
        room = await self.get("rooms/" + room_id)
        if message.left_chat_member and str(message.left_chat_member.id) == self.service.operator_id:
            if room["telegram"].get("state") == "awaiting_exit":
                await self.update("rooms/" + room_id + "/telegram", {"exitConfirmed": True})
                await self.finalize(room_id, message.chat_id)
        for user in message.new_chat_members or []:
            if user.id == self.bot.id:
                continue
            uid = await self.call(self.service.site_uid_for_telegram, user.id)
            if not uid or not room["members"].get(uid):
                if str(user.id) == self.service.operator_id:
                    await self.update("rooms/" + room_id + "/telegram", {"state": "awaiting_exit", "exitConfirmed": False})
                else:
                    await self.bot.ban_chat_member(message.chat_id, user.id)
            elif room["telegram"].get("state") == "ready":
                await self.update("rooms/" + room_id + "/telegram/participants", {str(user.id): uid})
                await self.promote_member(message.chat_id, user.id)
        raise ApplicationHandlerStop

    async def promote_member(self, group_id, user_id):
        member = await self.bot.get_chat_member(group_id, user_id)
        if member.status == "creator" or (member.status == "administrator" and not getattr(member, "can_be_edited", False)):
            return
        myself = await self.bot.get_chat_member(group_id, self.bot.id)
        if not getattr(myself, "can_promote_members", False):
            return
        await self.bot.promote_chat_member(
            group_id, user_id, can_manage_chat=True, can_change_info=bool(myself.can_change_info),
            can_delete_messages=bool(getattr(myself, "can_delete_messages", False)),
            can_pin_messages=bool(getattr(myself, "can_pin_messages", False)),
            can_invite_users=False, can_promote_members=False, is_anonymous=False)

    async def join_request(self, update, context):
        event = update.chat_join_request
        room_id = await self.linked_room(event.chat.id)
        if not room_id:
            return
        room = await self.get("rooms/" + room_id)
        uid = await self.call(self.service.site_uid_for_telegram, event.from_user.id)
        allowed = bool(room and room["telegram"].get("state") == "ready" and uid and room["members"].get(uid))
        if allowed:
            await self.update("rooms/" + room_id + "/telegram/participants", {str(event.from_user.id): uid})
            await event.approve()
        else:
            await event.decline()
        raise ApplicationHandlerStop

    async def my_membership(self, update, context):
        event = update.my_chat_member
        room_id = await self.linked_room(event.chat.id)
        if room_id and event.new_chat_member.status != "administrator":
            await self.update("rooms/" + room_id + "/telegram", {"state": "paused"})
            raise ApplicationHandlerStop

    async def deliver(self):
        groups = await self.get("telegram_groups") or {}
        for group_id, room_id in groups.items():
            room = await self.get("rooms/" + room_id)
            if not room:
                continue
            telegram = room.get("telegram", {})
            if telegram.get("state") == "awaiting_exit" and telegram.get("exitConfirmed"):
                await self.finalize(room_id, int(group_id))
                continue
            if telegram.get("state") != "ready":
                continue
            if not await self.audit_members(room_id, int(group_id), room):
                continue
            rows = await self.call(self.service.store.pending, "messenger/messages/" + room_id)
            for key, record in rows:
                if record.get("origin") == "telegram":
                    continue
                try:
                    profile = await self.call(self.service.profile, record["senderUid"])
                    payload = self.service.message_view(room_id, key, record)
                    text = profile["name"] + ":\n" + payload["text"]
                    # Mark before sending. A crash/network timeout must not blindly resend private messages.
                    path = "messages/" + room_id + "/" + key
                    await self.update(path, {"telegramState": "sending"})
                    if payload.get("image"):
                        raw = await self.call(self.service.image, record["senderUid"], room_id, payload["image"]["id"])
                        sent = await self.bot.send_photo(int(group_id), io.BytesIO(raw), caption=text[:1024])
                        if len(text) > 1024:
                            await self.bot.send_message(int(group_id), text[1024:])
                    else:
                        sent = await self.bot.send_message(int(group_id), text)
                    await self.update(path, {"telegramState": "sent", "telegramMessageId": sent.message_id})
                except RetryAfter as error:
                    await self.update(path, {"telegramState": "queued"})
                    await asyncio.sleep(min(float(error.retry_after), 60))
                    break
                except (NetworkError, TelegramError):
                    await self.update(path, {"telegramState": "uncertain"})
                    LOG.warning("Telegram delivery was not confirmed for room %s message %s", room_id, key)
                except MessengerError:
                    await self.update("messages/" + room_id + "/" + key, {"telegramState": "failed"})

    async def audit_members(self, room_id, group_id, room):
        for tg_id, uid in room.get("telegram", {}).get("participants", {}).items():
            current = await self.call(self.service.site_uid_for_telegram, tg_id)
            if current == uid and room["members"].get(uid):
                continue
            member = await self.bot.get_chat_member(group_id, int(tg_id))
            if member.status == "creator" or (member.status == "administrator" and not getattr(member, "can_be_edited", False)):
                await self.update("rooms/" + room_id + "/telegram", {"state": "paused"})
                return False
            if member.status == "administrator":
                await self.bot.promote_chat_member(group_id, int(tg_id), can_manage_chat=False,
                    can_change_info=False, can_delete_messages=False, can_invite_users=False,
                    can_restrict_members=False, can_pin_messages=False, can_promote_members=False,
                    can_manage_video_chats=False, is_anonymous=False)
            if member.status not in {"left", "kicked"}:
                await self.bot.ban_chat_member(group_id, int(tg_id))
            await self.update("rooms/" + room_id + "/telegram/participants", {tg_id: None})
        return True

    async def worker(self):
        while True:
            try:
                if self.service.operator_id:
                    await self.operator_request()
                await self.deliver()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Messenger transport task failed")
            await asyncio.sleep(3)

    def install(self):
        self.application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, self.private_message), group=-30)
        self.application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.StatusUpdate.ALL, self.service_message), group=-30)
        self.application.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, self.group_message), group=-30)
        self.application.add_handler(ChatJoinRequestHandler(self.join_request), group=-30)
        self.application.add_handler(ChatMemberHandler(self.my_membership, ChatMemberHandler.MY_CHAT_MEMBER), group=-30)
        self.task = asyncio.create_task(self.worker())
        return self
