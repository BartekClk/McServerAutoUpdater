import os
from pathlib import Path
import re
import time
from dotenv import load_dotenv
from mcrcon import MCRcon

MC_LOG = Path("/home/ubuntu/mc-server/logs/latest.log")
DONE_FILE = Path("restored.done")

# Mapa graczy i ich atrybutów
RESTORE_DATA = {
    "Who_it_can_be": {
        "Strength": 16,
        "Building": 26,
        "Constitution": 16,
        "Defense": 19,
        "Intelligence": 17,
        "Magic": 21,
        "Dexterity": 16,
        "Luck": 20,
    },
    "NightFoxD": {
        "Strength": 16,
        "Building": 16,
        "Constitution": 16,
        "Defense": 32,
        "Intelligence": 8,
        "Magic": 16,
        "Dexterity": 7,
    },
    "NeoMattX": {"Intelligence": 10},
    "MrCost": {"Strength": 8, "Building": 16, "Constitution": 5, "Defense": 8, "Intelligence": 8, "Magic": 7},
    "kamilop1": {"Strength": 8, "Building": 8, "Constitution": 5, "Defense": 8, "Intelligence": 8},
    "__Ewka__": {"Intelligence": 8, "Building": 8, "Strength": 13, "Defense": 8},
    "TOFIKS13": {"Strength": 8, "Building": 13, "Defense": 8, "Intelligence": 8},
    "_Aixa_6": {"Building": 2, "Intelligence": 2},
}

load_dotenv(Path(__file__).resolve().parent / ".env")
RCON_HOST = os.getenv("RCON_HOST", "127.0.0.1")
RCON_PORT = int(os.getenv("RCON_PORT", 25575))
RCON_PWD = os.getenv("RCON_PASSWORD", "")


def get_restored_set():
    if not DONE_FILE.exists():
        return set()
    return set(DONE_FILE.read_text(encoding="utf-8").splitlines())


def mark_as_restored(nick):
    with open(DONE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{nick}\n")


def execute_restore(nick):
    skills = RESTORE_DATA.get(nick)
    if not skills:
        return

    print(f"\n[!] Wykryto gracza {nick} na serwerze! Wysyłanie komend RCON...")
    try:
        with MCRcon(RCON_HOST, RCON_PWD, port=RCON_PORT) as mcr:
            for skill, lvl in skills.items():
                cmd = f"aptitudes {nick} {skill} set {lvl}"
                resp = mcr.command(cmd)
                print(f" -> /{cmd} | Odpowiedź: {resp}")
        mark_as_restored(nick)
        print(f"[OK] Pomyślnie przywrócono poziomy dla {nick}!\n")
    except Exception as e:
        print(f"[BŁĄD] RCON nie mógł wykonać komend dla {nick}: {e}")


def check_online_players():
    try:
        with MCRcon(RCON_HOST, RCON_PWD, port=RCON_PORT) as mcr:
            resp = mcr.command("list")
            restored = get_restored_set()
            for nick in RESTORE_DATA:
                if nick not in restored and nick.lower() in resp.lower():
                    execute_restore(nick)
    except Exception:
        pass


def main():
    print("=== Auto Restore on Join uruchomione ===")
    check_online_players()

    if not MC_LOG.exists():
        print(f"Oczekiwanie na plik logu: {MC_LOG}")
        while not MC_LOG.exists():
            time.sleep(2)

    with open(MC_LOG, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)  # Przejdź na koniec pliku
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue

            # Wzorzec logowania gracza do świata
            # np. "[Server thread/INFO] [minecraft/PlayerList]: MrCost joined the game"
            match = re.search(r":\s*(\w+)\s+joined the game", line)
            if match:
                nick = match.group(1)
                restored = get_restored_set()
                if nick in RESTORE_DATA and nick not in restored:
                    time.sleep(1.5)  # Daj 1.5s na załadowanie encji gracza
                    execute_restore(nick)


if __name__ == "__main__":
    main()