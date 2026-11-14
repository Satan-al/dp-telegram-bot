from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

import bot


class RatBridgeTests(TestCase):
    def route(self, active):
        message = NS(chat=NS(id=int(bot.RAT_CHAT_ID), type="supergroup"),
                     from_user=NS(id=123, is_bot=False, first_name="Test"), text="bridge test")
        send = AsyncMock()
        with patch.object(bot, "is_rat_mode_active", return_value=active), patch.object(
                bot, "get_link_by_tg_id", return_value=None), patch.object(bot.db, "reference") as ref, patch.object(
                bot, "delayed_delete", Mock(return_value=object())) as delete, patch.object(bot.asyncio, "create_task"):
            ref.return_value.push.return_value.key = "msg"
            with self.assertRaises(StopIteration):
                bot.handle_message(NS(message=message), NS(bot=NS(send_message=send))).send(None)
            send.assert_not_awaited()
            if active:
                ref.return_value.push.assert_called_once()
                data = ref.return_value.push.call_args.args[0]
                self.assertEqual(data["text"], message.text)
                self.assertTrue(data["fromTelegram"])
                with patch.object(bot, "message_queue") as queue:
                    bot.firebase_callback(NS(data=data, path="/msg"))
                    queue.put_nowait.assert_not_called()
                delete.assert_called_once_with(bot.CHAT_REF + "/msg", 300)
            else:
                ref.assert_not_called()
                delete.assert_not_called()

    def test_active(self):
        self.route(True)

    def test_inactive(self):
        self.route(False)
