# Community Applications submission packet — v2.2.1

This file is a release checklist, not an installation guide.

The public template repository must remain quarantined while this checklist is
incomplete. A historical or cached XML is not a release candidate.

Template repository:
[`felixapel/unraid-templates`](https://github.com/felixapel/unraid-templates)

## Certified first profile

- Unraid 7.3.2, x86_64
- Calibre-Web-Automated 4.0.6
- native CWA session authentication, including strong session protection
- local OpenAI-compatible LLM endpoint
- one non-root `BT_ROLE=all` container
- public proxy container port 8080 (host default 8385)
- internal API port 8390 not published
- private bind at `/mnt/user/appdata/cwa-translate-ca/data`, owned by 101:102

Prepare storage before installing:

```bash
install -d -m 0700 -o 101 -g 102 /mnt/user/appdata/cwa-translate-ca/data
```

## Submission order

1. Validate the exact v2.2.1 candidate locally and with the repository smoke.
2. Have Claude Code perform physical Unraid acceptance and record commit/image
   digest plus results.
3. Publish the annotated tag through GitHub first and Gitea second; wait for the
   natural Gitea workflow without rerunning it.
4. Confirm the GHCR package is public and the unused `2.2.1` tag can be checked
   anonymously, then run the manual GHCR workflow for that exact tag and SHA.
5. Record `GHCR_DIGEST`, prove anonymously that tag `2.2.1` resolves to that
   digest, and repeat physical acceptance using the pulled digest and final XML.
6. Replace the quarantine with the reviewed digest-pinned XML, Validate and
   Scan at the CA submission portal, then submit.
7. Publish forum and Reddit announcements only after the CA listing is live.

Stop on a mutable image reference, published port 8390, failed scan/smoke,
tag/SHA/digest mismatch, private package, missing physical acceptance, or
non-searchable CA listing.
