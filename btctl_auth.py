"""Render reviewed Authentik identity-edge fragments for the managed API route."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from btctl_core import ConfigError, DeploymentPlan, InstallConfig


@dataclass(frozen=True, slots=True)
class AuthentikEdgeArtifact:
    filename: str
    content: str


def _nginx(config: InstallConfig, api: str) -> str:
    return f"""# CWA Translate v{config.identity.version}: paste inside the existing HTTPS server block.
# Keep the standard Authentik sign-in/outpost locations for the rest of the host.
location ^~ /bt-api/ {{
    auth_request /outpost.goauthentik.io/auth/nginx;
    auth_request_set $bt_authentik_uid $upstream_http_x_authentik_uid;
    if ($bt_authentik_uid = "") {{ return 401; }}

    proxy_pass {api}/;
    proxy_set_header X-authentik-uid $bt_authentik_uid;
    proxy_set_header Cookie "";
    proxy_set_header X-BT-Subject "";
    proxy_set_header X-BT-Roles "";
    proxy_set_header Forwarded "";
    proxy_set_header X-Forwarded-For $remote_addr;
}}

location = /outpost.goauthentik.io/auth/nginx {{
    internal;
    proxy_pass {config.authentik_outpost_url}/outpost.goauthentik.io/auth/nginx;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
    proxy_set_header X-Original-Method $request_method;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Cookie $http_cookie;
}}
"""


def _traefik(config: InstallConfig, api: str) -> str:
    host = urlsplit(config.public_origin).hostname
    prefix = config.install_name
    return f"""# CWA Translate v{config.identity.version}: merge into Traefik dynamic configuration.
http:
  routers:
    {prefix}-api:
      rule: "Host(`{host}`) && PathPrefix(`/bt-api/`)"
      priority: 1000
      middlewares:
        - {prefix}-clear-client-identity
        - {prefix}-authentik
        - {prefix}-sanitize-api
        - {prefix}-strip-prefix
      service: {prefix}-api
  middlewares:
    {prefix}-clear-client-identity:
      headers:
        customRequestHeaders:
          X-authentik-uid: ""
          X-BT-Subject: ""
          X-BT-Roles: ""
    {prefix}-authentik:
      forwardAuth:
        address: "{config.authentik_outpost_url}/outpost.goauthentik.io/auth/traefik"
        trustForwardHeader: false
        authResponseHeaders:
          - X-authentik-uid
    {prefix}-sanitize-api:
      headers:
        customRequestHeaders:
          Cookie: ""
          X-BT-Subject: ""
          X-BT-Roles: ""
    {prefix}-strip-prefix:
      stripPrefix:
        prefixes:
          - /bt-api
  services:
    {prefix}-api:
      loadBalancer:
        servers:
          - url: "{api}"
"""


def _caddy(config: InstallConfig, api: str) -> str:
    return f"""# CWA Translate v{config.identity.version}: paste inside the existing site block.
@cwa_translate_api path /bt-api/*
handle @cwa_translate_api {{
    route {{
        request_header -X-authentik-uid
        request_header -X-BT-Subject
        request_header -X-BT-Roles
        forward_auth {config.authentik_outpost_url} {{
            uri /outpost.goauthentik.io/auth/caddy
            copy_headers X-Authentik-Uid
        }}
        uri strip_prefix /bt-api
        request_header -Cookie
        request_header -X-BT-Subject
        request_header -X-BT-Roles
        reverse_proxy {api}
    }}
}}
"""


def render_authentik_edge(
    config: InstallConfig, plan: DeploymentPlan
) -> AuthentikEdgeArtifact:
    """Return a non-secret edge fragment that authenticates only `/bt-api/`."""
    if config.auth_profile != "authentik-forwarded":
        raise ConfigError("auth snippet requires BT_AUTH_PROFILE=authentik-forwarded")
    api = f"http://{plan.resources['api']['name']}:8390"
    renderers = {
        "nginx": ("authentik-edge.nginx.conf", _nginx),
        "traefik": ("authentik-edge.traefik.yml", _traefik),
        "caddy": ("authentik-edge.caddy", _caddy),
    }
    try:
        filename, renderer = renderers[config.reverse_proxy]
    except KeyError as exc:
        raise ConfigError("unsupported Authentik reverse proxy") from exc
    return AuthentikEdgeArtifact(filename, renderer(config, api))
