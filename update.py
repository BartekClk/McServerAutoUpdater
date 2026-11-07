import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Konfiguracja logowania do wspolnego pliku
log_rel_path = os.getenv("LOG_FILE", "logs/szpontmc_manager.log")
log_path = BASE_DIR / log_rel_path
log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Updater] %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Updater")

# Parametry z Twojego .env
MC_DIR = Path(os.getenv("MC_DIR", "/home/ubuntu/mc-server")).resolve()
MC_START_SCRIPT = os.getenv("MC_START_SCRIPT", "run.sh")
PACKWIZ_URL = os.getenv("PACKWIZ_URL")
JAVA_PATH = os.getenv("JAVA_PATH", "java")
RCON_ENABLED = os.getenv("RCON_ENABLED", "false").lower() == "true"
RCON_HOST = os.getenv("RCON_HOST", "127.0.0.1")
RCON_PORT = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")
COUNTDOWN_FILE = os.getenv("COUNTDOWN_FILE", "countdown.txt")


def send_rcon_command(command: str):
    """Wysyła komendę przez RCON (używając biblioteki mcrcon)."""
    if not RCON_ENABLED:
        return
    try:
        from mcrcon import MCRcon
        with MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT, timeout=5) as mcr:
            return mcr.command(command)
    except Exception as exc:
        logger.warning("Nie udalo sie polaczyc z RCON (%s). Serwer moze byc offline: %s", command, exc)
        return None


def execute_countdown():
    """Odczytuje sekwencję komend z pliku countdown.txt lub wykonuje domyślne odliczanie."""
    countdown_path = BASE_DIR / COUNTDOWN_FILE
    if countdown_path.exists():
        logger.info("Wykonywanie sekwencji odliczania z pliku %s", COUNTDOWN_FILE)
        with open(countdown_path, "r", encoding="utf-8") as f:
            for line in f:
                cmd = line.strip()
                if cmd and not cmd.startswith("#"):
                    send_rcon_command(cmd)
                    time.sleep(1)
    else:
        # Domyślne szybkie ostrzeżenie
        send_rcon_command("title @a title {\"text\":\"RESTART SERWERA\", \"color\":\"red\", \"bold\":true}")
        send_rcon_command("title @a subtitle {\"text\":\"Wgrywanie aktualizacji modow za 5 sekund...\", \"color\":\"gold\"}")
        time.sleep(5)


def run_backup():
    """Uruchamia backup.py przed wyłączeniem serwera."""
    logger.info("Wykonywanie zapisu swiata przed backupem...")
    send_rcon_command("save-off")
    send_rcon_command("save-all flush")
    time.sleep(1)

    backup_script = BASE_DIR / "backup.py"
    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    python_bin = str(venv_python if venv_python.exists() else sys.executable)

    logger.info("Uruchamianie backup.py...")
    subprocess.run([python_bin, str(backup_script)], cwd=str(BASE_DIR), check=True)
    send_rcon_command("save-on")


def stop_minecraft():
    """Zatrzymuje serwer MC i czeka na zwolnienie procesu."""
    logger.info("Wysylanie polecenia /stop do serwera...")
    send_rcon_command("stop")

    # Krótkie oczekiwanie na zamknięcie wątków Forge i zwolnienie locków plików
    time.sleep(8)


def update_packwiz():
    """Pobiera i aktualizuje mody za pomoca packwiz-installer-bootstrap."""
    bootstrap_jar = MC_DIR / "packwiz-installer-bootstrap.jar"
    if not bootstrap_jar.exists():
        logger.info("Pobieranie packwiz-installer-bootstrap.jar do %s...", MC_DIR)
        url = "https://github.com/packwiz/packwiz-installer-bootstrap/releases/latest/download/packwiz-installer-bootstrap.jar"
        subprocess.run(["curl", "-sLo", str(bootstrap_jar), url], check=True)

    cmd = [
        JAVA_PATH,
        "-jar",
        str(bootstrap_jar),
        "-s", "server",
        PACKWIZ_URL
    ]

    logger.info("Aktualizacja paczki Packwiz z: %s", PACKWIZ_URL)
    result = subprocess.run(cmd, cwd=str(MC_DIR), capture_output=True, text=True)

    if result.returncode != 0:
        logger.error("Blad packwiz-installer:\n%s", result.stderr)
        raise RuntimeError("Packwiz nie mogl zaktualizowac modow!")
    logger.info("Paczka zaktualizowana pomyslnie.")


def start_minecraft():
    """Uruchamia serwer Minecraft wskazanym skryptem MC_START_SCRIPT."""
    start_script_path = MC_DIR / MC_START_SCRIPT
    if not start_script_path.exists():
        raise FileNotFoundError(f"Skrypt startowy {start_script_path} nie istnieje!")

    logger.info("Uruchamianie serwera Minecraft przez: %s", start_script_path)
    subprocess.Popen(
        ["/bin/bash", str(start_script_path)],
        cwd=str(MC_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True
    )


def main():
    start_time = time.time()
    try:
        execute_countdown()
        run_backup()
        stop_minecraft()
        update_packwiz()
        start_minecraft()

        elapsed = round(time.time() - start_time, 2)
        logger.info("Wdrozenie zakonczone pelnym sukcesem w %s sekund!", elapsed)

        # Opcjonalne wywołanie powiadomień Discord jeśli posiadasz discord_notify.py
        discord_script = BASE_DIR / "discord_notify.py"
        if discord_script.exists():
            venv_python = BASE_DIR / "venv" / "bin" / "python3"
            python_bin = str(venv_python if venv_python.exists() else sys.executable)
            subprocess.run([python_bin, str(discord_script), "--success", f"--time={elapsed}"], cwd=str(BASE_DIR))

    except Exception as exc:
        logger.exception("Blad podczas procesu aktualizacji: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()