import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from dotenv import load_dotenv

from backup import run_backup
from discord_notify import notify_deploy_failure, notify_deploy_start, notify_deploy_success

logger = logging.getLogger("deploy")
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

WAIT_REGEX = re.compile(r"^wait\s*\(\s*(\d+(?:\.\d+)?)\s*\)$", re.IGNORECASE)


def execute_command_sequence(sequence_path: Path, host: str, port: int, password: str) -> bool:
    if not sequence_path.exists():
        logger.warning("Sequence file %s not found. Proceeding without pre-shutdown sequence.", sequence_path.name)
        return False

    with open(sequence_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not lines:
        logger.warning("Sequence file %s is empty.", sequence_path.name)
        return True

    logger.info("Executing sequence from %s (%d instructions)...", sequence_path.name, len(lines))

    try:
        from mcrcon import MCRcon

        with MCRcon(host, password, port=port) as mcr:
            for line in lines:
                wait_match = WAIT_REGEX.match(line)
                if wait_match:
                    ms_val = float(wait_match.group(1))
                    sec_val = ms_val / 1000.0
                    logger.debug("Pausing for %.2fs (%d ms)", sec_val, int(ms_val))
                    time.sleep(sec_val)
                elif line.startswith("/"):
                    command = line.lstrip("/")
                    logger.info("RCON dispatch: /%s", command)
                    response = mcr.command(command)
                    if response:
                        logger.debug("RCON response: %s", response.strip())
                else:
                    logger.warning("Skipping unknown sequence instruction: %s", line)
        return True

    except Exception as exc:
        logger.error("Failed executing sequence via RCON: %s", exc)
        return False


def test_countdown(quick: bool = False) -> bool:
    filename = "countdown_quick.txt" if quick else os.getenv("COUNTDOWN_FILE", "countdown.txt")
    countdown_file = (BASE_DIR / filename).resolve()
    rcon_host = os.getenv("RCON_HOST", "127.0.0.1")
    rcon_port = int(os.getenv("RCON_PORT", "25575"))
    rcon_password = os.getenv("RCON_PASSWORD", "")

    logger.info("Running countdown simulation mode (file: %s)...", countdown_file.name)
    success = execute_command_sequence(countdown_file, rcon_host, rcon_port, rcon_password)
    return success


def stop_minecraft_server(host: str, port: int, password: str) -> None:
    logger.info("Sending /stop command via RCON...")
    try:
        from mcrcon import MCRcon

        with MCRcon(host, password, port=port) as mcr:
            mcr.command("stop")
        logger.info("Stop signal dispatched successfully.")
    except Exception as exc:
        logger.error("Failed to stop server via RCON: %s", exc)
        raise


def wait_for_server_shutdown(timeout: int = 45) -> None:
    logger.info("Waiting for server process termination...")
    time.sleep(5)
    logger.info("Server process stopped.")


def update_mods_via_packwiz(mc_dir: Path, pack_url: str) -> None:
    logger.info("Synchronizing mod files via Packwiz...")
    bootstrap_jar = mc_dir / "packwiz-installer-bootstrap.jar"
    java_bin = os.getenv("JAVA_PATH", "java")

    resolved_java = shutil.which(java_bin) or (Path(java_bin).exists() and java_bin)
    if not resolved_java:
        raise FileNotFoundError(f"Java executable not found at '{java_bin}'. Check JAVA_PATH in .env")

    if not bootstrap_jar.exists():
        raise FileNotFoundError(f"Missing Packwiz bootstrap JAR at: {bootstrap_jar}")

    cmd = [
        str(resolved_java),
        "-jar",
        str(bootstrap_jar),
        "-s",
        "server",
        pack_url,
    ]

    result = subprocess.run(cmd, cwd=mc_dir, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Packwiz sync failed: %s", result.stderr)
        raise RuntimeError(f"Packwiz exited with code {result.returncode}: {result.stderr.strip()}")

    logger.info("Mod synchronization complete.")


def start_minecraft_server(mc_dir: Path, start_script: str) -> None:
    logger.info("Starting Minecraft server using %s...", start_script)
    script_path = mc_dir / start_script

    if not script_path.exists():
        raise FileNotFoundError(f"Start script not found: {script_path}")

    subprocess.Popen(
        [str(script_path)],
        cwd=mc_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    logger.info("Server start initiated in detached session.")


def run_deployment(quick: bool = False) -> bool:
    start_time = time.time()
    mc_dir = Path(os.getenv("MC_DIR", ".")).resolve()
    pack_url = os.getenv("PACKWIZ_URL")
    start_script = os.getenv("MC_START_SCRIPT", "run.sh")

    filename = "countdown_quick.txt" if quick else os.getenv("COUNTDOWN_FILE", "countdown.txt")
    countdown_file = (BASE_DIR / filename).resolve()

    rcon_host = os.getenv("RCON_HOST", "127.0.0.1")
    rcon_port = int(os.getenv("RCON_PORT", "25575"))
    rcon_password = os.getenv("RCON_PASSWORD", "")

    if not pack_url:
        logger.error("PACKWIZ_URL is not configured in .env")
        notify_deploy_failure("Inicjalizacja", "Brak zdefiniowanego PACKWIZ_URL w .env")
        return False

    # 1. Alert na Discord o starcie wdrożenia
    notify_deploy_start(quick=quick)

    try:
        # 2. Odliczanie RCON w grze
        execute_command_sequence(countdown_file, rcon_host, rcon_port, rcon_password)

        # 3. Zatrzymanie serwera
        stop_minecraft_server(rcon_host, rcon_port, rcon_password)
        wait_for_server_shutdown()

        # 4. Kopia zapasowa
        logger.info("Executing safety backup prior to mod update...")
        backup_success = run_backup(skip_rcon=True)
        if not backup_success:
            err_msg = "Safety backup failed. Mod update aborted to protect server state."
            logger.error(err_msg)
            notify_deploy_failure("Kopia zapasowa (Safety Backup)", err_msg)
            return False

        # 5. Aktualizacja plików przez Packwiz
        update_mods_via_packwiz(mc_dir, pack_url)

        # 6. Ponowny rozruch
        start_minecraft_server(mc_dir, start_script)

        elapsed = time.time() - start_time
        logger.info("Deployment sequence finished successfully in %.1fs.", elapsed)

        # 7. Sukces na Discord
        notify_deploy_success(elapsed_seconds=elapsed)
        return True

    except Exception as exc:
        logger.exception("Deployment sequence aborted due to error: %s", exc)
        notify_deploy_failure("Procedura wdrożeniowa", str(exc))
        return False