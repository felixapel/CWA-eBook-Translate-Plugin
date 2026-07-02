# Security Policy

## Supported Versions

Only the latest release (and `main`) receives security fixes.

## Reporting a Vulnerability

Please report vulnerabilities privately via **GitHub Security Advisories**
("Report a vulnerability" on the repository's Security tab) rather than a
public issue. You should get a first response within 7 days.

## Scope notes for self-hosters

- The translation API is designed for LAN/behind-your-own-proxy use. If you
  expose it beyond your LAN, set `BT_API_TOKEN` (shared secret), keep the
  rate limits on, and prefer proxy-injection mode so the API is never
  reachable except through your authenticated CWA domain.
- The API never stores your provider API keys anywhere except the container
  environment you configure; the SQLite cache contains only paragraph text
  and translations.
