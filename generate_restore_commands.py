import argparse
import glob
import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request
import nbtlib

MC_DIR = Path("/home/ubuntu/mc-server").resolve()
BACKUP_DIRS = [
    Path("/home/ubuntu/mc-backups-temp"),
    Path("/home/ubuntu/mc-backupds-temp"),
    MC_DIR / "backups",
    Path("/tmp/mc-backups"),
]

USERCACHE_PATH = MC_DIR / "usercache.json"


def load_usercache() -> dict[str, str]:
    """Wczytuje mapowanie UUID -> Nickname z usercache.json lub Mojang API."""
    cache = {}
    if USERCACHE_PATH.exists():
        try:
            with open(USERCACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data:
                    uuid_str = entry.get("uuid", "").replace("-", "").lower()
                    cache[uuid_str] = entry.get("name")
        except Exception as e:
            print(f"[!] Błąd odczytu usercache.json: {e}")
    return cache


def get_player_name(uuid: str, cache: dict[str, str]) -> str:
    clean_uuid = uuid.replace("-", "").lower()
    if clean_uuid in cache:
        return cache[clean_uuid]

    # Awaryjny fallback do API Mojang, jeśli gracza nie ma w lokalnym cache
    try:
        url = f"https://sessionserver.mojang.com/session/minecraft/profile/{clean_uuid}"
        req = urllib.request.Request(url, headers={"User-Agent": "SzpontMc-Restore/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            name = data.get("name")
            if name:
                cache[clean_uuid] = name
                return name
    except Exception:
        pass

    return uuid  # Jeśli nick nieznany, użyj surowego UUID


def find_latest_backup() -> Path:
    candidates = []
    for d in BACKUP_DIRS:
        if d.exists():
            candidates.extend(d.glob("backup_*.tar*"))
    if not candidates:
        raise FileNotFoundError("Nie znaleziono żadnego archiwum backupu w znanych lokalizacjach!")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_player_data(file_bytes: bytes) -> dict[str, int]:
    """Rozpakowuje GZIP i wyciąga umiejętności (aptitudes) z ForgeCaps."""
    decompressed = gzip.decompress(file_bytes)
    nbt = nbtlib.File.from_fileobj(io.BytesIO(decompressed))
    caps = nbt.get("ForgeCaps", {})

    old_jl_data = None
    for k in caps:
        if "justleveling" in k.lower() or "aptitude" in k.lower():
            old_jl_data = caps[k]
            break

    if not old_jl_data:
        return {}

    aptitudes = {}
    for key, val in old_jl_data.items():
        if key.startswith("aptitude."):
            apt_name = key.replace("aptitude.", "")
            level = int(val)
            # Pomijamy poziom bazowy 1, aby nie generować zbędnych komend
            if level > 1:
                aptitudes[apt_name] = level

    return aptitudes


def main():
    parser = argparse.ArgumentParser(description="Generator komend przywracania poziomów Just Leveling")
    parser.add_argument("--rcon", action="store_true", help="Automatycznie wyślij wygenerowane komendy przez RCON")
    parser.add_argument(
        "--cmd",
        choices=["aptitudes", "updateAptitudeLevel"],
        default="aptitudes",
        help="Składnia komendy (domyślnie: aptitudes)",
    )
    args = parser.parse_args()

    latest_backup = find_latest_backup()
    print(f"[*] Wczytywanie danych z backupu: {latest_backup.name}")
    usercache = load_usercache()

    generated_commands = []
    summary_data = {}

    with tarfile.open(latest_backup, "r:*") as tar:
        for member in tar.getmembers():
            if "playerdata/" in member.name and member.name.endswith(".dat"):
                uuid = Path(member.name).stem
                f = tar.extractfile(member)
                if not f:
                    continue

                try:
                    aptitudes = parse_player_data(f.read())
                    if aptitudes:
                        player_name = get_player_name(uuid, usercache)
                        summary_data[player_name] = aptitudes

                        for apt_name, level in aptitudes.items():
                            if args.cmd == "aptitudes":
                                cmd = f"aptitudes set {player_name} {apt_name} {level}"
                            else:
                                cmd = f"updateAptitudeLevel {player_name} {apt_name} {level}"
                            generated_commands.append(cmd)
                except Exception as e:
                    print(f"[!] Błąd przetwarzania {member.name}: {e}")

    output_file = Path("restore_commands.txt")
    with open(output_file, "w", encoding="utf-8") as f:
        for cmd in generated_commands:
            f.write(f"/{cmd}\n")

    print("\n" + "=" * 50)
    print("ZNALEZIONE POZIOMY GRACZY:")
    print("=" * 50)
    for player, skills in summary_data.items():
        skills_str = ", ".join(f"{k}: {v}" for k, v in skills.items())
        print(f"-> {player}: {skills_str}")

    print("=" * 50)
    print(f"[OK] Wygenerowano {len(generated_commands)} komend do pliku: {output_file.resolve()}")

    if args.rcon:
        from dotenv import load_dotenv
        import os
        from mcrcon import MCRcon

        load_dotenv(Path(__file__).resolve().parent / ".env")
        host = os.getenv("RCON_HOST", "127.0.0.1")
        port = int(os.getenv("RCON_PORT", 25575))
        pwd = os.getenv("RCON_PASSWORD", "")

        print("\n[*] Wysyłanie komend przez RCON na serwer...")
        try:
            with MCRcon(host, pwd, port=port) as mcr:
                for cmd in generated_commands:
                    resp = mcr.command(cmd)
                    print(f"/{cmd} -> {resp}")
            print("[OK] Wszystkie komendy wysłane!")
        except Exception as e:
            print(f"[X] Błąd połączenia RCON: {e}")
            print("Uwaga: Jeśli gracz jest offline, większość komend Forge zgłasza brak gracza.")


if __name__ == "__main__":
    main()