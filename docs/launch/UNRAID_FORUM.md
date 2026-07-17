# Unraid forum launch draft — v2.2.1

Publish this only after the v2.2.1 tag, Gitea release workflow, public GHCR
digest, physical Unraid acceptance, searchable Community Applications listing,
and support links are final.

## Title

CWA eBook Translate v2.2.1 — bilingual AI translation for Calibre-Web-Automated

## Post

CWA eBook Translate adds an original-plus-translation reading mode to
Calibre-Web-Automated. It supports local OpenAI-compatible servers such as
vLLM, Ollama, LM Studio, and llama.cpp, plus optional cloud providers.

The Community Applications template is designed for an existing CWA container.
It runs non-root with a read-only root filesystem, validates the existing CWA
session, and does not publish the internal translation API port. The more
advanced `btctl` path remains available for a split API/proxy topology,
upgrades, rollback, and Authentik deployments.

Before installing, read the compatibility matrix and create the documented
private appdata directory. Support and source:
https://github.com/felixapel/CWA-eBook-Translate-Plugin

There is no project API key or telemetry. Local models keep provider prompts on
your network; optional cloud providers receive selected text only under the
documented consent boundary.

This is an early-adopter release. Please report the exact v2.2.1 image digest,
CWA/Unraid versions, auth profile, and redacted doctor/log evidence with bugs.
