import asyncio
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import websockets

try:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import GiftEvent
    from TikTokLive.client.errors import UserOfflineError
except ImportError:
    TikTokLiveClient = None
    GiftEvent = None
    UserOfflineError = None

try:
    import pygame
except ImportError:
    pygame = None

# ================== CONFIGURATION ==================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MP3_DIR = os.path.join(BASE_DIR, "mp3")
HTTP_PORT = int(os.environ.get("PORT", 8001))
WS_PORT = int(os.environ.get("WS_PORT", 8766))
HOST = os.environ.get("HOST", "0.0.0.0")

BGM_AKTIF = os.environ.get("BGM", "true").lower() in ("1", "true", "yes")
BGM_VOLUME = 0.28
BGM_VOLUME_DUCK = 0.09
BGM_DUCK_DETIK = 2.6

GIFTS = {
    "Rose": ("🌹", 5),
    "Finger Heart": ("🫰", 5),
    "Ice Cream Cone": ("🍦", 8),
    "Hand Hearts": ("🤲", 20),
    "Doughnut": ("🍩", 30),
    "Swan": ("🦢", 40),
    "Corgi": ("🐶", 50),
    "Money Gun": ("🔫", 70),
    "Galaxy": ("🌌", 150),
    "Lion": ("🦁", 300),
    "TikTok Universe": ("🪐", 500),
}

# ================== GLOBAL STATE ==================
penghubung = set()
_bgm_siap = False
_bgm_ducked = False
_bgm_task = None
_duck_tasks = set()
_loop = None

server_state = {
    "running": False,
    "connected": False,
    "username": os.environ.get("DEFAULT_USERNAME", "").lstrip("@"),
    "status_message": "Standby - Menunggu instruksi",
    "gifts_count": 0,
    "apples_total": 0,
    "start_time": None,
    "recent_gifts": [],
}

tiktok_task = None
tiktok_client = None


# ---------------- Audio / BGM Support ----------------
def siapkan_mixer():
    global _bgm_siap
    if not BGM_AKTIF or pygame is None:
        return
    try:
        # Avoid hanging on headless environments if ALSA/PulseAudio is absent
        if os.environ.get("SDL_AUDIODRIVER") == "dummy":
            return
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(8)
        _bgm_siap = True
        print(f"[BGM] Pygame audio mixer aktif di folder: {MP3_DIR}")
    except Exception as err:
        print("[BGM] Audio device fisik tidak tersedia (mode headless / VPS):", err)
        _bgm_siap = False


def daftar_musik_latar():
    if not os.path.isdir(MP3_DIR):
        return []
    ekstensi = (".mp3", ".ogg", ".wav", ".flac", ".m4a")
    return [
        os.path.join(MP3_DIR, n)
        for n in sorted(os.listdir(MP3_DIR), key=str.lower)
        if n.lower().endswith(ekstensi) and os.path.isfile(os.path.join(MP3_DIR, n))
    ]


def atur_volume_bgm(ducked):
    global _bgm_ducked
    _bgm_ducked = ducked
    if not _bgm_siap:
        return
    try:
        pygame.mixer.music.set_volume(BGM_VOLUME_DUCK if ducked else BGM_VOLUME)
    except Exception:
        pass


async def duck_sebentar(detik=BGM_DUCK_DETIK):
    if not _bgm_siap:
        return
    atur_volume_bgm(True)
    await asyncio.sleep(detik)
    atur_volume_bgm(False)


def jadwalkan_duck():
    if not _bgm_siap or not _loop:
        return
    t = _loop.create_task(duck_sebentar())
    _duck_tasks.add(t)
    t.add_done_callback(_duck_tasks.discard)


async def putar_musik_latar():
    if not _bgm_siap:
        return
    lagu = daftar_musik_latar()
    if not lagu:
        return
    i = 0
    while BGM_AKTIF:
        path = lagu[i]
        i = (i + 1) % len(lagu)
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(BGM_VOLUME_DUCK if _bgm_ducked else BGM_VOLUME)
            pygame.mixer.music.play()
        except Exception:
            await asyncio.sleep(2.0)
            continue
        while BGM_AKTIF:
            try:
                if not pygame.mixer.music.get_busy():
                    break
            except Exception:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(0.4)


# ---------------- WebSocket & Broadcast ----------------
def apel_untuk(gift_name, diamond_count=1):
    if gift_name in GIFTS:
        return GIFTS[gift_name][1]
    k = diamond_count or 1
    for batas, apel in [(20, 5), (50, 10), (100, 20), (500, 60), (1000, 100)]:
        if k < batas:
            return apel
    return 200


async def ws_handler(ws):
    penghubung.add(ws)
    try:
        # Kirim konfigurasi gift saat browser baru tersambung
        await ws.send(
            json.dumps({"type": "config", "gifts": [[i, n, a] for n, (i, a) in GIFTS.items()]})
        )
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


async def handle_gift_event(nama, gift_name, jumlah=1, diamond_count=1):
    apel = apel_untuk(gift_name, diamond_count) * max(1, jumlah)
    icon = GIFTS.get(gift_name, ("🎁", 1))[0]
    display_gift = f"{jumlah}x {gift_name}" if jumlah > 1 else gift_name

    server_state["gifts_count"] += 1
    server_state["apples_total"] += apel
    server_state["recent_gifts"].insert(
        0,
        {
            "user": nama,
            "gift": display_gift,
            "apples": apel,
            "icon": icon,
            "time": time.strftime("%H:%M:%S"),
        },
    )
    if len(server_state["recent_gifts"]) > 30:
        server_state["recent_gifts"].pop()

    print(f"[GIFT] {nama} -> {display_gift} = +{apel} apel")
    jadwalkan_duck()
    await kirim({"type": "gift", "user": nama, "gift": display_gift, "apples": apel, "icon": icon})


# ---------------- TikTok Live Client Lifecycle ----------------
async def tiktok_runner(username):
    global tiktok_client
    server_state["running"] = True
    server_state["connected"] = False
    server_state["username"] = username
    server_state["start_time"] = time.time()
    server_state["status_message"] = f"Menghubungkan ke @{username}..."

    if TikTokLiveClient is None:
        server_state["status_message"] = "TikTokLive belum terpasang (pip install TikTokLive)"
        server_state["running"] = False
        return

    async def _on_gift(e: GiftEvent):
        if e.gift.streakable and e.streaking:
            return
        jumlah = e.repeat_count if e.gift.streakable else 1
        nama = e.user.nickname or e.user.unique_id
        await handle_gift_event(
            nama=nama,
            gift_name=e.gift.name,
            jumlah=jumlah,
            diamond_count=e.gift.diamond_count or 1,
        )

    while server_state["running"]:
        try:
            c = TikTokLiveClient(unique_id=username)
            tiktok_client = c
            c.add_listener(GiftEvent, _on_gift)
            server_state["status_message"] = f"Mencoba tersambung ke live @{username}..."
            print(f"[TIKTOK] Menghubungkan ke live @{username}...")

            await c.connect()
            server_state["connected"] = True
            server_state["status_message"] = f"Terhubung ke Live @{username}"
        except asyncio.CancelledError:
            print("[TIKTOK] Task dihentikan oleh pengguna.")
            break
        except Exception as err:
            server_state["connected"] = False
            msg = str(err)
            if "offline" in msg.lower() or (UserOfflineError and isinstance(err, UserOfflineError)):
                server_state["status_message"] = f"Akun @{username} sedang OFFLINE. Recheck 15 detik..."
                print(f"[TIKTOK] @{username} belum live, mencoba lagi dalam 15 detik...")
                await asyncio.sleep(15)
            else:
                server_state["status_message"] = f"Koneksi terputus: {msg}"
                print(f"[TIKTOK] Error: {msg}. Coba lagi 8 detik...")
                await asyncio.sleep(8)

    server_state["running"] = False
    server_state["connected"] = False
    server_state["status_message"] = "Live dihentikan"
    print(f"[TIKTOK] Berhenti memantau @{username}.")


async def start_tiktok(username):
    global tiktok_task
    username = username.strip().lstrip("@")
    if not username:
        return False, "Username tidak boleh kosong"

    if tiktok_task and not tiktok_task.done():
        tiktok_task.cancel()
        try:
            await tiktok_task
        except asyncio.CancelledError:
            pass

    tiktok_task = asyncio.create_task(tiktok_runner(username))
    return True, f"Memulai bot untuk @{username}"


async def stop_tiktok():
    global tiktok_task, tiktok_client
    server_state["running"] = False
    server_state["connected"] = False
    server_state["status_message"] = "Dihentikan oleh pengguna"
    if tiktok_client:
        try:
            await tiktok_client.disconnect()
        except Exception:
            pass
    if tiktok_task and not tiktok_task.done():
        tiktok_task.cancel()
    return True, "Bot TikTok Live berhasil dimatikan"


# ---------------- HTTP Request Handler & REST API ----------------
class AppHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        # Silence static request logs to keep terminal neat
        pass

    def send_json(self, data, status_code=200):
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Root route -> auto serve home.html
        if path == "/" or path == "":
            self.path = "/home.html"
            return super().do_GET()

        # REST API: Status
        if path == "/api/status":
            uptime = int(time.time() - server_state["start_time"]) if server_state["start_time"] and server_state["running"] else 0
            res = {
                "running": server_state["running"],
                "connected": server_state["connected"],
                "username": server_state["username"],
                "status_message": server_state["status_message"],
                "gifts_count": server_state["gifts_count"],
                "apples_total": server_state["apples_total"],
                "ws_clients": len(penghubung),
                "uptime_seconds": uptime,
                "recent_gifts": server_state["recent_gifts"],
                "http_port": HTTP_PORT,
                "ws_port": WS_PORT,
            }
            return self.send_json(res)

        # REST API: Songs in mp3 directory
        if path == "/api/songs":
            lagu = [os.path.basename(p) for p in daftar_musik_latar()]
            return self.send_json({"songs": lagu})

        # Static files fallback
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"

        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        if path == "/api/start":
            username = payload.get("username", "").strip()
            if not username:
                return self.send_json({"ok": False, "error": "Username tidak boleh kosong"}, 400)
            fut = asyncio.run_coroutine_threadsafe(start_tiktok(username), _loop)
            ok, msg = fut.result(timeout=5)
            return self.send_json({"ok": ok, "message": msg, "username": username})

        if path == "/api/stop":
            fut = asyncio.run_coroutine_threadsafe(stop_tiktok(), _loop)
            ok, msg = fut.result(timeout=5)
            return self.send_json({"ok": ok, "message": msg})

        if path == "/api/test-gift":
            user = payload.get("user", "Tester")
            gift_name = payload.get("gift", "Rose")
            count = int(payload.get("count", 1))
            diamonds = int(payload.get("diamonds", 1))

            fut = asyncio.run_coroutine_threadsafe(
                handle_gift_event(user, gift_name, count, diamonds), _loop
            )
            fut.result(timeout=5)
            return self.send_json({"ok": True, "message": f"Gift {gift_name} berhasil disimulasikan!"})

        self.send_json({"error": "Endpoint tidak ditemukan"}, 404)


def run_http_server():
    server_address = (HOST, HTTP_PORT)
    httpd = ThreadingHTTPServer(server_address, AppHTTPHandler)
    print(f"[HTTP] Server berjalan di http://{HOST}:{HTTP_PORT} (membuka home.html)")
    httpd.serve_forever()


# ---------------- Main Orchestrator ----------------
async def main_async():
    global _loop, _bgm_task
    _loop = asyncio.get_running_loop()

    # Siapkan background audio (opsional jika bukan headless)
    siapkan_mixer()
    if BGM_AKTIF and _bgm_siap:
        _bgm_task = asyncio.create_task(putar_musik_latar())

    # Jalankan HTTP server di thread daemon
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # Jalankan WebSocket server di loop async utama
    print(f"[WS] WebSocket Relay aktif di ws://{HOST}:{WS_PORT}")
    ws_server = await websockets.serve(ws_handler, HOST, WS_PORT)

    # Auto-start jika ada parameter username CLI atau environment
    cli_user = sys.argv[1].lstrip("@") if len(sys.argv) > 1 else server_state["username"]
    if cli_user:
        print(f"[INIT] Memulai bot TikTokLive untuk @{cli_user}...")
        asyncio.create_task(start_tiktok(cli_user))

    # Jaga agar server tetap hidup
    await asyncio.Future()


if __name__ == "__main__":
    print("=" * 60)
    print("[*] SNAKE LIVE CONTROL HUB & STREAM OVERLAY")
    print(f" Web Dashboard: http://localhost:{HTTP_PORT} (atau http://IP-VPS:{HTTP_PORT})")
    print(f" WebSocket API: ws://localhost:{WS_PORT}")
    print("=" * 60)
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[STOP] Server dimatikan dengan aman.")
