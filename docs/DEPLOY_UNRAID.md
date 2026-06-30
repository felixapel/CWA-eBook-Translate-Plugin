# Deploying to Unraid

This document details how to deploy and update the `book-translator` project on an Unraid server.

## Prerequisites

- SSH access to your Unraid server (configured in Unraid Settings > SSH).
- Docker and Docker Compose installed (usually standard on modern Unraid).
- Calibre-Web-Automated container installed.

## Deployment Steps

1. Clone or download the repository onto your local machine.
2. Ensure you can SSH into Unraid without interactive prompts (recommended) or prepare your password.
3. Run the automated deployment script:
   ```bash
   ./deploy_unraid.sh
   ```
4. This script will:
   - Connect to Unraid.
   - Pull the latest backend code to `/opt/book-translator`.
   - Setup the correct environment variables in `/opt/book-translator/.env`.
   - Rebuild and start the `book-translator-api` container.
   - Backup the old overlay files to `/mnt/user/appdata/calibre-web-automated/overlay/backups/`.
   - Copy the new `translator.js` and `translator.css` to `/mnt/user/appdata/calibre-web-automated/overlay/`.

5. Run verification:
   ```bash
   ./verify_unraid.sh
   ```

## Rollback Instructions

If anything fails, you can roll back the frontend files using the backup copy created during deployment:
```bash
ssh root@192.168.0.122 "cp /mnt/user/appdata/calibre-web-automated/overlay/backups/translator_YYYYMMDD_HHMMSS.js /mnt/user/appdata/calibre-web-automated/overlay/translator.js"
```
Replace the timestamp with the one generated during your deployment.
