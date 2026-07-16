# Community Applications submission packet — v2.2.1

This file is a release checklist, not an installation guide.

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
4. Run the manual GHCR workflow for that exact tag and SHA.
5. Repeat physical acceptance using the pulled digest and final XML.
6. Update the public template repository to that digest, Validate and Scan at
   the CA submission portal, then submit.
7. Publish forum and Reddit announcements only after the CA listing is live.

Stop on a mutable image reference, published port 8390, failed scan/smoke,
tag/SHA mismatch, missing physical acceptance, or non-searchable CA listing.
