"""Authenticated HTTP API. TLS is terminated by Fly.io."""
import io
import logging
import time
from collections import defaultdict, deque
from functools import wraps

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from messenger_core import MessengerError

LOG = logging.getLogger(__name__)


def create_app(service, verify_token, origins):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=9 * 1024 * 1024)
    allowed_origins = set(origins)
    requests_by_user = defaultdict(deque)

    @app.before_request
    def check_origin():
        origin = request.headers.get("Origin")
        if origin and origin not in allowed_origins:
            raise MessengerError("Origin is not allowed", 403)
        if request.method == "OPTIONS":
            return "", 204

    @app.after_request
    def headers(response):
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(Exception)
    def error_response(error):
        if isinstance(error, MessengerError):
            return jsonify(error=str(error)), error.status
        if isinstance(error, HTTPException):
            return jsonify(error=error.name), error.code
        LOG.exception("Messenger request failed")
        return jsonify(error="Messenger is temporarily unavailable"), 503

    @app.get("/health")
    def health():
        return jsonify(ok=True, messenger=service is not None)

    def authorized(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if service is None:
                raise MessengerError("Messenger is not configured yet", 503)
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or len(header) > 8192:
                raise MessengerError("Sign in to continue", 401)
            try:
                identity = verify_token(header[7:])
                if identity.get("firebase", {}).get("sign_in_provider") != "password":
                    raise ValueError("Unsupported identity")
                uid = identity["uid"]
            except Exception as error:
                raise MessengerError("Sign in again", 401) from error
            service.admitted(uid)
            timestamp = time.monotonic()
            history = requests_by_user[uid]
            while history and history[0] < timestamp - 60:
                history.popleft()
            if len(history) >= 150:
                raise MessengerError("Too many requests. Please wait", 429)
            history.append(timestamp)
            return fn(uid, *args, **kwargs)
        return wrapped

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise MessengerError("Expected a JSON object")
        return value

    prefix = "/messenger/api"

    @app.get(prefix + "/me")
    @authorized
    def me(uid):
        return jsonify(user=service.profile(uid), telegramEnabled=bool(service.operator_id), imagesEnabled=service.media is not None)

    @app.get(prefix + "/members")
    @authorized
    def members(uid):
        return jsonify(members=service.members(uid))

    @app.get(prefix + "/rooms")
    @authorized
    def rooms(uid):
        return jsonify(rooms=service.rooms(uid))

    @app.post(prefix + "/rooms")
    @authorized
    def create_room(uid):
        data = body()
        room = service.create_room(uid, data.get("members"), data.get("kind", "direct"), data.get("title", ""))
        return jsonify(room=room), 201

    @app.get(prefix + "/rooms/<room_id>/messages")
    @authorized
    def messages(uid, room_id):
        return jsonify(service.messages(uid, room_id, request.args.get("before"), request.args.get("after")))

    @app.post(prefix + "/rooms/<room_id>/messages")
    @authorized
    def post_message(uid, room_id):
        data = body()
        return jsonify(message=service.append(uid, room_id, data.get("clientId"), data.get("text", ""))), 201

    @app.post(prefix + "/rooms/<room_id>/images")
    @authorized
    def post_image(uid, room_id):
        image = request.files.get("image")
        if not image:
            raise MessengerError("Choose an image")
        message = service.image_message(uid, room_id, request.form.get("clientId"),
                                        image.read(8 * 1024 * 1024 + 1), request.form.get("text", ""))
        return jsonify(message=message), 201

    @app.get(prefix + "/rooms/<room_id>/images/<image_id>")
    @authorized
    def image(uid, room_id, image_id):
        return send_file(io.BytesIO(service.image(uid, room_id, image_id)), mimetype="image/webp")

    @app.post(prefix + "/rooms/<room_id>/read")
    @authorized
    def read(uid, room_id):
        service.mark_read(uid, room_id, body().get("messageId"))
        return jsonify(ok=True)

    @app.post(prefix + "/rooms/<room_id>/telegram")
    @authorized
    def telegram(uid, room_id):
        service.request_telegram(uid, room_id)
        return jsonify(room=service.describe_room(uid, room_id))

    return app
