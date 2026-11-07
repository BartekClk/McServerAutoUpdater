from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Optional
import urllib.error
import urllib.request
from dotenv import load_dotenv

logger = logging.getLogger("discord")
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def send_discord_webhook(embed: dict, content: Optional[str] = None) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL is not set. Skipping notification.")
        return False

    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "SzpontMc-DeployDaemon/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 204):
                logger.info("Discord webhook sent successfully.")
                return True
            logger.warning("Discord webhook returned unexpected status: %d", response.status)
            return False
    except urllib.error.HTTPError as exc:
        logger.error("Discord webhook HTTP error %d: %s", exc.code, exc.read().decode("utf-8", "ignore"))
        return False
    except Exception as exc:
        logger.error("Failed to transmit Discord webhook: %s", exc)
        return False


def get_role_mention() -> Optional[str]:
    role_id = os.getenv("DISCORD_ROLE_ID", "").strip()
    return f"<@&{role_id}>" if role_id else None


def notify_deploy_start(quick: bool = False) -> bool:
    mode_text = "Szybka aktualizacja (hotfix)" if quick else "Standardowa aktualizacja paczki"
    embed = {
        "title": "🛠️ Prace techniczne: Aktualizacja serwera",
        "description": "Rozpoczęto procedurę wdrażania zmian na serwerze. Gracze zostaną rozłączeni.",
        "color": 0xF1C40F,  # Żółty / Amber
        "fields": [
            {"name": "Tryb", "value": mode_text, "inline": True},
            {"name": "Status", "value": "Zapisywanie świata i backup...", "inline": True},
        ],
        "footer": {"text": "SzpontMc Deployment Daemon"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return send_discord_webhook(embed, content=get_role_mention())


def get_pack_version_from_toml(mc_dir: Path) -> str:
    """Odczytuje aktualną wersję paczki z pliku pack.toml Packwiza."""
    pack_toml = mc_dir / "pack.toml"
    if pack_toml.exists():
        try:
            for line in pack_toml.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("version"):
                    raw_ver = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return raw_ver if raw_ver.startswith("v") else f"v{raw_ver}"
        except Exception as exc:
            logger.warning("Nie udało się odczytać wersji z pack.toml: %s", exc)
    return "v0.2.0"


def notify_deploy_success(
    elapsed_seconds: float = 0.0,
    version: Optional[str] = None,
    changelog: Optional[str] = None,
    release_url: Optional[str] = None,
    pack_name: str = "SZPONT MC Modded",
) -> bool:
    mc_dir = Path(os.getenv("MC_DIR", ".")).resolve()
    ver = version or get_pack_version_from_toml(mc_dir)
    url = release_url or os.getenv(
        "RELEASE_URL",
        "https://github.com/BartekClk/SzpontMcModded/releases",
    )

    if not changelog:
        changelog = (
            "**Dodano:**\n\n"
            "• Sortowanie ekwipunku.\n"
            "• Możliwość podglądu receptury craftowania wewnątrz questu.\n"
            "• Oznaczenie już zlootowanych skrzynek w strukturach\n"
            "*(Skrzynki zlootowane przed dodaniem moda mogą nie uledz zmianie)*\n"
            "• Dodano możliwość edycji configów modów takich jak JEI wewnątrz gry."
        )

    # Informacja o czasie wykonania prac technicznych
    time_info = f" (prace trwały {elapsed_seconds:.1f}s)" if elapsed_seconds > 0 else ""

    description = (
        f"🟢 **Serwer jest już online i gotowy do gry!**{time_info}\n"
        f"⚠️ **Przed wejściem koniecznie zaktualizuj paczkę w swoim launcherze.**\n\n"
        f"**Wersja:** `{ver}`\n\n"
        f"**Lista zmian:**\n\n"
        f"{changelog}\n\n"
        f"**Pobieranie**\n"
        f"[Kliknij tutaj, aby przejść do wydania]({url})"
    )

    embed = {
        "title": f"🚀 Nowa aktualizacja paczki: {pack_name} {ver}",
        "description": description,
        "color": 0x2ECC71,  # Zielony kolor sukcesu
        "footer": {
            "text": "szpontmc.com • Zaktualizuj paczkę w launcherze przed wejściem!"
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return send_discord_webhook(embed, content=get_role_mention())


def notify_deploy_failure(stage: str, error_msg: str) -> bool:
    embed = {
        "title": "❌ Błąd podczas wdrażania aktualizacji",
        "description": "Procedura wdrożeniowa została przerwana. Wymagana interwencja administratora.",
        "color": 0xE74C3C,  # Czerwony
        "fields": [
            {"name": "Etap awarii", "value": stage, "inline": True},
            {"name": "Szczegóły", "value": f"```{error_msg[:1000]}```", "inline": False},
        ],
        "footer": {"text": "SzpontMc Deployment Daemon"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return send_discord_webhook(embed)