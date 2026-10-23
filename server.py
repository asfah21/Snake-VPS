import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
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

# ================== LOCAL .ENV LOADER ==================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Muat .env sederhana tanpa dependency. Environment yang sudah ada menang."""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return
    except Exception as err:
        print(f"[ENV] Peringatan: gagal membaca {path}: {err}")
        return

    if os.name == "posix":
        try:
            if os.stat(path).st_mode & 0o077:
                print(f"[ENV] Peringatan: permission {path} terlalu terbuka. Jalankan: chmod 600 {path}")
        except OSError:
            pass

    loaded = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    if loaded:
        print(f"[ENV] Memuat {loaded} variabel dari {path}")


_load_dotenv(os.path.join(BASE_DIR, ".env"))


# ================== CONFIGURATION ==================
def env_int(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


MP3_DIR = os.path.join(BASE_DIR, "mp3")
SFX_DIR = os.path.join(BASE_DIR, "sfx")
AUDIO_EXTENSIONS = (".mp3", ".ogg", ".wav", ".flac", ".m4a")
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")
HTTP_PORT = env_int("PORT", 8001)
WS_PORT = env_int("WS_PORT", 8766)
HOST = os.environ.get("HOST", "0.0.0.0")

# Hanya file ini yang boleh diakses langsung lewat HTTP.
STATIC_PAGES = {"/home.html", "/login.html", "/engine.html", "/snake.html"}

# ================== AUTH / SECURITY CONFIG ==================
COOKIE_NAME = "snake_session"
PBKDF2_ITERATIONS = 260_000
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = env_int("JWT_EXPIRE_HOURS", 12)
JWT_EXPIRE_SECONDS = JWT_EXPIRE_HOURS * 3600
JWT_SECRET = ""
OVERLAY_TOKEN = ""
AUTH_USERNAME = "admin"
AUTH_PASSWORD_HASH = ""
DUMMY_PASSWORD_HASH = ""

TRUST_PROXY = os.environ.get("TRUST_PROXY", "true").strip().lower() in ("1", "true", "yes", "on")
COOKIE_SECURE_MODE = os.environ.get("COOKIE_SECURE", "auto").strip().lower()
PUBLIC_ORIGIN = os.environ.get("PUBLIC_ORIGIN", "").strip()

LOGIN_MAX_ATTEMPTS = env_int("LOGIN_MAX_ATTEMPTS", 5)
LOGIN_WINDOW_SECONDS = env_int("LOGIN_WINDOW_SECONDS", 300)
API_RATE_LIMIT = env_int("API_RATE_LIMIT", 180)
API_RATE_WINDOW_SECONDS = env_int("API_RATE_WINDOW_SECONDS", 60)
WS_RATE_LIMIT = env_int("WS_RATE_LIMIT", 60)
WS_RATE_WINDOW_SECONDS = 60
MAX_WS_CLIENTS = env_int("MAX_WS_CLIENTS", 100)
MAX_BODY_BYTES = 16 * 1024

# ================== AUTH HELPERS ==================
def _secure_write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_or_create_secret(filename, factory, label):
    path = os.path.join(DATA_DIR, filename)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value:
                return value
        value = factory()
        _secure_write(path, value + "\n")
        print(f"[AUTH] {label} dibuat dan disimpan di {path}")
        return value
    except Exception as err:
        value = factory()
        print(f"[AUTH] Peringatan: gagal menyimpan {label} ({err}). Memakai nilai sementara.")
        return value


def hash_password(password):
    if not isinstance(password, str):
        raise ValueError("Password harus berupa string")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256:{}:{}:{}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _is_valid_password_hash(stored):
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split(":", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        if int(iterations) < 100_000:
            return False
        base64.b64decode(salt_b64, validate=True)
        base64.b64decode(digest_b64, validate=True)
        return True
    except Exception:
        return False


def verify_password(password, stored):
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split(":", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations)
        if iterations < 100_000 or iterations > 5_000_000:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def check_credentials(username, password):
    username = username or ""
    password = password or ""
    user_ok = hmac.compare_digest(
        hashlib.sha256(username.encode("utf-8")).digest(),
        hashlib.sha256(AUTH_USERNAME.encode("utf-8")).digest(),
    )
    stored = AUTH_PASSWORD_HASH if user_ok else DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, stored)
    return user_ok and password_ok


def initialize_auth():
    global AUTH_USERNAME, AUTH_PASSWORD_HASH, JWT_SECRET, OVERLAY_TOKEN, DUMMY_PASSWORD_HASH

    env_user = (os.environ.get("ADMIN_USERNAME") or "").strip()
    env_password = os.environ.get("ADMIN_PASSWORD") or ""
    env_hash = (os.environ.get("ADMIN_PASSWORD_HASH") or "").strip()

    if not env_user:
        print("[AUTH] ADMIN_USERNAME wajib diisi di .env.")
        return False
    if len(env_user) > 128 or "\n" in env_user or "\r" in env_user:
        print("[AUTH] ADMIN_USERNAME tidak valid (maksimal 128 karakter, tanpa baris baru).")
        return False
    AUTH_USERNAME = env_user

    if env_hash:
        if not _is_valid_password_hash(env_hash):
            print("[AUTH] ADMIN_PASSWORD_HASH tidak valid. Format: pbkdf2_sha256:iterasi:salt:hash")
            return False
        AUTH_PASSWORD_HASH = env_hash
        print(f"[AUTH] Login memakai ADMIN_PASSWORD_HASH dari .env untuk @{AUTH_USERNAME}.")
    elif env_password:
        if len(env_password) < 8:
            print("[AUTH] ADMIN_PASSWORD minimal 8 karakter.")
            return False
        if len(env_password) > 1024:
            print("[AUTH] ADMIN_PASSWORD maksimal 1024 karakter.")
            return False
        AUTH_PASSWORD_HASH = hash_password(env_password)
        print(f"[AUTH] Login memakai ADMIN_PASSWORD dari .env untuk @{AUTH_USERNAME}.")
    else:
        print("[AUTH] ADMIN_PASSWORD atau ADMIN_PASSWORD_HASH wajib diisi di .env.")
        print("[AUTH] Isi kredensial di .env, lalu restart container.")
        return False

    env_jwt = (os.environ.get("JWT_SECRET") or "").strip()
    if env_jwt and len(env_jwt) >= 32:
        JWT_SECRET = env_jwt
    else:
        if env_jwt:
            print("[AUTH] JWT_SECRET terlalu pendek (<32 karakter), memakai secret tersimpan.")
        JWT_SECRET = _load_or_create_secret(
            "jwt_secret", lambda: secrets.token_hex(32), "JWT secret"
        )

    env_overlay = (os.environ.get("OVERLAY_TOKEN") or "").strip()
    if env_overlay and len(env_overlay) >= 16:
        OVERLAY_TOKEN = env_overlay
    else:
        if env_overlay:
            print("[AUTH] OVERLAY_TOKEN terlalu pendek (<16 karakter), memakai token tersimpan.")
        OVERLAY_TOKEN = _load_or_create_secret(
            "overlay_token", lambda: secrets.token_urlsafe(24), "Overlay token"
        )

    DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))
    return True


def _b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text):
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def create_token(username):
    now = int(time.time())
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + JWT_EXPIRE_SECONDS,
        "jti": secrets.token_hex(8),
    }
    segments = [
        _b64url_encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ),
        _b64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ),
    ]
    signing_input = ".".join(segments)
    signature = hmac.new(
        JWT_SECRET.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url_encode(signature)


def verify_token(token):
    if not isinstance(token, str) or not token or len(token) > 2048:
        return None
    try:
        if token.count(".") != 2:
            return None
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(
            JWT_SECRET.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
        ).digest()
        actual = _b64url_decode(signature_b64)
        if not hmac.compare_digest(expected, actual):
            return None
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        if header.get("alg") != JWT_ALGORITHM:
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        now = int(time.time())
        expires_at = int(payload.get("exp", 0))
        issued_at = int(payload.get("iat", 0))
        if expires_at <= now or issued_at > now + 60:
            return None
        if payload.get("sub") != AUTH_USERNAME:
            return None
        return payload
    except Exception:
        return None


class RateLimiter:
    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key, limit, window_seconds):
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])) + 1)
                return False, retry_after
            events.append(now)
            return True, 0

    def reset(self, key):
        with self._lock:
            self._events.pop(key, None)


login_limiter = RateLimiter()
api_limiter = RateLimiter()
ws_limiter = RateLimiter()


def _client_ip(headers, fallback):
    if TRUST_PROXY:
        real_ip = headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip.strip() or fallback
        forwarded = headers.get("X-Forwarded-For", "")
        if forwarded:
            # NPM menambahkan IP client asli di akhir rantai.
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if parts:
                return parts[-1]
    return fallback


def _origin_allowed(origin, headers):
    if not origin:
        return True

    def normalize(netloc):
        netloc = netloc.strip().lower()
        if netloc.endswith(":443"):
            return netloc[:-4]
        if netloc.endswith(":80"):
            return netloc[:-3]
        return netloc

    try:
        origin_host = normalize(urlparse(origin).netloc)
    except Exception:
        return False
    candidates = set()
    host = headers.get("Host", "")
    if host:
        candidates.add(normalize(host))
    forwarded_host = headers.get("X-Forwarded-Host", "")
    if forwarded_host:
        candidates.add(normalize(forwarded_host.split(",")[0]))
    if PUBLIC_ORIGIN:
        candidates.add(normalize(urlparse(PUBLIC_ORIGIN).netloc))
    return origin_host in candidates


def _parse_cookie_header(raw):
    if not raw:
        return {}
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return {}
    return {key: morsel.value for key, morsel in cookie.items()}


def _extract_token_from_headers(headers):
    authorization = headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    cookies = _parse_cookie_header(headers.get("Cookie", ""))
    return cookies.get(COOKIE_NAME, "")


BGM_AKTIF = os.environ.get("BGM", "false").lower() in ("1", "true", "yes")
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
    return [
        os.path.join(MP3_DIR, n)
        for n in sorted(os.listdir(MP3_DIR), key=str.lower)
        if n.lower().endswith(AUDIO_EXTENSIONS) and os.path.isfile(os.path.join(MP3_DIR, n))
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
    try:
        request = ws.request
        headers = request.headers
        request_path = request.path
    except Exception:
        headers = {}
        request_path = "/ws"

    if not _origin_allowed(headers.get("Origin"), headers):
        await ws.close(code=1008, reason="Origin tidak diizinkan")
        return

    query_token = ""
    try:
        query = parse_qs(urlparse(request_path).query)
        query_token = (query.get("token") or [""])[0]
    except Exception:
        pass

    token = _extract_token_from_headers(headers)
    auth_payload = verify_token(token) if token else None
    if auth_payload is None and query_token:
        auth_payload = verify_token(query_token)

    overlay_ok = False
    if auth_payload is None and query_token and OVERLAY_TOKEN:
        try:
            overlay_ok = hmac.compare_digest(
                query_token.encode("utf-8"), OVERLAY_TOKEN.encode("utf-8")
            )
        except Exception:
            overlay_ok = False

    if auth_payload is None and not overlay_ok:
        await ws.close(code=1008, reason="Unauthorized")
        return

    remote_ip = ws.remote_address[0] if ws.remote_address else "unknown"
    client_ip = _client_ip(headers, remote_ip)
    allowed, _retry_after = ws_limiter.hit(
        f"ws:{client_ip}", WS_RATE_LIMIT, WS_RATE_WINDOW_SECONDS
    )
    if not allowed:
        await ws.close(code=1013, reason="Terlalu banyak koneksi")
        return
    if len(penghubung) >= MAX_WS_CLIENTS:
        await ws.close(code=1013, reason="Server penuh")
        return

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


async def handle_gift_event(
    nama,
    gift_name,
    jumlah=1,
    diamond_count=1,
    apples_override=None,
    icon_override=None,
):
    jumlah = max(1, int(jumlah or 1))
    if apples_override is not None:
        apel = max(0, int(apples_override))
    else:
        apel = apel_untuk(gift_name, diamond_count) * jumlah
    icon = icon_override or GIFTS.get(gift_name, ("🎁", 1))[0]
    nama = nama or "Seseorang"
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
        if e.gift is None or e.user is None:
            return
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
        tiktok_client = None
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

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if urlparse(self.path).path.lower().endswith((".html", ".htm")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data, status_code=200, extra_headers=None):
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def redirect(self, location, status_code=302):
        self.send_response(status_code)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def _audio_file_allowed(self, path, prefix, root):
        filename = unquote(path[len(prefix):])
        full_path = os.path.realpath(os.path.join(root, filename))
        root_real = os.path.realpath(root)
        return (
            bool(filename)
            and not filename.startswith(".")
            and "/" not in filename
            and "\\" not in filename
            and os.path.splitext(filename)[1].lower() in AUDIO_EXTENSIONS
            and os.path.commonpath([full_path, root_real]) == root_real
            and os.path.isfile(full_path)
        )

    def _static_file_allowed(self, path):
        if path in STATIC_PAGES:
            return True
        if path.startswith("/mp3/"):
            return self._audio_file_allowed(path, "/mp3/", MP3_DIR)
        if path.startswith("/sfx/"):
            return self._audio_file_allowed(path, "/sfx/", SFX_DIR)
        return False

    def client_ip(self):
        return _client_ip(self.headers, self.client_address[0])

    def is_https(self):
        if COOKIE_SECURE_MODE == "true":
            return True
        if COOKIE_SECURE_MODE == "false":
            return False
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "")
        return (
            forwarded_proto.split(",")[0].strip().lower() == "https"
            or self.headers.get("X-Forwarded-Ssl", "").lower() == "on"
        )

    def auth_payload(self):
        token = _extract_token_from_headers(self.headers)
        return verify_token(token) if token else None

    def csrf_ok(self):
        return _origin_allowed(self.headers.get("Origin"), self.headers)

    def has_overlay_token(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            token = (query.get("token") or [""])[0]
            return bool(token and OVERLAY_TOKEN) and hmac.compare_digest(
                token.encode("utf-8"), OVERLAY_TOKEN.encode("utf-8")
            )
        except Exception:
            return False

    def session_cookie(self, token, max_age):
        parts = [
            f"{COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={int(max_age)}",
        ]
        if self.is_https():
            parts.append("Secure")
        return "; ".join(parts)

    def redirect_to_login(self):
        path = urlparse(self.path).path or "/"
        self.redirect(f"/login.html?next={quote(path, safe='')}")

    def require_api_auth(self):
        if self.auth_payload():
            return True
        self.send_json({"ok": False, "error": "Unauthorized"}, 401)
        return False

    def is_json_request(self):
        content_type = self.headers.get("Content-Type", "")
        return content_type.split(";")[0].strip().lower() == "application/json"

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (TypeError, ValueError):
            return None, 400
        if length < 0 or length > MAX_BODY_BYTES:
            return None, 413
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}, 0
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, 400
        if not isinstance(payload, dict):
            return None, 400
        return payload, 0

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path in ("", "/", "/home.html"):
            if not self.auth_payload():
                self.send_response(401)
                self.end_headers()
                return
            self.path = "/home.html"
            return super().do_HEAD()
        if path in ("/engine", "/engine/", "/engine.html"):
            if not (self.auth_payload() or self.has_overlay_token()):
                self.send_response(401)
                self.end_headers()
                return
            self.path = "/engine.html"
            return super().do_HEAD()
        if path in ("/snake", "/snake/", "/snake.html"):
            if not (self.auth_payload() or self.has_overlay_token()):
                self.send_response(401)
                self.end_headers()
                return
            self.path = "/snake.html"
            return super().do_HEAD()
        if path == "/login.html":
            return super().do_HEAD()
        if path in ("/snake-v2.html", "/snake-v2"):
            query = urlparse(self.path).query
            self.redirect(f"/engine.html{f'?{query}' if query else ''}", 301)
            return
        if path.startswith(("/mp3/", "/sfx/")):
            if not (self.auth_payload() or self.has_overlay_token()):
                self.send_response(401)
                self.end_headers()
                return
            if self._static_file_allowed(path):
                return super().do_HEAD()
        if path.startswith("/api/"):
            self.send_response(405)
            self.send_header("Allow", "GET, POST")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Health check untuk Docker / Nginx Proxy Manager
        if path == "/healthz":
            return self.send_json(
                {
                    "ok": True,
                    "running": server_state["running"],
                    "connected": server_state["connected"],
                }
            )

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # Halaman login selalu publik. Jika sudah login, langsung ke dashboard.
        if path == "/login.html":
            if self.auth_payload():
                return self.redirect("/")
            if not self._static_file_allowed(path):
                return self.send_json({"error": "Halaman tidak ditemukan"}, 404)
            return super().do_GET()

        # Alias lama snake-v2.html -> engine.html
        if path in ("/snake-v2.html", "/snake-v2"):
            query = parsed.query
            return self.redirect(f"/engine.html{f'?{query}' if query else ''}", 301)

        # REST API dilindungi login (kecuali login/logout)
        if path.startswith("/api/"):
            if path == "/api/login":
                return self.send_json(
                    {"ok": False, "error": "Method not allowed"}, 405, {"Allow": "POST"}
                )
            if path == "/api/logout":
                return self.send_json(
                    {"ok": False, "error": "Method not allowed"}, 405, {"Allow": "POST"}
                )

            allowed, retry_after = api_limiter.hit(
                f"api:{self.client_ip()}", API_RATE_LIMIT, API_RATE_WINDOW_SECONDS
            )
            if not allowed:
                return self.send_json(
                    {"ok": False, "error": "Terlalu banyak permintaan"},
                    429,
                    {"Retry-After": str(retry_after)},
                )

            payload = self.auth_payload()
            if path == "/api/me":
                if not payload:
                    return self.send_json({"ok": False, "error": "Unauthorized"}, 401)
                return self.send_json(
                    {
                        "ok": True,
                        "username": payload.get("sub"),
                        "expires_at": payload.get("exp"),
                    }
                )

            if path == "/api/songs":
                if not (payload or self.has_overlay_token()):
                    return self.send_json({"ok": False, "error": "Unauthorized"}, 401)
                songs = [os.path.basename(p) for p in daftar_musik_latar()]
                return self.send_json({"songs": songs})

            if not payload:
                return self.send_json({"ok": False, "error": "Unauthorized"}, 401)

            if path == "/api/status":
                uptime = (
                    int(time.time() - server_state["start_time"])
                    if server_state["start_time"] and server_state["running"]
                    else 0
                )
                return self.send_json(
                    {
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
                )

            if path == "/api/config":
                return self.send_json(
                    {
                        "engine": "/engine.html",
                        "legacy": "/snake.html",
                        "ws_port": WS_PORT,
                        "ws_path": "/ws",
                        "overlay_token": OVERLAY_TOKEN,
                        "jwt_expires_hours": JWT_EXPIRE_HOURS,
                    }
                )

            return self.send_json({"error": "Endpoint tidak ditemukan"}, 404)

        # Dashboard: wajib login
        if path in ("", "/"):
            if not self.auth_payload():
                return self.redirect_to_login()
            self.path = "/home.html"
            return super().do_GET()

        if path == "/home.html":
            if not self.auth_payload():
                return self.redirect_to_login()
            return super().do_GET()

        # Game: login cookie atau overlay token (untuk OBS)
        if path in ("/engine", "/engine/", "/engine.html"):
            if not (self.auth_payload() or self.has_overlay_token()):
                return self.redirect_to_login()
            if path in ("/engine", "/engine/"):
                self.path = "/engine.html"
            return super().do_GET()

        if path in ("/snake", "/snake/", "/snake.html"):
            if not (self.auth_payload() or self.has_overlay_token()):
                return self.redirect_to_login()
            if path in ("/snake", "/snake/"):
                self.path = "/snake.html"
            return super().do_GET()

        # File audio: login atau overlay token (agar BGM/SFX tetap jalan di OBS)
        if path.startswith(("/mp3/", "/sfx/")):
            if not (self.auth_payload() or self.has_overlay_token()):
                return self.send_json({"error": "Unauthorized"}, 401)
            if self._static_file_allowed(path):
                return super().do_GET()
            return self.send_json({"error": "File tidak ditemukan"}, 404)

        return self.send_json({"error": "Halaman tidak ditemukan"}, 404)

    def handle_login(self):
        if not self.csrf_ok():
            return self.send_json({"ok": False, "error": "Origin tidak diizinkan"}, 403)
        if not self.is_json_request():
            return self.send_json({"ok": False, "error": "Content-Type harus application/json"}, 415)

        payload, error_status = self.read_json_body()
        if error_status:
            message = "Request terlalu besar" if error_status == 413 else "Format request tidak valid"
            return self.send_json({"ok": False, "error": message}, error_status)

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        if not username or not password:
            return self.send_json(
                {"ok": False, "error": "Username dan password wajib diisi"}, 400
            )
        if len(username) > 128 or len(password) > 1024:
            return self.send_json({"ok": False, "error": "Input terlalu panjang"}, 400)

        client_ip = self.client_ip()
        username_key = username.lower()
        allowed, retry_after = login_limiter.hit(
            f"login:ip:{client_ip}", LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS
        )
        if not allowed:
            return self.send_json(
                {"ok": False, "error": "Terlalu banyak percobaan login"},
                429,
                {"Retry-After": str(retry_after)},
            )
        allowed, retry_after = login_limiter.hit(
            f"login:user:{client_ip}:{username_key}",
            LOGIN_MAX_ATTEMPTS,
            LOGIN_WINDOW_SECONDS,
        )
        if not allowed:
            return self.send_json(
                {"ok": False, "error": "Terlalu banyak percobaan login"},
                429,
                {"Retry-After": str(retry_after)},
            )

        if not check_credentials(username, password):
            safe_user = "".join(ch if ch.isprintable() else " " for ch in username[:64])
            print(f"[AUTH] Login gagal dari {client_ip} untuk '{safe_user}'")
            return self.send_json(
                {"ok": False, "error": "Username atau password salah"}, 401
            )

        login_limiter.reset(f"login:ip:{client_ip}")
        login_limiter.reset(f"login:user:{client_ip}:{username_key}")
        token = create_token(AUTH_USERNAME)
        cookie = self.session_cookie(token, JWT_EXPIRE_SECONDS)
        print(f"[AUTH] Login sukses: @{AUTH_USERNAME} dari {client_ip}")
        return self.send_json(
            {
                "ok": True,
                "username": AUTH_USERNAME,
                "expires_in": JWT_EXPIRE_SECONDS,
            },
            extra_headers={"Set-Cookie": cookie},
        )

    def handle_logout(self):
        # Logout tidak memerlukan CSRF: menghapus sesi sendiri bukan aksi sensitif,
        # dan cookie harus tetap terhapus agar user tidak terjebak login loop.
        cookie = self.session_cookie("", 0)
        return self.send_json({"ok": True}, extra_headers={"Set-Cookie": cookie})

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            return self.handle_login()
        if path == "/api/logout":
            return self.handle_logout()

        allowed, retry_after = api_limiter.hit(
            f"api:{self.client_ip()}", API_RATE_LIMIT, API_RATE_WINDOW_SECONDS
        )
        if not allowed:
            return self.send_json(
                {"ok": False, "error": "Terlalu banyak permintaan"},
                429,
                {"Retry-After": str(retry_after)},
            )

        if not self.require_api_auth():
            return
        if not self.csrf_ok():
            return self.send_json({"ok": False, "error": "Origin tidak diizinkan"}, 403)
        if not self.is_json_request():
            return self.send_json(
                {"ok": False, "error": "Content-Type harus application/json"}, 415
            )

        payload, error_status = self.read_json_body()
        if error_status:
            message = "Request terlalu besar" if error_status == 413 else "Format request tidak valid"
            return self.send_json({"ok": False, "error": message}, error_status)

        if path == "/api/start":
            username = str(payload.get("username", "")).strip().lstrip("@")
            if not username:
                return self.send_json({"ok": False, "error": "Username tidak boleh kosong"}, 400)
            if len(username) > 64:
                return self.send_json({"ok": False, "error": "Username terlalu panjang"}, 400)
            try:
                fut = asyncio.run_coroutine_threadsafe(start_tiktok(username), _loop)
                ok, msg = fut.result(timeout=8)
            except Exception as err:
                return self.send_json({"ok": False, "error": f"Gagal memulai bot: {err}"}, 503)
            return self.send_json({"ok": ok, "message": msg, "username": username})

        if path == "/api/stop":
            try:
                fut = asyncio.run_coroutine_threadsafe(stop_tiktok(), _loop)
                ok, msg = fut.result(timeout=8)
            except Exception as err:
                return self.send_json({"ok": False, "error": f"Gagal menghentikan bot: {err}"}, 503)
            return self.send_json({"ok": ok, "message": msg})

        if path == "/api/test-gift":
            user = str(payload.get("user", "Tester")).strip() or "Tester"
            gift_name = str(payload.get("gift", "Rose")).strip() or "Rose"
            if len(user) > 64 or len(gift_name) > 64:
                return self.send_json({"ok": False, "error": "Input terlalu panjang"}, 400)
            try:
                count = max(1, int(payload.get("count", 1) or 1))
            except (TypeError, ValueError):
                count = 1
            try:
                diamonds = max(1, int(payload.get("diamonds", 1) or 1))
            except (TypeError, ValueError):
                diamonds = 1

            apples_override = payload.get("apples")
            if apples_override is not None:
                try:
                    apples_override = max(0, int(apples_override))
                except (TypeError, ValueError):
                    apples_override = None
            icon_override = str(payload.get("icon", "")).strip() or None
            if icon_override and len(icon_override) > 16:
                icon_override = icon_override[:16]

            try:
                fut = asyncio.run_coroutine_threadsafe(
                    handle_gift_event(
                        user,
                        gift_name,
                        count,
                        diamonds,
                        apples_override=apples_override,
                        icon_override=icon_override,
                    ),
                    _loop,
                )
                fut.result(timeout=5)
            except Exception as err:
                return self.send_json({"ok": False, "error": f"Gagal mengirim gift: {err}"}, 503)
            return self.send_json({"ok": True, "message": f"Gift {gift_name} berhasil disimulasikan!"})

        self.send_json({"error": "Endpoint tidak ditemukan"}, 404)


def run_http_server():
    server_address = (HOST, HTTP_PORT)
    httpd = ThreadingHTTPServer(server_address, AppHTTPHandler)
    httpd.daemon_threads = True
    print(f"[HTTP] Server berjalan di http://{HOST}:{HTTP_PORT} (membuka home.html)")
    print(f"[HTTP] Game default: http://{HOST}:{HTTP_PORT}/engine.html")
    print(f"[HTTP] Versi legacy : http://{HOST}:{HTTP_PORT}/snake.html")
    httpd.serve_forever()


# ---------------- Main Orchestrator ----------------
def cli_hash_password():
    import getpass

    print("[AUTH] Membuat hash password PBKDF2-SHA256...")
    first = getpass.getpass("Password baru: ")
    if len(first) < 8:
        print("Password minimal 8 karakter.")
        return 1
    second = getpass.getpass("Ulangi password: ")
    if first != second:
        print("Password tidak sama.")
        return 1
    print("\nSalin baris berikut ke .env (tanpa tanda kutip):\n")
    print("ADMIN_PASSWORD_HASH=" + hash_password(first))
    print("\nLalu restart container: docker compose up -d --force-recreate snake-live")
    return 0


async def main_async():
    global _loop, _bgm_task

    if not initialize_auth():
        print("[AUTH] Konfigurasi autentikasi tidak valid. Server dihentikan.")
        raise SystemExit(1)

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
    if "--hash-password" in sys.argv[1:]:
        raise SystemExit(cli_hash_password())

    print("=" * 60)
    print("[*] SNAKE LIVE CONTROL HUB & STREAM OVERLAY")
    print(f" Web Dashboard: http://localhost:{HTTP_PORT} (atau http://IP-VPS:{HTTP_PORT})")
    print(f" Login         : http://localhost:{HTTP_PORT}/login.html")
    print(f" WebSocket API: ws://localhost:{WS_PORT}")
    print("=" * 60)
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[STOP] Server dimatikan dengan aman.")
