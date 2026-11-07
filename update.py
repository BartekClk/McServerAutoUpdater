import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def setup_logging():
    log_file_env = os.getenv("LOG_FILE", "logs/mc_manager.log")
    log_path = (BASE_DIR / log_file_env).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_format = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 1. Rotating File Handler (5 MB per file, max 5 files)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    root_logger.addHandler(file_handler)

    # 2. Console Stream Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    root_logger.addHandler(console_handler)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Minecraft server maintenance and deployment daemon."
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Trigger selective server compression, upload to Google Drive, and prune old archives",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Execute automated maintenance: countdown, graceful stop, backup, Packwiz pull, and server restart",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Modifier flag indicating test/dry-run execution",
    )
    parser.add_argument(
        "--countdown",
        action="store_true",
        help="Target the countdown routine",
    )
    parser.add_argument(
        "--test-countdown",
        action="store_true",
        help="Direct shortcut to test RCON countdown without server shutdown",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use rapid 5-second countdown sequence (countdown_quick.txt)",
    )
    parser.add_argument(
        "--test-discord",
        action="store_true",
        help="Send a test embed to Discord to verify webhook connectivity",
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        help="Modifier targeting discord routines",
    )
    return parser.parse_args()


def main():
    setup_logging()
    logger = logging.getLogger("main")
    args = parse_args()

    # Obsługa testu odliczania: --test --countdown [--quick] lub --test-countdown [--quick]
    if (args.test and args.countdown) or args.test_countdown:
        from deploy import test_countdown

        logger.info("Task initiated: countdown test (quick=%s)", args.quick)
        success = test_countdown(quick=args.quick)
        if not success:
            logger.error("Task failed: countdown test returned non-zero status")
            sys.exit(1)
        logger.info("Task completed successfully: countdown test")
        sys.exit(0)
    
    # Obsługa testu Discorda: --test-discord lub --test --discord
    if (args.test and args.discord) or args.test_discord:
        from discord_notify import notify_deploy_start

        logger.info("Task initiated: Discord webhook test")
        success = notify_deploy_start(quick=True)
        if not success:
            logger.error("Task failed: Discord webhook test failed")
            sys.exit(1)
        logger.info("Task completed successfully: Discord webhook test")
        sys.exit(0)

    if args.backup:
        from backup import run_backup

        logger.info("Task initiated: --backup")
        success = run_backup()
        if not success:
            logger.error("Task failed: --backup returned non-zero status")
            sys.exit(1)
        logger.info("Task completed successfully: --backup")
        sys.exit(0)

    if args.deploy:
        from deploy import run_deployment

        logger.info("Task initiated: --deploy (quick=%s)", args.quick)
        success = run_deployment(quick=args.quick)
        if not success:
            logger.error("Task failed: --deploy returned non-zero status")
            sys.exit(1)
        logger.info("Task completed successfully: --deploy")
        sys.exit(0)

    if len(sys.argv) == 1:
        print("No operation requested. Use --help to list available subroutines.")
        sys.exit(0)


if __name__ == "__main__":
    main()