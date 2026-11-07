import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import threading
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Konfiguracja logowania ze zmiennej LOG_FILE
log_rel_path = os.getenv("LOG_FILE", "logs/szpontmc_manager.log")
log_path = BASE_DIR / log_rel_path
log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DeployAPI] %(message)s",
    handlers=[
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("DeployAPI")

API_PORT = int(os.getenv("DEPLOY_API_PORT", "9000"))
API_TOKEN = os.getenv("DEPLOY_API_TOKEN")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Obsługuje każde żądanie w osobnym wątku."""
    daemon_threads = True


class DeployWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/deploy":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "Endpoint not found"}')
            return

        # Weryfikacja nagłówka autoryzacyjnego
        auth_header = self.headers.get("Authorization", "")
        expected_header = f"Bearer {API_TOKEN}"

        if not API_TOKEN or auth_header != expected_header:
            logger.warning("Odrzucono nieautoryzowane zadanie z IP: %s", self.client_address[0])
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized"}')
            return

        # Odczyt danych z GitHub Actions
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}

        commit_hash = payload.get("commit", "unknown")
        actor = payload.get("actor", "unknown")
        logger.info("Zautoryzowano deployment od %s (commit: %s)", actor, commit_hash)

        # Odpowiedź 202 Accepted dla GitHub Actions
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "accepted",
            "message": "Deployment started in background",
            "commit": commit_hash
        }).encode("utf-8"))

        # Uruchomienie procedury aktualizacji w tle
        threading.Thread(target=self.run_update_process, args=(commit_hash, actor)).start()

    def run_update_process(self, commit_hash: str, actor: str):
        venv_python = BASE_DIR / "venv" / "bin" / "python3"
        python_bin = str(venv_python if venv_python.exists() else sys.executable)
        update_script = BASE_DIR / "update.py"

        logger.info("Rozpoczynanie procesu update.py...")
        try:
            result = subprocess.run(
                [python_bin, str(update_script)],
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                logger.info("Proces update.py zakonczony pelnym sukcesem.")
            else:
                logger.error("update.py zwrocil blad (kod %s):\n%s", result.returncode, result.stderr)
        except Exception as exc:
            logger.exception("Krytyczny wyjatek podczas uruchamiania update.py: %s", exc)

    def log_message(self, format, *args):
        # Wyciszenie domyślnych logów konsolowych HTTPServer na rzecz loggera
        return


def main():
    if not API_TOKEN:
        logger.error("Brak zdefiniowanego DEPLOY_API_TOKEN w .env! Przerywanie.")
        sys.exit(1)

    server_address = ("0.0.0.0", API_PORT)
    httpd = ThreadedHTTPServer(server_address, DeployWebhookHandler)
    logger.info("Serwer API nasluchuje na 0.0.0.0:%s", API_PORT)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Zatrzymywanie serwera API...")
        httpd.server_close()


if __name__ == "__main__":
    main()