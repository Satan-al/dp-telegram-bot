"""Private rooms. Only the trusted bridge can decrypt stored content."""
import hashlib
import io
import json
import re
import secrets
import threading
import time
from collections import defaultdict

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from PIL import Image, ImageOps


class MessengerError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now_ms():
    return int(time.time() * 1000)


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise MessengerError("Invalid identifier")
    return value


def normalize_image(raw):
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise MessengerError("Image must be smaller than 8 MB", 413)
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise MessengerError("Only PNG, JPEG and WebP are supported")
            if source.width * source.height > 24_000_000:
                raise MessengerError("Image dimensions are too large", 413)
            image = ImageOps.exif_transpose(source)
            image.thumbnail((2560, 2560))
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=90, method=4)
            return output.getvalue(), image.width, image.height
    except MessengerError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise MessengerError("Invalid image") from error


class Cipher:
    def __init__(self, keys):
        parsed = [key.strip() for key in keys.split(",") if key.strip()]
        if not parsed:
            raise ValueError("MESSENGER_ENCRYPTION_KEYS is required")
        self.fernet = MultiFernet([Fernet(key.encode("ascii")) for key in parsed])

    def encrypt(self, raw):
        return self.fernet.encrypt(raw)

    def decrypt(self, token):
        try:
            return self.fernet.decrypt(token)
        except InvalidToken as error:
            raise MessengerError("Encrypted content is unavailable", 503) from error

    def seal(self, payload):
        return self.encrypt(json.dumps(payload, ensure_ascii=False).encode()).decode("ascii")

    def open(self, token):
        return json.loads(self.decrypt(token.encode("ascii")))


class FirebaseStore:
    def __init__(self, db):
        self.db = db

    def get(self, path):
        return self.db.reference(path).get()

    def set(self, path, value):
        self.db.reference(path).set(value)

    def update(self, path, value):
        self.db.reference(path).update(value)

    def transaction(self, path, fn):
        return self.db.reference(path).transaction(fn)

    def new_key(self, path):
        return self.db.reference(path).push().key

    def page(self, path, before=None, after=None, limit=50):
        query = self.db.reference(path).order_by_key()
        if before:
            query = query.end_at(before)
        if after:
            query = query.start_at(after)
        count = limit + 1 + bool(before or after)
        query = query.limit_to_first(count) if after else query.limit_to_last(count)
        return [(key, value) for key, value in sorted((query.get() or {}).items())
                if (not before or key < before) and (not after or key > after)]

    def pending(self, path, limit=20):
        rows = self.db.reference(path).order_by_child("telegramState").equal_to("queued").limit_to_first(limit).get()
        return sorted((rows or {}).items())


class FirebaseMedia:
    def __init__(self, bucket):
        self.bucket = bucket

    def put(self, key, raw):
        self.bucket.blob("messenger/" + key).upload_from_string(raw, content_type="application/octet-stream")

    def get(self, key):
        return self.bucket.blob("messenger/" + key).download_as_bytes()

    def delete(self, key):
        self.bucket.blob("messenger/" + key).delete()


class MessengerService:
    ROOT = "messenger"
    SESSION = "sessions/DepressivePasties"

    def __init__(self, store, cipher, media, operator_id=None):
        self.store, self.cipher, self.media = store, cipher, media
        self.operator_id = str(operator_id or "")
        self.locks = defaultdict(threading.RLock)
        self.media_slots = threading.BoundedSemaphore(1)

    def admitted(self, uid):
        account = self.store.get("access/" + safe_id(uid)) or {}
        if not account.get("enabled") or account.get("version") != 1:
            raise MessengerError("Account is not admitted", 403)
        return account

    def profile(self, uid):
        account = self.admitted(uid)
        profile = self.store.get(self.SESSION + "/users/" + uid) or {}
        color = str(profile.get("color", "#16c7b7"))
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#16c7b7"
        emoji = profile.get("emoji") or ""
        if isinstance(emoji, dict):
            emoji = emoji.get("src", "") if emoji.get("type") == "png" else ""
        emoji = str(emoji)
        return {"uid": uid, "name": str(account.get("name") or profile.get("name") or "User")[:64],
                "color": color, "emoji": emoji if len(emoji) <= 100_000 else ""}

    def members(self, uid):
        self.admitted(uid)
        return sorted([self.profile(key) for key, value in (self.store.get("access") or {}).items()
                       if value.get("enabled") and value.get("version") == 1],
                      key=lambda profile: profile["name"].casefold())

    def room(self, uid, room_id):
        self.admitted(uid)
        room = self.store.get(self.ROOT + "/rooms/" + safe_id(room_id))
        if not room or not room.get("members", {}).get(uid):
            raise MessengerError("Conversation not found", 404)
        return room

    def room_profiles(self, room):
        result = []
        for uid in room["members"]:
            try:
                result.append(self.profile(uid))
            except MessengerError:
                result.append({"uid": uid, "name": "Account unavailable", "color": "#777777", "emoji": ""})
        return result

    def create_room(self, uid, member_ids, kind="direct", title=""):
        self.admitted(uid)
        if not self.operator_id:
            raise MessengerError("Telegram setup is not configured", 503)
        if not isinstance(member_ids, list) or kind not in {"direct", "group"}:
            raise MessengerError("Invalid conversation")
        ids = sorted({safe_id(value) for value in member_ids} | {uid})
        if len(ids) < 2 or len(ids) > 32 or (kind == "direct" and len(ids) != 2):
            raise MessengerError("Choose 1 person, or up to 31 for a group")
        for member_id in ids:
            self.admitted(member_id)
        room_id = ("d_" + hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:32]
                   if kind == "direct" else "g_" + secrets.token_hex(16))
        proposed = {"kind": kind, "title": str(title).strip()[:80] if kind == "group" else "",
                    "members": dict.fromkeys(ids, True), "createdBy": uid,
                    "createdAt": now_ms(), "updatedAt": now_ms(), "telegram": {"state": "off"}}
        self.store.transaction(self.ROOT + "/rooms/" + room_id, lambda old: old or proposed)
        self.store.update(self.ROOT + "/user_rooms", {member_id + "/" + room_id: True for member_id in ids})
        self.request_telegram(uid, room_id)
        return self.describe_room(uid, room_id)

    def describe_room(self, uid, room_id):
        room = self.room(uid, room_id)
        profiles = self.room_profiles(room)
        title = room.get("title") or " · ".join(
            profile["name"] for profile in profiles if room["kind"] == "group" or profile["uid"] != uid)
        rows = self.store.page(self.ROOT + "/messages/" + room_id, limit=1)
        latest = self.message_view(room_id, *rows[-1]) if rows else None
        read_key = self.store.get(self.ROOT + "/read/" + uid + "/" + room_id) or ""
        telegram = room.get("telegram", {})
        return {"id": room_id, "kind": room["kind"], "title": title, "members": profiles,
                "updatedAt": room.get("updatedAt", room["createdAt"]), "lastMessage": latest,
                "unread": bool(latest and latest["id"] > read_key and latest["senderUid"] != uid),
                "telegram": {"state": telegram.get("state", "off"),
                             "invite": telegram.get("invite") if telegram.get("state") == "ready" else None}}

    def rooms(self, uid):
        self.admitted(uid)
        result = []
        for room_id in (self.store.get(self.ROOT + "/user_rooms/" + uid) or {}):
            try:
                result.append(self.describe_room(uid, room_id))
            except MessengerError as error:
                if error.status != 404:
                    raise
        return sorted(result, key=lambda room: room["updatedAt"], reverse=True)

    def existing_message(self, uid, room_id, client_id):
        self.room(uid, room_id)
        digest = hashlib.sha256((room_id + ":" + uid + ":" + client_id).encode()).hexdigest()
        key = self.store.get(self.ROOT + "/idempotency/" + digest)
        record = self.store.get(self.ROOT + "/messages/" + room_id + "/" + key) if key else None
        return self.message_view(room_id, key, record) if record else None

    def append(self, uid, room_id, client_id, text="", image=None, origin="site", telegram_id=None):
        room = self.room(uid, room_id)
        if room.get("telegram", {}).get("state") != "ready":
            raise MessengerError("Создание беседы", 409)
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", client_id):
            raise MessengerError("Invalid message ID")
        if not isinstance(text, str) or len(text) > 4000:
            raise MessengerError("Message is too long")
        text = text.strip()
        if not text and not image:
            raise MessengerError("Message is empty")
        digest = hashlib.sha256((room_id + ":" + uid + ":" + client_id).encode()).hexdigest()
        key = self.store.transaction(self.ROOT + "/idempotency/" + digest,
                                     lambda old: old or self.store.new_key(self.ROOT + "/messages/" + room_id))
        payload = {"roomId": room_id, "senderUid": uid, "text": text, "image": image}
        proposal = {"senderUid": uid, "createdAt": now_ms(), "ciphertext": self.cipher.seal(payload),
                    "origin": origin, "telegramState": "native" if origin == "telegram" else "queued",
                    "telegramMessageId": telegram_id}
        record = self.store.transaction(self.ROOT + "/messages/" + room_id + "/" + key, lambda old: old or proposal)
        self.store.transaction(self.ROOT + "/rooms/" + room_id + "/updatedAt",
                               lambda old: max(old or 0, record["createdAt"]))
        return self.message_view(room_id, key, record)

    def image_message(self, uid, room_id, client_id, raw, text="", origin="site", telegram_id=None):
        if self.media is None:
            raise MessengerError("Image sharing is disabled", 403)
        room = self.room(uid, room_id)
        if room.get("telegram", {}).get("state") != "ready":
            raise MessengerError("Создание беседы", 409)
        with self.locks["image:" + room_id + ":" + uid + ":" + str(client_id)]:
            existing = self.existing_message(uid, room_id, str(client_id))
            if existing:
                return existing
            with self.media_slots:
                clean, width, height = normalize_image(raw)
            image_id = secrets.token_hex(16)
            self.media.put(room_id + "/" + image_id, self.cipher.encrypt(clean))
            self.store.set(self.ROOT + "/media/" + room_id + "/" + image_id, {"uid": uid, "createdAt": now_ms()})
            try:
                return self.append(uid, room_id, client_id, text,
                                   {"id": image_id, "width": width, "height": height}, origin, telegram_id)
            except Exception:
                self.media.delete(room_id + "/" + image_id)
                self.store.set(self.ROOT + "/media/" + room_id + "/" + image_id, None)
                raise

    def image(self, uid, room_id, image_id):
        self.room(uid, room_id)
        if not self.store.get(self.ROOT + "/media/" + room_id + "/" + safe_id(image_id)):
            raise MessengerError("Image not found", 404)
        return self.cipher.decrypt(self.media.get(room_id + "/" + image_id))

    def message_view(self, room_id, key, record):
        payload = self.cipher.open(record["ciphertext"])
        if payload["roomId"] != room_id or payload["senderUid"] != record["senderUid"]:
            raise MessengerError("Encrypted content does not match conversation", 503)
        return {"id": key, "senderUid": record["senderUid"], "createdAt": record["createdAt"],
                "text": payload["text"], "image": payload.get("image"),
                "kind": payload.get("kind", "message"), "invite": payload.get("invite"),
                "origin": record["origin"], "telegramState": record["telegramState"]}

    def messages(self, uid, room_id, before=None, after=None):
        self.room(uid, room_id)
        if before:
            safe_id(before)
        if after:
            safe_id(after)
        rows = self.store.page(self.ROOT + "/messages/" + room_id, before, after, limit=50)
        has_more = len(rows) > 50
        rows = rows[:50] if after else rows[-50:]
        return {"messages": [self.message_view(room_id, key, value) for key, value in rows], "hasMore": has_more}

    def mark_read(self, uid, room_id, message_id):
        self.room(uid, room_id)
        if not self.store.get(self.ROOT + "/messages/" + room_id + "/" + safe_id(message_id)):
            raise MessengerError("Message not found", 404)
        self.store.transaction(self.ROOT + "/read/" + uid + "/" + room_id, lambda old: max(old or "", message_id))

    def request_telegram(self, uid, room_id):
        self.room(uid, room_id)
        if not self.operator_id:
            raise MessengerError("Telegram setup is not configured", 503)
        return self.store.transaction(self.ROOT + "/rooms/" + room_id + "/telegram",
            lambda old: old if old and old.get("state") != "off" else
            {"state": "queued", "requestId": secrets.token_hex(8), "requestedAt": now_ms()})

    def queued_setup(self):
        active = self.store.get(self.ROOT + "/setup_active")
        if active:
            room = self.store.get(self.ROOT + "/rooms/" + active["roomId"])
            if room and room.get("telegram", {}).get("state") in {"queued", "assigned", "awaiting_exit"}:
                return active, room
            self.store.set(self.ROOT + "/setup_active", None)
        rooms = self.store.get(self.ROOT + "/rooms") or {}
        pending = [(key, room) for key, room in rooms.items() if room.get("telegram", {}).get("state") == "queued"]
        if not pending:
            return None
        key, room = min(pending, key=lambda item: item[1]["telegram"]["requestedAt"])
        desired = {"roomId": key, "requestId": room["telegram"]["requestId"]}
        active = self.store.transaction(self.ROOT + "/setup_active", lambda old: old or desired)
        return active, self.store.get(self.ROOT + "/rooms/" + active["roomId"])

    def bind_group(self, operator_id, group_id):
        if str(operator_id) != self.operator_id:
            raise MessengerError("Only the setup operator can bind groups", 403)
        with self.locks["setup"]:
            setup = self.queued_setup()
            if not setup:
                raise MessengerError("No active setup request", 409)
            active, room = setup
            prior = self.store.get(self.ROOT + "/telegram_groups/" + str(group_id))
            if prior and prior != active["roomId"]:
                raise MessengerError("Group is already linked", 409)
            telegram = room["telegram"]
            if telegram.get("groupId") and telegram["groupId"] != group_id:
                raise MessengerError("Another group is already attached to this request", 409)
            self.store.update(self.ROOT, {
                "telegram_groups/" + str(group_id): active["roomId"],
                "rooms/" + active["roomId"] + "/telegram/groupId": group_id,
                "rooms/" + active["roomId"] + "/telegram/state": "awaiting_exit"})
            return active["roomId"], room

    def activate_group(self, operator_id, group_id, invite):
        if str(operator_id) != self.operator_id:
            raise MessengerError("Invalid operator", 403)
        room_id = self.store.get(self.ROOT + "/telegram_groups/" + str(group_id))
        room = self.store.get(self.ROOT + "/rooms/" + str(room_id)) or {}
        telegram = room.get("telegram", {})
        if telegram.get("state") != "awaiting_exit" or not telegram.get("exitConfirmed"):
            raise MessengerError("Group is not waiting for creator exit", 409)
        if not re.fullmatch(r"https://t\.me/\+[A-Za-z0-9_-]+", invite):
            raise MessengerError("Invalid invitation", 409)
        key = self.store.transaction(self.ROOT + "/rooms/" + room_id + "/telegram/inviteMessageId",
            lambda old: old or self.store.new_key(self.ROOT + "/messages/" + room_id))
        timestamp = now_ms()
        payload = {"roomId": room_id, "senderUid": "_system", "kind": "telegram_invite",
                   "text": "Беседа создана. Можно вступить в Telegram-группу или продолжить здесь.",
                   "image": None, "invite": invite}
        message = {"senderUid": "_system", "createdAt": timestamp, "ciphertext": self.cipher.seal(payload),
                   "origin": "system", "telegramState": "native", "telegramMessageId": None}
        self.store.update(self.ROOT, {
            "messages/" + room_id + "/" + key: message,
            "rooms/" + room_id + "/updatedAt": timestamp,
            "rooms/" + room_id + "/telegram/state": "ready",
            "rooms/" + room_id + "/telegram/invite": invite,
            "rooms/" + room_id + "/telegram/operatorLeftAt": now_ms()})
        self.store.transaction(self.ROOT + "/setup_active",
            lambda old: None if old and old.get("roomId") == room_id else old)
        return room_id

    def site_uid_for_telegram(self, telegram_uid):
        for key, link in (self.store.get(self.SESSION + "/telegram_links") or {}).items():
            tg = link.get("tgUserId") or link.get("telegramUserId") or link.get("tg_user_id")
            uid = link.get("siteUserId") or link.get("uid")
            if str(tg) == str(telegram_uid):
                uid = uid or key
            elif str(key) != str(telegram_uid) or not uid:
                continue
            if uid:
                try:
                    self.admitted(uid)
                    return uid
                except MessengerError:
                    return None
        return None

    def operator_from_site_name(self, name="Salyose"):
        matches = [uid for uid, account in (self.store.get("access") or {}).items()
                   if account.get("enabled") and account.get("version") == 1
                   and str(account.get("name", "")).casefold() == name.casefold()]
        if len(matches) != 1:
            return ""
        candidates = set()
        for key, link in (self.store.get(self.SESSION + "/telegram_links") or {}).items():
            uid = link.get("siteUserId") or link.get("uid") or key
            tg = link.get("tgUserId") or link.get("telegramUserId") or link.get("tg_user_id")
            if not tg and str(key).isdigit():
                tg = key
            if uid == matches[0] and str(tg).isdigit():
                candidates.add(str(tg))
        return candidates.pop() if len(candidates) == 1 else ""
