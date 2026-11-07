import argparse
from datetime import datetime
import logging
import os
from pathlib import Path
import sys
import tarfile
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Ścieżka do logów zgodna z Twoim .env
log_path = BASE_DIR / os.getenv("LOG_FILE", "logs/szpontmc_manager.log")
log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Backup")

# Ścieżki i limity w 100% zgodne z Twoim .env
MC_DIR = Path(os.getenv("MC_DIR", "/home/ubuntu/mc-server")).resolve()
BACKUP_DIR = Path(os.getenv("TEMP_BACKUP_DIR", "/tmp/mc-backups")).resolve()
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "7"))

FULL_BACKUP_EXCLUDES = {
    "backups",
    ".git",
    ".cache",
    ".gradle",
    "crash-reports",
}


def tar_exclude_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Filtruje rekursywne dodawanie katalogu backups oraz zbędnych plików tymczasowych."""
    path_parts = Path(tarinfo.name).parts
    for part in path_parts:
        if part in FULL_BACKUP_EXCLUDES:
            return None
    return tarinfo


def cleanup_old_backups(keep_count: int) -> None:
    if keep_count <= 0:
        return

    auto_backups = sorted(
        [
            f for f in BACKUP_DIR.glob("backup_*.tar.gz")
            if not f.name.startswith("backup_FULL_")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if len(auto_backups) > keep_count:
        to_delete = auto_backups[keep_count:]
        for old_file in to_delete:
            try:
                old_file.unlink()
                logger.info("Usunięto stary automatyczny backup (rotacja): %s", old_file.name)
            except Exception as exc:
                logger.warning("Nie udało się usunąć pliku %s: %s", old_file.name, exc)


def create_backup(full: bool = False) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if full:
        archive_name = f"backup_FULL_{timestamp}.tar.gz"
        archive_path = BACKUP_DIR / archive_name
        logger.info("Tworzenie PEŁNEGO backupu serwera (wszystkie pliki) -> %s", archive_name)

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in MC_DIR.iterdir():
                if item.name not in FULL_BACKUP_EXCLUDES:
                    logger.info("Pakowanie: %s", item.name)
                    tar.add(item, arcname=item.name, filter=tar_exclude_filter)

        logger.info("Pełny backup ukończony pomyślnie. Plik nie podlega rotacji retencji.")
    else:
        archive_name = f"backup_auto_{timestamp}.tar.gz"
        archive_path = BACKUP_DIR / archive_name
        world_dir = MC_DIR / "world"

        if not world_dir.exists():
            raise FileNotFoundError(f"Folder świata {world_dir} nie istnieje!")

        logger.info("Tworzenie standardowego backupu świata -> %s", archive_name)
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(world_dir, arcname="world")

        # Rotacja dotyczy tylko zwykłych kopii
        cleanup_old_backups(MAX_BACKUPS)

    file_size_mb = archive_path.stat().st_size / (1024 * 1024)
    logger.info("Zapisano: %s (%.2f MB)", archive_path.name, file_size_mb)
    return archive_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SzpontMc Backup Utility")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Wykonaj pełny backup całego katalogu serwera (bez limitu ilościowego).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        archive = create_backup(full=args.all)
        print(f"SUCCESS:{archive}")
    except Exception as exc:
        logger.exception("Błąd podczas tworzenia backupu: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()