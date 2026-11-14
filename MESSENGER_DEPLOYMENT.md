# Private messenger deployment

Source baselines: site main a65c2850f085ae3cd1d85714f3d9dd89f7fb8de8; bot main 8cdc30aecd111be64d03df76e3ead0e96d37461a.

## Behaviour

- Only admitted Firebase password accounts can list people, create rooms, read history or retrieve images.
- Creating a room automatically queues Telegram setup. Sending text and images is locked until setup completes.
- Only one setup request is assigned to the operator at a time. The request contains an opaque number, never participant names.
- The operator creates an empty group, grants the bot admin rights to change information, invite/remove members and promote admins, then says "готово" inside that group.
- An optional invitation can be sent as a reply to the operator request. It is never trusted as the final published invitation.
- The bot binds the group, asks the operator to leave, and waits for Telegram's actual departure service event.
- Only after the departure is confirmed, with one bot remaining, does the bot set the room title/avatar and generate an invite requiring approval.
- The invite is persisted atomically as the first system message before the room becomes writable.
- Joining Telegram is optional. Website participants can continue entirely on the website after room creation.
- Join requests are checked against both the current site admission and the exact room membership.
- Verified entrants are promoted to admins, with permissions limited to those the bot can grant. They cannot invite others or promote new admins through this automatic grant.
- Public/RAT chat data, reactions and quote-game archives are not used to store private messages.

## Required Fly configuration

The existing BOT_TOKEN, CHAT_ID, FIREBASE_KEY_JSON and FIREBASE_DATABASE_URL remain required.

1. Enable the Firebase Storage bucket for this Firebase project. The service account must be allowed to read/write its objects.
2. Generate a Fernet key with a local Python installation:
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
3. Store it as the Fly secret MESSENGER_ENCRYPTION_KEYS. Keep a separate private backup. Never put the value in GitHub, a frontend config, logs, or the database.
4. Set MESSENGER_STORAGE_BUCKET if it differs from dpgames-66d73.firebasestorage.app.
5. Set MESSENGER_OPERATOR_TG_ID to the operator's numeric Telegram user ID. Alternatively, the bot resolves the uniquely admitted site account named Salyose through its existing Telegram binding. MESSENGER_OPERATOR_SITE_NAME changes that site account name.
6. The operator must have started the bot's private chat and must not block it.
7. MESSENGER_ALLOWED_ORIGINS defaults to https://xn--90ahbmokbvugf3b.xn--p1ai. Add other approved frontend origins as comma-separated exact origins.
8. Deploy the bot first. The existing Fly app exposes /health on port 8080; the API is under /messenger/api. The revised Fly VM has 512 MB for image decoding and the HTTP API.
9. Apply the site database.rules.json, preserving the existing admission rules. The new messenger namespace has no client read/write grants.
10. Deploy the matching frontend. /messenger/ is the standalone entry; the normal site adds a private-messages icon after sign-in.

If MESSENGER_ENCRYPTION_KEYS is absent, only the messenger is disabled and /health reports messenger:false. The existing public chat bridge keeps running. Invalid configured keys fail startup rather than silently writing plaintext.

The frontend defaults to https://dp-telegram-bot.fly.dev/messenger/api. A different deployment must set window.DP_MESSENGER_API before loading messenger.js. This is a trusted deployment setting, not a URL query parameter.

## Live acceptance test before release

Use two test site accounts and a fresh, empty Telegram group.

- Creating a conversation shows "Создание беседы"; neither HTTP nor the UI allows early messages.
- Operator receives no participant names.
- "готово" alone does not publish an invite or reveal the eventual title/avatar.
- Operator leaves the group; the bot remains an administrator.
- The first site message is the bot's invitation, and only then is the composer unlocked.
- Intended accounts can join; an unlinked user or a site user outside this room is declined.
- Each legitimate Telegram entrant is promoted.
- Text and PNG/JPEG/WebP images work in both directions; no forwarding loop occurs.
- A nonparticipant cannot read messages or images, including through direct API requests.
- Normal public chat, RAT switching, login and games still work.
- Restart the Fly machine: room state/history persist and the same encryption key can decrypt them.

The user already tested that Telegram permits the creator to leave a group with one administrator bot remaining. The new code's departure detection and routing still require the live acceptance test above.

## Privacy and operations

This is encrypted storage, not end-to-end encryption. The server and Telegram bridge can decrypt messages; Telegram groups are cloud chats. Room membership and delivery metadata are not encrypted. The setup operator receives no private room API access merely by being the operator.

Messages are encrypted before being stored in Firebase. Images are normalized, stripped of their original metadata, encrypted and stored as private-format objects. No bot file URLs, Firebase download tokens, or bearer tokens are sent in browser image URLs.

Photo input is limited to 8 MB and 24 million pixels, then resized to a 2560-pixel maximum edge. Only PNG, JPEG and WebP are accepted.

Clients use Firebase ID tokens in Authorization headers. Membership is checked on every request. Telegram bindings are checked again when a member joins or sends a message, and previously admitted Telegram members are audited before delivery; inaccessible owners/admins cause delivery to pause.

A Telegram network timeout may have an unknown send result. The bridge records an uncertain status instead of blindly retrying and duplicating a message. Messages remain available on the site. A process exit during sending can similarly leave a sending status; this also means delivery was not confirmed, not that it is safe to resend.

Rotation: put a new key first in MESSENGER_ENCRYPTION_KEYS and retain old keys, comma-separated, while older records remain. Removing the last key that can decrypt an old record loses access to that record.

Run one polling bot/API instance for this app. Do not scale polling replicas without adding a durable distributed setup/delivery lease. Back up encrypted records, Storage objects and encryption keys separately.

## Tests

Bot:
  python -m unittest discover -s tests -v

Site:
  node --experimental-vm-modules tests/auth.test.cjs
  node tests/messenger.browser.cjs

The browser test requires Playwright and installed Chromium. PLAYWRIGHT_MODULE and PLAYWRIGHT_CHROMIUM_EXECUTABLE can point to a bundled installation. Tests use explicit fake accounts and HTTP responses, never live credentials.
