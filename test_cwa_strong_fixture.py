"""Small CWA v4.0.6 strong-session fixture for auth regression tests.

The fixture deliberately mirrors only the upstream contract used by this
project: ProxyFix supplies ``request.remote_addr``, the session identifier
binds that address to User-Agent, and ``/ajax/emailstat`` returns HTML for an
unauthenticated session or a JSON task list for an authenticated one.
"""

from __future__ import annotations

import hashlib
import os
from http.cookies import SimpleCookie

from flask import Flask, jsonify, request, session
from werkzeug.middleware.proxy_fix import ProxyFix


def _session_identifier() -> str:
    address = request.remote_addr
    if address is not None:
        address = address.encode("utf-8")
    user_agent = request.headers.get("User-Agent")
    if user_agent is not None:
        user_agent = user_agent.encode("utf-8")
    material = f"{address}|{user_agent}"
    return hashlib.sha512(material.encode("utf-8")).hexdigest()


def create_cwa_strong_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "cwa-strong-session-test-only"
    app.config.update(
        CWA_CONFIG_SESSION=1,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_NAME="session",
    )
    # CWA v4.0.6 defaults TRUSTED_PROXY_COUNT to one.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

    @app.get("/fixture/login")
    def login():
        session.clear()
        session["_user_id"] = request.args.get("user", "fixture-user")
        session["_random"] = "fixture-random"
        session["_fresh"] = True
        session["_id"] = _session_identifier()
        return jsonify({"authenticated": True})

    @app.get("/ajax/emailstat")
    def email_status():
        if (
            app.config["CWA_CONFIG_SESSION"] == 1
            and session.get("_user_id")
            and session.get("_id") != _session_identifier()
        ):
            session.clear()
            session["_remember"] = "clear"
        if not session.get("_user_id"):
            return "<!doctype html><title>Login</title>", 200, {
                "Content-Type": "text/html; charset=utf-8"
            }
        return jsonify([{"error": None, "progress": "100 %"}])

    return app


def session_cookie_from(response) -> str:
    parsed: SimpleCookie[str] = SimpleCookie()
    parsed.load(response.headers["Set-Cookie"])
    morsel = parsed["session"]
    return f"session={morsel.coded_value}"


if __name__ == "__main__":  # pragma: no cover - exercised by Docker smoke
    create_cwa_strong_app().run(
        host="0.0.0.0",
        port=int(os.environ.get("CWA_FIXTURE_PORT", "8083")),
        debug=False,
        use_reloader=False,
    )
