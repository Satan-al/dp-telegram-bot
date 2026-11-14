import copy
import io
import json
import threading
import unittest

from cryptography.fernet import Fernet
from PIL import Image
from messenger_core import Cipher, FirebaseStore, MessengerError, MessengerService
from messenger_http import create_app


class MemoryStore:
    def __init__(self):
        self.data, self.lock, self.counter = {}, threading.RLock(), 0

    def get(self, path):
        node = self.data
        for part in path.strip("/").split("/"):
            if not part:
                continue
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return copy.deepcopy(node)

    def set(self, path, value):
        parts, node = path.strip("/").split("/"), self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if value is None:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = copy.deepcopy(value)

    def update(self, path, values):
        for key, value in values.items():
            self.set(path.rstrip("/") + "/" + key, value)

    def transaction(self, path, fn):
        with self.lock:
            value = fn(self.get(path))
            self.set(path, value)
            return value

    def new_key(self, path):
        self.counter += 1
        return f"{self.counter:020d}"

    def page(self, path, before=None, after=None, limit=50):
        rows = [(key, row) for key, row in sorted((self.get(path) or {}).items())
                if (not before or key < before) and (not after or key > after)]
        return rows[:limit+1] if after else rows[-limit-1:]

    def pending(self, path, limit=20):
        return [(key, row) for key, row in sorted((self.get(path) or {}).items())
                if row.get("telegramState") == "queued"][:limit]


class MemoryMedia:
    def __init__(self):
        self.data = {}

    def put(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data[key]

    def delete(self, key):
        self.data.pop(key, None)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.store, self.media = MemoryStore(), MemoryMedia()
        self.key = Fernet.generate_key().decode()
        self.service = MessengerService(self.store, Cipher(self.key), self.media, "111")
        for uid in ["alice", "bob", "eve", "operator"]:
            self.store.set("access/" + uid, {"name": uid, "enabled": True, "version": 1})
            self.store.set("sessions/DepressivePasties/users/" + uid,
                           {"name": uid, "emoji": "", "color": "#aabbcc"})
        self.rid = self.service.create_room("alice", ["bob"])["id"]
        self.store.set(f"messenger/rooms/{self.rid}/telegram", {"state": "ready"})

    def test_room_image_limit_shared_between_transports(self):
        from messenger_core import DatabaseMedia
        self.service.media = DatabaseMedia(self.store)
        second = self.pending_room()
        self.store.set(f"messenger/rooms/{second}/telegram", {"state": "ready"})
        buf = io.BytesIO()
        Image.new("RGB", (3, 3)).save(buf, "PNG")
        def post(room, i):
            origin = "telegram" if i % 2 == 0 else "site"
            return self.service.image_message("alice", room, str(i), buf.getvalue(), f"caption {i}", origin)
        other = post(second, 0)
        first = post(self.rid, 0)
        for i in range(1, 22):
            post(self.rid, i)
        expired = post(self.rid, 0)
        self.assertEqual(first["id"], expired["id"])
        self.assertTrue(expired["imageExpired"])
        self.assertEqual("caption 0", expired["text"])
        self.assertIsNone(expired["image"])
        self.assertEqual(20, len(self.store.get("messenger/image_data/" + self.rid)))
        with self.assertRaises(MessengerError):
            self.service.image("bob", self.rid, first["image"]["id"])
        self.assertTrue(self.service.image("bob", second, other["image"]["id"]))

    def pending_room(self):
        return self.service.create_room("alice", ["bob"], "group")["id"]

    def test_direct_chat_is_unique_in_both_directions(self):
        self.assertEqual(self.rid, self.service.create_room("bob", ["alice"])["id"])
        self.assertEqual(1, len(self.store.get("messenger/rooms")))

    def test_unknown_and_disabled_people_cannot_be_added(self):
        for target in ["unknown", "eve"]:
            if target == "eve":
                self.store.set("access/eve/enabled", False)
            with self.assertRaises(MessengerError):
                self.service.create_room("alice", [target])

    def test_private_history_is_not_accessible_even_to_setup_operator(self):
        for uid in ["eve", "operator"]:
            with self.assertRaises(MessengerError) as error:
                self.service.messages(uid, self.rid)
            self.assertEqual(404, error.exception.status)
        self.assertEqual([], self.service.rooms("eve"))

    def test_ciphertext_and_idempotent_retry(self):
        one = self.service.append("alice", self.rid, "retry", "private phrase")
        two = self.service.append("alice", self.rid, "retry", "changed retry")
        self.assertEqual(one, two)
        self.assertNotIn("private phrase", json.dumps(self.store.data))
        self.assertEqual(1, len(self.store.get("messenger/messages/" + self.rid)))
        self.assertEqual("private phrase", self.service.messages("bob", self.rid)["messages"][0]["text"])

    def test_client_id_is_scoped_to_sender(self):
        one = self.service.append("alice", self.rid, "same", "A")
        two = self.service.append("bob", self.rid, "same", "B")
        self.assertNotEqual(one["id"], two["id"])

    def test_disabled_account_loses_access_immediately(self):
        self.store.set("access/alice/enabled", False)
        with self.assertRaises(MessengerError):
            self.service.append("alice", self.rid, "no", "No")

    def test_all_history_pages_are_complete(self):
        for i in range(125):
            self.service.append("alice", self.rid, f"m{i}", str(i))
        ids, before = [], None
        while True:
            page = self.service.messages("bob", self.rid, before=before)
            ids.extend(message["id"] for message in page["messages"])
            if not page["hasMore"]:
                break
            before = page["messages"][0]["id"]
        self.assertEqual(125, len(ids))
        self.assertEqual(125, len(set(ids)))

    def test_images_are_encrypted_private_and_idempotent(self):
        buffer = io.BytesIO()
        Image.new("RGB", (30, 20), "red").save(buffer, "PNG")
        message = self.service.image_message("alice", self.rid, "image", buffer.getvalue())
        self.assertNotIn(b"RIFF", next(iter(self.media.data.values())))
        raw = self.service.image("bob", self.rid, message["image"]["id"])
        self.assertEqual((30, 20), Image.open(io.BytesIO(raw)).size)
        with self.assertRaises(MessengerError):
            self.service.image("eve", self.rid, message["image"]["id"])
        other = self.service.create_room("alice", ["eve"])["id"]
        with self.assertRaises(MessengerError):
            self.service.image("alice", other, message["image"]["id"])
        self.service.image_message("alice", self.rid, "image", buffer.getvalue())
        self.assertEqual(1, len(self.media.data))

    def test_invalid_image_is_rejected(self):
        with self.assertRaises(MessengerError):
            self.service.image_message("alice", self.rid, "bad", b"<svg/>")

    def test_key_rotation_reads_old_messages(self):
        self.service.append("alice", self.rid, "old", "old data")
        self.service.cipher = Cipher(Fernet.generate_key().decode() + "," + self.key)
        self.assertEqual("old data", self.service.messages("bob", self.rid)["messages"][0]["text"])

    def test_create_automatically_queues_setup_and_blocks_chat(self):
        rid = self.pending_room()
        room = self.service.describe_room("alice", rid)
        self.assertEqual("queued", room["telegram"]["state"])
        with self.assertRaises(MessengerError) as error:
            self.service.append("alice", rid, "early", "Do not send")
        self.assertEqual(409, error.exception.status)
        with self.assertRaises(MessengerError):
            self.service.image_message("alice", rid, "earlyimage", b"image")
        self.assertFalse(self.media.data)
        self.assertIsNone(room["telegram"]["invite"])

    def test_setup_request_is_idempotent(self):
        rid = self.pending_room()
        one = self.service.request_telegram("alice", rid)
        two = self.service.request_telegram("bob", rid)
        self.assertEqual(one["requestId"], two["requestId"])
        active, _ = self.service.queued_setup()
        self.assertNotIn("alice", json.dumps(active))
        self.assertNotIn("members", active)

    def test_ready_does_not_replace_verified_exit(self):
        rid = self.pending_room()
        self.service.bind_group(111, -1001)
        with self.assertRaises(MessengerError):
            self.service.activate_group(111, -1001, "https://t.me/+invite")
        self.assertIsNone(self.service.describe_room("bob", rid)["telegram"]["invite"])

    def test_invitation_is_first_message_and_exit_opens_chat(self):
        rid = self.pending_room()
        with self.assertRaises(MessengerError):
            self.service.bind_group(222, -1001)
        self.service.bind_group(111, -1001)
        self.store.set(f"messenger/rooms/{rid}/telegram/exitConfirmed", True)
        self.service.activate_group(111, -1001, "https://t.me/+valid")
        self.service.append("alice", rid, "hi", "Hello")
        messages = self.service.messages("bob", rid)["messages"]
        self.assertEqual("telegram_invite", messages[0]["kind"])
        self.assertEqual("_system", messages[0]["senderUid"])
        self.assertEqual("https://t.me/+valid", messages[0]["invite"])
        self.assertEqual("Hello", messages[1]["text"])

    def test_read_cursor_does_not_go_backwards(self):
        one = self.service.append("alice", self.rid, "a", "A")
        two = self.service.append("alice", self.rid, "b", "B")
        self.service.mark_read("bob", self.rid, two["id"])
        self.service.mark_read("bob", self.rid, one["id"])
        self.assertEqual(two["id"], self.store.get(f"messenger/read/bob/{self.rid}"))
        with self.assertRaises(MessengerError):
            self.service.mark_read("bob", self.rid, "missing")

    def test_http_authorization_origin_and_sender_spoof(self):
        app = create_app(self.service, lambda token: {"uid": token, "firebase": {"sign_in_provider": "password"}},
                         ["https://room.example"])
        client = app.test_client()
        self.assertEqual(401, client.get("/messenger/api/rooms?token=alice").status_code)
        self.assertEqual(403, client.get("/messenger/api/rooms",
            headers={"Authorization": "Bearer alice", "Origin": "https://evil.example"}).status_code)
        self.assertEqual(204, client.options("/messenger/api/rooms",
            headers={"Origin": "https://room.example"}).status_code)
        response = client.post(f"/messenger/api/rooms/{self.rid}/messages",
            headers={"Authorization": "Bearer alice", "Origin": "https://room.example"},
            json={"uid": "bob", "clientId": "spoof", "text": "Hi"})
        self.assertEqual(201, response.status_code)
        self.assertEqual("alice", response.json["message"]["senderUid"])
        self.assertEqual("https://room.example", response.headers["Access-Control-Allow-Origin"])
        self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_anonymous_token_is_rejected(self):
        app = create_app(self.service,
            lambda _: {"uid": "alice", "firebase": {"sign_in_provider": "anonymous"}}, [])
        self.assertEqual(401, app.test_client().get("/messenger/api/me",
            headers={"Authorization": "Bearer token"}).status_code)

    def test_firebase_inclusive_cursor_keeps_the_extra_history_row(self):
        class Query:
            def __init__(self):
                self.before, self.after, self.limit, self.first = None, None, 0, False
            def order_by_key(self):
                return self
            def end_at(self, value):
                self.before = value
                return self
            def start_at(self, value):
                self.after = value
                return self
            def limit_to_last(self, value):
                self.limit = value
                return self
            def limit_to_first(self, value):
                self.limit, self.first = value, True
                return self
            def get(self):
                rows = [(str(i).zfill(4), {}) for i in range(1, 126)
                        if (not self.before or str(i).zfill(4) <= self.before)
                        and (not self.after or str(i).zfill(4) >= self.after)]
                return dict(rows[:self.limit] if self.first else rows[-self.limit:])
        class FakeDB:
            def reference(self, path):
                return Query()
        store = FirebaseStore(FakeDB())
        self.assertEqual(51, len(store.page("messages", before="0076")))
        self.assertEqual(51, len(store.page("messages", after="0001")))

    def test_operator_is_resolved_from_unique_admitted_site_account(self):
        self.store.set("access/operator/name", "Salyose")
        self.store.set("sessions/DepressivePasties/telegram_links/operator", {"tgUserId": 111})
        self.assertEqual("111", self.service.operator_from_site_name())
        self.store.set("access/eve/name", "Salyose")
        self.assertEqual("", self.service.operator_from_site_name())


if __name__ == "__main__":
    unittest.main()
