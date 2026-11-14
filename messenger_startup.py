"""Start optional messenger without disabling the existing public chat bridge."""
import logging
import os
import threading

from firebase_admin import auth, db, storage
from messenger_core import Cipher, FirebaseMedia, FirebaseStore, MessengerService
from messenger_http import create_app
from messenger_telegram import MessengerTelegram

LOG = logging.getLogger(__name__)


def start_messenger(application, protected_chat_ids=()):
    from waitress import create_server

    keys = os.getenv("MESSENGER_ENCRYPTION_KEYS", "")
    service = None
    if keys:
        service = MessengerService(
            FirebaseStore(db), Cipher(keys),
            FirebaseMedia(storage.bucket(os.environ["MESSENGER_STORAGE_BUCKET"]))
            if os.getenv("MESSENGER_STORAGE_BUCKET") else None,
            os.getenv("MESSENGER_OPERATOR_TG_ID"))
        if not service.operator_id:
            service.operator_id = service.operator_from_site_name(os.getenv("MESSENGER_OPERATOR_SITE_NAME", "Salyose"))
    origins = os.getenv("MESSENGER_ALLOWED_ORIGINS",
                        "https://xn--90ahbmokbvugf3b.xn--p1ai").split(",")
    app = create_app(service, lambda token: auth.verify_id_token(token, check_revoked=True),
                     [origin.strip() for origin in origins if origin.strip()])
    server = create_server(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")),
                           threads=6, max_request_body_size=9 * 1024 * 1024,
                           clear_untrusted_proxy_headers=True)
    threading.Thread(target=server.run, name="messenger-api", daemon=True).start()
    transport = MessengerTelegram(service, application, protected_chat_ids).install() if service else None
    if not service:
        LOG.warning("Messenger disabled: configure MESSENGER_ENCRYPTION_KEYS")
    return server, transport
