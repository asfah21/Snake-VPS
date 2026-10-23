import asyncio, json, os, subprocess, sys, threading, webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import websockets
from TikTokLive import TikTokLiveClient
from TikTokLive.events import GiftEvent
from TikTokLive.client.errors import UserOfflineError

try:
    import pygame
except ImportError:
    pygame = None

# ================== PENGATURAN ==================
USERNAME = (sys.argv[1] if len(sys.argv) > 1 else "isi_username_kamu").lstrip("@")
HTTP_PORT, WS_PORT = 8001, 8766

# ---------- Musik latar (folder "mp3" di sebelah file ini) ----------
BGM_AKTIF = True            # False = matikan musik latar
BGM_VOLUME = 0.28           # volume normal (0.0 - 1.0)
BGM_VOLUME_DUCK = 0.09      # volume saat efek suara gift berbunyi
BGM_DUCK_DETIK = 2.6        # lama musik dikecilkan setelah gift masuk

# Daftar gift: "Nama gift di TikTok": ("ikon", jumlah apel). Tambah / ubah sesukamu.
GIFTS = {
    "Rose": ("🌹", 5),
    "Finger Heart": ("🫰", 5), "Ice Cream Cone": ("🍦", 8),
    "Hand Hearts": ("🤲", 20), "Doughnut": ("🍩", 30), "Swan": ("🦢", 40),
    "Corgi": ("🐶", 50), "Money Gun": ("🔫", 70), "Galaxy": ("🌌", 150),
    "Lion": ("🦁", 300), "TikTok Universe": ("🪐", 500),
}
# ================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MP3_DIR = os.path.join(BASE_DIR, "mp3")

penghubung = set()
_bgm_siap = False
_bgm_ducked = False
_bgm_task = None            # referensi task musik (supaya tidak di-GC)
_duck_tasks = set()         # referensi task ducking sementara


# ---------------- Musik latar (gaya Mika.py) ----------------
def siapkan_mixer():
    """Nyalakan pygame mixer untuk musik latar. Dipanggil sekali di main()."""
    global _bgm_siap
    if not BGM_AKTIF:
        print("[BGM] Musik latar dimatikan (BGM_AKTIF = False).")
        return
    if pygame is None:
        print("[BGM] pygame belum terpasang -> pip install pygame. Musik latar dilewati.")
        return
    try:
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(8)
        _bgm_siap = True
    except Exception as err:
        print("[BGM] Gagal inisialisasi mixer:", err)
        return
    lagu = daftar_musik_latar()
    print(f"[BGM] Mixer siap. Folder: {MP3_DIR} ({len(lagu)} file audio).")


def daftar_musik_latar():
    """Ambil semua file audio di folder mp3/ (urut nama)."""
    if not os.path.isdir(MP3_DIR):
        return []
    ekstensi = (".mp3", ".ogg", ".wav", ".flac", ".m4a")
    return [
        os.path.join(MP3_DIR, nama)
        for nama in sorted(os.listdir(MP3_DIR), key=str.lower)
        if nama.lower().endswith(ekstensi) and os.path.isfile(os.path.join(MP3_DIR, nama))
    ]


def atur_volume_bgm(ducked):
    """Kecilkan / kembalikan volume musik latar."""
    global _bgm_ducked
    _bgm_ducked = ducked
    if not _bgm_siap:
        return
    try:
        pygame.mixer.music.set_volume(BGM_VOLUME_DUCK if ducked else BGM_VOLUME)
    except Exception:
        pass


async def duck_sebentar(detik=BGM_DUCK_DETIK):
    """Kecilkan musik sementara supaya efek suara gift terdengar jelas."""
    if not _bgm_siap:
        return
    atur_volume_bgm(True)
    await asyncio.sleep(detik)
    atur_volume_bgm(False)


def jadwalkan_duck():
    """Jalankan ducking tanpa membuat task-nya hilang karena garbage collector."""
    t = asyncio.create_task(duck_sebentar())
    _duck_tasks.add(t)
    t.add_done_callback(_duck_tasks.discard)


async def putar_musik_latar():
    """Putar semua lagu di folder mp3/ bergantian selama game berjalan."""
    if not _bgm_siap:
        return
    os.makedirs(MP3_DIR, exist_ok=True)
    lagu = daftar_musik_latar()
    if not lagu:
        print(f"[BGM] Folder {MP3_DIR} masih kosong. Taruh file .mp3 di sana lalu jalankan ulang.")
        return

    print(f"[BGM] {len(lagu)} lagu ditemukan. Musik latar aktif (volume {int(BGM_VOLUME * 100)}%).")
    i = 0
    while True:
        path = lagu[i]
        i = (i + 1) % len(lagu)
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(BGM_VOLUME_DUCK if _bgm_ducked else BGM_VOLUME)
            pygame.mixer.music.play()
            print("[BGM] Memutar:", os.path.basename(path))
        except Exception as err:
            print("[BGM] Gagal memutar", os.path.basename(path), "->", err)
            await asyncio.sleep(1.0)
            continue

        while True:                                  # tunggu lagu selesai
            try:
                if not pygame.mixer.music.get_busy():
                    break
            except Exception:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.4)


# ---------------- Browser dengan audio autoplay ----------------
def buka_browser(url):
    """Buka game di Chrome/Edge dengan autoplay audio aktif, jadi suara langsung nyala."""
    if sys.platform != "win32":
        webbrowser.open(url)
        return
    kandidat = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if not root:
            continue
        kandidat.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
        kandidat.append(os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"))
    profil = os.path.join(BASE_DIR, ".browser_profile")
    for exe in kandidat:
        if not os.path.isfile(exe):
            continue
        try:
            subprocess.Popen(
                [
                    exe,
                    "--autoplay-policy=no-user-gesture-required",
                    f"--user-data-dir={profil}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--new-window",
                    url,
                ],
                close_fds=True,
            )
            print(f"[BROWSER] {os.path.basename(exe)} dibuka, audio autoplay aktif.")
            return
        except Exception as err:
            print("[BROWSER] Gagal membuka", exe, "->", err)
    webbrowser.open(url)


# ---------------- Gift & websocket ----------------
def apel_untuk(gift):
    if gift.name in GIFTS:
        return GIFTS[gift.name][1]
    k = gift.diamond_count or 1          # gift yang tidak ada di daftar: dihitung dari nilai koin
    for batas, apel in [(20, 5), (50, 10), (100, 20), (500, 60), (1000, 100)]:
        if k < batas:
            return apel
    return 200


async def ws_handler(ws):
    penghubung.add(ws)
    await ws.send(json.dumps({"type": "config", "gifts": [[i, n, a] for n, (i, a) in GIFTS.items()]}))
    try:
        await ws.wait_closed()
    finally:
        penghubung.discard(ws)


async def kirim(data):
    pesan = json.dumps(data)
    for ws in list(penghubung):
        try:
            await ws.send(pesan)
        except Exception:
            penghubung.discard(ws)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def http_server():
    os.chdir(BASE_DIR)
    ThreadingHTTPServer(("localhost", HTTP_PORT), Quiet).serve_forever()


async def on_gift(e: GiftEvent):
    if e.gift.streakable and e.streaking:      # tunggu combo selesai
        return
    jumlah = e.repeat_count if e.gift.streakable else 1
    apel = apel_untuk(e.gift) * max(1, jumlah)
    nama = e.user.nickname or e.user.unique_id
    print(f"GIFT: {nama} -> {jumlah}x {e.gift.name} = +{apel} apel")
    jadwalkan_duck()                            # musik mengecil sebentar saat gift masuk
    await kirim({"type": "gift", "user": nama, "gift": f"{jumlah}x {e.gift.name}" if jumlah > 1 else e.gift.name, "apples": apel, "icon": GIFTS.get(e.gift.name, ("🎁",))[0]})


async def main():
    global _bgm_task
    siapkan_mixer()
    threading.Thread(target=http_server, daemon=True).start()
    await websockets.serve(ws_handler, "localhost", WS_PORT)
    if BGM_AKTIF and _bgm_siap:
        _bgm_task = asyncio.create_task(putar_musik_latar())   # simpan referensi task
    buka_browser(f"http://localhost:{HTTP_PORT}/snake.html?ws={WS_PORT}")
    print(f"Game: http://localhost:{HTTP_PORT}/snake.html  | akun: @{USERNAME}")
    try:
        while True:
            c = TikTokLiveClient(unique_id=USERNAME)
            c.add_listener(GiftEvent, on_gift)
            try:
                print("Menyambung ke live...")
                await c.connect()
            except UserOfflineError:
                print("Belum live, coba lagi 15 detik..."); await asyncio.sleep(15)
            except Exception as err:
                print("Putus:", err); await asyncio.sleep(5)
    finally:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Berhenti.")
