# Reddit launch draft — v2.2.1

Do not publish until the CA listing is approved and searchable. Start with
r/unRAID. A separate r/selfhosted post must follow that community's current
new-project rules rather than duplicating this announcement.

## Suggested title

I built a bilingual AI translation overlay for Calibre-Web-Automated (Unraid CA)

## Draft

CWA eBook Translate v2.2.1 translates ebook paragraphs while you read and can
show the original and translation together. It works with local
OpenAI-compatible LLM servers or optional cloud providers, keeps a bounded
private cache, and authenticates through the existing CWA session.

The first Community Applications profile targets Unraid 7.3.2 x86_64, CWA
4.0.6, native CWA sessions, and a local LLM. The container runs non-root and
does not publish its internal API. More advanced installs can use the source
`btctl` workflow for split roles, rollback, and Authentik.

Source, installation, demo, limitations, and support:
https://github.com/felixapel/CWA-eBook-Translate-Plugin

I am the sole maintainer, so exact reproduction details and redacted evidence
are especially helpful. This project is not affiliated with CWA or any model
provider.
