from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Konfiguracja audytowego loggera
LOG_FILE = BASE_DIR / "deploy_api.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("DeployAPI")

API_PORT = int(os.getenv("DEPLOY_API_PORT", "9000"))
API_TOKEN = os.getenv("DEPLOY_API_TOKEN", "")


def execute_update_script(commit_sha: str, actor: str) -> None:
    """Uruchamia update.py w tle i rejestruje rezultat."""
    logger.info("Rozpoczynanie wdrożenia dla commita %s (wywołane przez: %s)", commit_sha, actor)
    start_time = time.time()
    try:
        result = subprocess.run(
            ["python3", str(BASE_DIR / "update.py"), "--deploy", "--quick"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        elapsed = time.time() - start_time
        if result.returncode == 0:
            logger.info("Wdrożenie zakończone sukcesem w %.1fs (kod: 0)", elapsed)
        else:
            logger.error("Wdrożenie zakończone błędem (kod: %d): %s", result.returncode, result.stderr.strip())
    except subprocess.TimeoutExpired:
        logger.error("Wdrożenie przekroczyło limit czasu 300 sekund.")
    except Exception as exc:
        logger.exception("Nieoczekiwany wyjątek podczas wdrożenia: %s", exc)


class DeployWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        client_ip = self.client_address[0]

        # 1. Sprawdzenie ścieżki
        if self.path != "/api/deploy":
            logger.warning("Nieprawidłowa ścieżka żądania od %s: %s", client_ip, self.path)
            self._send_response(404, {"error": "Not Found"})
            return

        # 2. Weryfikacja tokenu autoryzacji
        auth_header = self.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()

        if not API_TOKEN or token != API_TOKEN:
            logger.warning("Nieautoryzowana próba wywołania API od %s (błędny token)", client_ip)
            self._send_response(401, {"error": "Unauthorized"})
            return

        # 3. Odczyt parametrów z JSON
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(post_data) if post_data else {}
        except Exception:
            payload = {}

        commit_sha = payload.get("commit", "unknown")[:8]
        actor = payload.get("actor", "github-actions")

        logger.info("Autoryzowane wywołanie od %s | Commit: %s | Autor: %s", client_ip, commit_sha, actor)

        # 4. Asynchroniczne odpalenie deploymentu w osobnym wątku
        deploy_thread = threading.Thread(
            target=execute_update_script,
            args=(commit_sha, actor),
            daemon=True,
        )
        deploy_thread.start()

        # 5. Natychmiastowa odpowiedź dla GitHuba
        self._send_response(202, {
            "status": "Accepted",
            "message": "Deployment initiated",
            "commit": commit_sha,
        })

    def _send_response(self, code: int, body: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        # Tłumienie domyślnego stdout BaseHTTPRequestHandler na rzecz pliku logu
        return


def run_api_server() -> None:
    if not API_TOKEN:
        print("[OSTRZEŻENIE] Brak zdefiniowanego DEPLOY_API_TOKEN w .env! Wygeneruj token.")
    server_address = ("0.0.0.0", API_PORT)
    httpd = HTTPServer(server_address, DeployWebhookHandler)
    logger.info("Mikro-serwer Webhook API wystartował na porcie %d", API_PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        logger.info("Mikro-serwer Webhook API został zatrzymany.")


if __name__ == "__main__":
    run_api_server()