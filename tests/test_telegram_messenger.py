import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from telegram.ext import ApplicationHandlerStop

from messenger_telegram import MessengerTelegram
import test_messenger as fixtures


def immediate(test):
    # All transport I/O is mocked. Fail if a test accidentally starts real asynchronous I/O.
    def run(self):
        coroutine = test(self)
        try:
            yielded = coroutine.send(None)
        except StopIteration:
            return
        finally:
            coroutine.close()
        raise AssertionError(f"Unexpected asynchronous I/O: {yielded!r}")
    return run


class TelegramTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ServiceTests()
        fixture.setUp()
        self.store, self.service, self.rid = fixture.store, fixture.service, fixture.rid
        self.store.set(f"messenger/rooms/{self.rid}/telegram", {"state": "queued", "requestId": "abc123", "requestedAt": 1})
        self.admin = NS(status="administrator", can_invite_users=True, can_change_info=True,
                        can_promote_members=True, can_delete_messages=True, can_pin_messages=True,
                        can_restrict_members=True)
        self.bot = NS(id=999)
        for name in ["send_message", "send_photo", "set_chat_permissions", "set_chat_title",
                     "set_chat_photo", "promote_chat_member", "ban_chat_member"]:
            setattr(self.bot, name, AsyncMock(return_value=NS(message_id=77)))
        self.bot.get_chat_member = AsyncMock(side_effect=lambda chat, uid:
            self.admin if uid == 999 else NS(status="creator" if uid == 111 else "member"))
        self.bot.get_chat_member_count = AsyncMock(return_value=2)
        self.bot.create_chat_invite_link = AsyncMock(return_value=NS(invite_link="https://t.me/+verified"))
        self.transport = MessengerTelegram(self.service, NS(bot=self.bot), [-2222])
        async def direct_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        self.transport.call = direct_call

    def update(self, text="", user=111, left=None, joined=()):
        message = NS(text=text, chat_id=-1001, reply_text=AsyncMock(),
                     left_chat_member=NS(id=left) if left else None,
                     new_chat_members=list(joined), photo=[], document=None)
        return NS(effective_message=message, effective_user=NS(id=user, is_bot=False))

    async def ready(self):
        with self.assertRaises(ApplicationHandlerStop):
            await self.transport.group_message(self.update("готово"), None)

    async def leave(self):
        self.bot.get_chat_member_count.return_value = 1
        with self.assertRaises(ApplicationHandlerStop):
            await self.transport.service_message(self.update(left=111), None)

    @immediate
    async def test_ready_does_not_publish_or_decorate(self):
        await self.ready()
        self.assertEqual("awaiting_exit", self.store.get(f"messenger/rooms/{self.rid}/telegram/state"))
        self.bot.create_chat_invite_link.assert_not_awaited()
        self.bot.set_chat_title.assert_not_awaited()
        self.assertEqual([], self.service.messages("alice", self.rid)["messages"])
        await self.transport.deliver()
        self.bot.send_message.assert_not_awaited()

    @immediate
    async def test_verified_exit_publishes_invite_first(self):
        await self.ready()
        await self.leave()
        self.assertEqual("ready", self.store.get(f"messenger/rooms/{self.rid}/telegram/state"))
        self.bot.create_chat_invite_link.assert_awaited_once_with(-1001, creates_join_request=True)
        self.bot.set_chat_photo.assert_awaited_once()
        messages = self.service.messages("alice", self.rid)["messages"]
        self.assertEqual("telegram_invite", messages[0]["kind"])

    @immediate
    async def test_another_person_leaving_does_not_unlock_group(self):
        await self.ready()
        self.bot.get_chat_member_count.return_value = 1
        with self.assertRaises(ApplicationHandlerStop):
            await self.transport.service_message(self.update(left=222), None)
        self.bot.create_chat_invite_link.assert_not_awaited()

    @immediate
    async def test_group_with_other_members_cannot_be_bound(self):
        self.bot.get_chat_member_count.return_value = 3
        await self.ready()
        self.assertIsNone(self.store.get("messenger/telegram_groups/-1001"))

    @immediate
    async def test_promote_right_is_required(self):
        self.admin.can_promote_members = False
        await self.ready()
        self.assertIsNone(self.store.get("messenger/telegram_groups/-1001"))

    @immediate
    async def test_notification_contains_no_participant_names(self):
        await self.transport.operator_request()
        sent = self.bot.send_message.call_args.kwargs["text"]
        self.assertNotIn("alice", sent)
        self.assertNotIn("bob", sent)
        self.assertEqual(111, self.bot.send_message.call_args.kwargs["chat_id"])

    @immediate
    async def test_only_linked_room_member_join_requests_are_approved(self):
        await self.ready()
        await self.leave()
        self.store.set("sessions/DepressivePasties/telegram_links/alice", {"tgUserId": 444, "siteUserId": "alice"})
        for user, permitted in [(444, True), (555, False)]:
            event = NS(chat=NS(id=-1001), from_user=NS(id=user), approve=AsyncMock(), decline=AsyncMock())
            with self.assertRaises(ApplicationHandlerStop):
                await self.transport.join_request(NS(chat_join_request=event), None)
            (event.approve if permitted else event.decline).assert_awaited_once()

    @immediate
    async def test_authorized_new_member_is_promoted_but_outsider_is_not(self):
        await self.ready()
        await self.leave()
        self.store.set("sessions/DepressivePasties/telegram_links/alice", {"tgUserId": 444, "siteUserId": "alice"})
        with self.assertRaises(ApplicationHandlerStop):
            await self.transport.service_message(self.update(joined=[NS(id=444)]), None)
        self.bot.promote_chat_member.assert_awaited_once()
        self.assertEqual(444, self.bot.promote_chat_member.call_args.args[1])
        self.bot.promote_chat_member.reset_mock()
        with self.assertRaises(ApplicationHandlerStop):
            await self.transport.service_message(self.update(joined=[NS(id=555)]), None)
        self.bot.promote_chat_member.assert_not_awaited()
        self.bot.ban_chat_member.assert_awaited_once_with(-1001, 555)

    @immediate
    async def test_site_message_is_relayed_once_and_telegram_source_does_not_loop(self):
        await self.ready()
        await self.leave()
        self.service.append("alice", self.rid, "site", "private text")
        self.service.append("bob", self.rid, "tg:-1001:12", "native text", origin="telegram", telegram_id=12)
        await self.transport.deliver()
        await self.transport.deliver()
        self.bot.send_message.assert_awaited_once_with(-1001, "alice:\nprivate text")


if __name__ == "__main__":
    unittest.main()
