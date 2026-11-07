import logging
import os
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger("backup")
BASE_DIR = Path(__file__).resolve().parent
SCOPES = ["https://www.googleapis.com/auth/drive"]


def load_backup_targets(config_file: str = "backup_targets.txt") -> list[str]:
    cfg_path = BASE_DIR / config_file
    if not cfg_path.exists():
        logger.warning("Configuration file %s not found. Using default targets.", config_file)
        return [
            "world",
            "config",
            "defaultconfigs",
            "local",
            "server.properties",
            "ops.json",
            "whitelist.json",
            "banned-players.json",
            "banned-ips.json",
            "usercache.json",
            "usernamecache.json",
            "server-icon.png",
        ]

    with open(cfg_path, "r", encoding="utf-8") as f:
        targets = [
            line.strip()
            for line in f
            if line.strip() and not line.startswith("#")
        ]
    logger.debug("Loaded %d backup targets from %s", len(targets), config_file)
    return targets


def send_rcon_command(host: str, port: int, password: str, command: str) -> None:
    try:
        from mcrcon import MCRcon

        with MCRcon(host, password, port=port) as mcr:
            mcr.command(command)
        logger.debug("RCON command executed successfully: %s", command)
    except Exception as exc:
        logger.warning("RCON command '%s' failed: %s", command, exc)


def create_tar_archive(mc_dir: Path, temp_dir: Path, rcon_cfg: dict, targets: list[str]) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = temp_dir / f"mc_backup_{timestamp}.tar.gz"

    if rcon_cfg.get("enabled"):
        logger.info("Suspending level saves via RCON...")
        send_rcon_command(rcon_cfg["host"], rcon_cfg["port"], rcon_cfg["password"], "/save-off")
        send_rcon_command(rcon_cfg["host"], rcon_cfg["port"], rcon_cfg["password"], "/save-all flush")

    try:
        logger.info("Packaging server files into %s", archive_path.name)
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in targets:
                target_path = mc_dir / item
                if target_path.exists():
                    logger.debug("Adding: %s", item)
                    tar.add(target_path, arcname=item)
                else:
                    logger.debug("Skipping missing target: %s", item)
    finally:
        if rcon_cfg.get("enabled"):
            logger.info("Resuming level saves via RCON...")
            send_rcon_command(rcon_cfg["host"], rcon_cfg["port"], rcon_cfg["password"], "/save-on")

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    logger.info("Archive successfully created: %s (%.2f MB)", archive_path.name, size_mb)
    return archive_path


def get_drive_client(credentials_path: Path, token_path: Path):
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("OAuth token expired, refreshing via token.json...")
            creds.refresh(Request())
        else:
            logger.info("Acquiring new OAuth user credentials via browser flow...")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        logger.info("Authorization credentials saved to %s", token_path.name)

    return build("drive", "v3", credentials=creds)


def upload_to_gdrive(service, folder_id: str, file_path: Path) -> str:
    logger.info("Uploading %s to Google Drive...", file_path.name)
    metadata = {
        "name": file_path.name,
        "parents": [folder_id],
    }
    media = MediaFileUpload(str(file_path), mimetype="application/gzip", resumable=True)
    result = (
        service.files()
        .create(body=metadata, media_body=media, fields="id, name")
        .execute()
    )
    file_id = result.get("id")
    logger.info("Upload complete. Remote File ID: %s", file_id)
    return file_id


def prune_gdrive_backups(service, folder_id: str, max_backups: int) -> None:
    logger.info("Evaluating remote retention policy (limit: %d)", max_backups)
    query = (
        f"'{folder_id}' in parents and trashed = false and "
        f"mimeType != 'application/vnd.google-apps.folder'"
    )
    response = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name, createdTime)",
            orderBy="createdTime asc",
        )
        .execute()
    )
    files = response.get("files", [])
    total_files = len(files)
    logger.info("Found %d remote backup archive(s)", total_files)

    if total_files > max_backups:
        excess_count = total_files - max_backups
        to_delete = files[:excess_count]
        for f in to_delete:
            logger.info("Pruning expired backup: %s (ID: %s)", f["name"], f["id"])
            service.files().delete(fileId=f["id"]).execute()


def run_backup(skip_rcon: bool = False) -> bool:
    load_dotenv(BASE_DIR / ".env")

    mc_dir = Path(os.getenv("MC_DIR", ".")).resolve()
    temp_dir = Path(os.getenv("TEMP_BACKUP_DIR", "/tmp/mc-backups")).resolve()
    folder_id = os.getenv("GDRIVE_FOLDER_ID")
    max_backups = int(os.getenv("MAX_BACKUPS", "5"))

    credentials_path = (BASE_DIR / os.getenv("CREDENTIALS_FILE", "credentials.json")).resolve()
    token_path = (BASE_DIR / os.getenv("TOKEN_FILE", "token.json")).resolve()

    rcon_cfg = {
        "enabled": False if skip_rcon else (os.getenv("RCON_ENABLED", "false").lower() == "true"),
        "host": os.getenv("RCON_HOST", "127.0.0.1"),
        "port": int(os.getenv("RCON_PORT", "25575")),
        "password": os.getenv("RCON_PASSWORD", ""),
    }
    
    if not mc_dir.exists():
        logger.error("Minecraft directory does not exist: %s", mc_dir)
        return False

    if not credentials_path.exists() and not token_path.exists():
        logger.error("OAuth client file missing: %s", credentials_path)
        return False

    if not folder_id:
        logger.error("GDRIVE_FOLDER_ID is unset in configuration")
        return False

    archive_path = None
    try:
        targets = load_backup_targets("backup_targets.txt")
        drive_service = get_drive_client(credentials_path, token_path)
        archive_path = create_tar_archive(mc_dir, temp_dir, rcon_cfg, targets)
        upload_to_gdrive(drive_service, folder_id, archive_path)
        prune_gdrive_backups(drive_service, folder_id, max_backups)
        logger.info("Backup pipeline finished successfully")
        return True
    except Exception as exc:
        logger.exception("Backup execution failed: %s", exc)
        return False
    finally:
        if archive_path and archive_path.exists():
            archive_path.unlink()
            logger.debug("Cleaned up local temporary archive: %s", archive_path)


if __name__ == "__main__":
    from update import setup_logging
    setup_logging()
    success = run_backup()
    sys.exit(0 if success else 1)