import os
import time
import random
import logging
import traceback
import json
import requests
from datetime import datetime, timedelta
import streamlit as st
from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, ClientError

# ================================
# KONFIGURASI LOGGING
# ================================
LOG_FILENAME = "bot_stealth.log"

logger = logging.getLogger("instagrapi_logger")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    file_handler = logging.FileHandler(LOG_FILENAME, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

class StreamlitLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        if "logs" not in st.session_state:
            st.session_state["logs"] = []

    def emit(self, record):
        msg = self.format(record)
        st.session_state["logs"].append(msg)
        if len(st.session_state["logs"]) > 2000:
            st.session_state["logs"] = st.session_state["logs"][-2000:]

if not any(isinstance(h, StreamlitLogHandler) for h in logger.handlers):
    sh = StreamlitLogHandler()
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

# ================================
# DEFAULT KONFIGURASI
# ================================
ACCOUNTS_FILE = "accounts.txt"
COMMENTS_FILE = "comments.txt"
SESSION_DIR = "sessions"
DEFAULT_ACCOUNTS = """restisukawati,jasuke00
devanoaditama21,jasuke00
lia.santika24,jasuke00"""
DEFAULT_TARGET = "https://www.instagram.com/p/DPWSqohCp2a/"
DEFAULT_COMMENTS = """Keren!
Mantap!
Good content!
Luar biasa!
Keren banget!"""

FALLBACK_RETRIES = 3
FALLBACK_BACKOFF = 2

# ================================
# FUNGSI UTILITAS
# ================================

def ensure_session_dir():
    """Membuat direktori session jika belum ada"""
    if not os.path.exists(SESSION_DIR):
        os.makedirs(SESSION_DIR)

def get_session_path(username):
    """Mendapatkan path file session"""
    ensure_session_dir()
    return os.path.join(SESSION_DIR, f"session_{username}.json")

def validate_instagram_url(url):
    """Validasi format URL Instagram"""
    if not url:
        return False
    patterns = [
        "https://www.instagram.com/p/",
        "https://instagram.com/p/",
        "https://www.instagram.com/reel/",
        "https://instagram.com/reel/"
    ]
    return any(pattern in url for pattern in patterns)

def get_random_user_agent():
    """Mendapatkan random user agent untuk mengurangi deteksi"""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]
    return random.choice(user_agents)

# ================================
# FUNGSI BACA/TULIS FILE AKUN & KOMENTAR
# ================================

def load_accounts_from_file():
    """Membaca data akun dari file"""
    try:
        if os.path.exists(ACCOUNTS_FILE):
            with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        else:
            with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_ACCOUNTS)
            return DEFAULT_ACCOUNTS
    except Exception as e:
        logger.error(f"Gagal membaca file akun: {e}")
        return DEFAULT_ACCOUNTS

def save_accounts_to_file(accounts_text):
    """Menyimpan data akun ke file"""
    try:
        with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
            f.write(accounts_text)
        logger.info("Data akun berhasil disimpan ke file")
        return True
    except Exception as e:
        logger.error(f"Gagal menyimpan file akun: {e}")
        return False

def load_comments_from_file():
    """Membaca data komentar dari file"""
    try:
        if os.path.exists(COMMENTS_FILE):
            with open(COMMENTS_FILE, 'r', encoding='utf-8') as f:
                return f.read().strip()
        else:
            with open(COMMENTS_FILE, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_COMMENTS)
            return DEFAULT_COMMENTS
    except Exception as e:
        logger.error(f"Gagal membaca file komentar: {e}")
        return DEFAULT_COMMENTS

def save_comments_to_file(comments_text):
    """Menyimpan data komentar ke file"""
    try:
        with open(COMMENTS_FILE, 'w', encoding='utf-8') as f:
            f.write(comments_text)
        logger.info("Data komentar berhasil disimpan ke file")
        return True
    except Exception as e:
        logger.error(f"Gagal menyimpan file komentar: {e}")
        return False

def parse_accounts_input(text):
    """Parse input akun dari text"""
    accounts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",", 2)]
        if len(parts) >= 2:
            accounts.append({
                "username": parts[0],
                "password": parts[1],
                "twofa": parts[2] if len(parts) > 2 else None
            })
    return accounts

# ================================
# SESSION MANAGEMENT
# ================================

def cleanup_old_sessions(max_age_days=7):
    """Membersihkan session file yang sudah lama"""
    try:
        ensure_session_dir()
        current_time = time.time()
        for filename in os.listdir(SESSION_DIR):
            if filename.startswith("session_") and filename.endswith(".json"):
                filepath = os.path.join(SESSION_DIR, filename)
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > max_age_days * 24 * 60 * 60:  # Convert days to seconds
                    os.remove(filepath)
                    logger.info(f"Session lama dihapus: {filename}")
    except Exception as e:
        logger.warning(f"Gagal membersihkan session lama: {e}")

def login_client_for_account(username: str, password: str, twofa: str = None, proxy: str = None) -> Client:
    """Login dengan session management yang lebih robust"""
    session_file = get_session_path(username)
    cl = Client()
    
    # Set random user agent
    try:
        cl.set_user_agent(get_random_user_agent())
    except Exception as e:
        logger.warning(f"[{username}] Gagal set user agent: {e}")
    
    if proxy:
        try:
            cl.set_proxy(proxy)
            logger.info(f"[{username}] Proxy set: {proxy}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")

    # Coba load session yang sudah ada
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            logger.info(f"[{username}] Session loaded dari {session_file}")
            
            # Verifikasi session masih valid dengan mencoba request kecil
            try:
                cl.get_timeline_feed()  # Test request kecil
                logger.info(f"[{username}] Session valid, lanjut tanpa login")
                return cl
            except Exception as e:
                logger.warning(f"[{username}] Session expired, perlu login ulang: {e}")
                os.remove(session_file)  # Hapus session expired
        except Exception as e:
            logger.warning(f"[{username}] Gagal load session: {e}")
            try:
                os.remove(session_file)
            except Exception:
                pass

    # Login fresh jika tidak ada session atau session invalid
    max_retries = 2
    for attempt in range(max_retries):
        try:
            logger.info(f"[{username}] Login attempt {attempt + 1}/{max_retries}")
            
            # Tambahkan delay random antara percobaan login
            if attempt > 0:
                delay = random.uniform(5, 10)
                logger.info(f"[{username}] Tunggu {delay:.1f} detik sebelum retry login")
                time.sleep(delay)
            
            if twofa:
                cl.login(username, password, verification_code=twofa)
            else:
                cl.login(username, password)
            
            # Simpan session setelah login sukses
            try:
                cl.dump_settings(session_file)
                logger.info(f"[{username}] Login sukses, session disimpan")
            except Exception as e:
                logger.warning(f"[{username}] Gagal menyimpan session: {e}")
            
            return cl
            
        except TwoFactorRequired:
            logger.error(f"[{username}] Diperlukan 2FA/OTP.")
            raise RuntimeError(f"[{username}] Diperlukan kode 2FA/OTP. Tambahkan kode pada input akun (username,password,2fa).")
        except ChallengeRequired:
            logger.error(f"[{username}] ChallengeRequired: Verifikasi manual diperlukan.")
            raise RuntimeError(f"[{username}] Verifikasi IG (challenge) diperlukan; verifikasi manual lewat Instagram.")
        except ClientError as e:
            logger.error(f"[{username}] ClientError saat login: {e}")
            if attempt == max_retries - 1:
                raise RuntimeError(f"[{username}] Login gagal setelah {max_retries} percobaan: {e}")
        except Exception as e:
            logger.error(f"[{username}] Error tak terduga saat login: {e}")
            if attempt == max_retries - 1:
                raise RuntimeError(f"[{username}] Login gagal: {e}")

    raise RuntimeError(f"[{username}] Login gagal setelah semua percobaan")

def _fallback_private_comment(cl: Client, media_pk: int, comment_text: str) -> bool:
    """Fallback method untuk komentar"""
    for attempt in range(1, FALLBACK_RETRIES + 1):
        try:
            endpoint = f"media/{media_pk}/comment/"
            data = {"comment_text": comment_text}
            logger.debug(f"Fallback attempt {attempt} -> {endpoint}")
            resp = cl.private_request(endpoint, data=data)
            if isinstance(resp, dict) and (resp.get("status") == "ok" or "comment_id" in resp or "comment" in resp):
                return True
            return True
        except Exception as e:
            logger.warning(f"Fallback private_request gagal (attempt {attempt}): {e}")
            if attempt == FALLBACK_RETRIES:
                return False
            sleep_time = FALLBACK_BACKOFF ** (attempt - 1)
            logger.info(f"Tunggu {sleep_time} detik sebelum retry fallback...")
            time.sleep(sleep_time)
    return False

def run_buzzer_for_account(cl: Client, username: str, target_url: str, comments: list, comment_counts: dict, max_comments: int, delays: dict):
    """Jalankan aksi like dan comment untuk satu akun"""
    try:
        # Validasi URL target
        if not validate_instagram_url(target_url):
            logger.error(f"[{username}] URL target tidak valid: {target_url}")
            return
        
        pk = cl.media_pk_from_url(target_url)
        logger.info(f"[{username}] Target media PK: {pk}")
    except Exception as e:
        logger.error(f"[{username}] Gagal konversi URL ke media_pk: {e}")
        return

    # Like media
    try:
        cl.media_like(pk)
        logger.info(f"[{username}] Liked media PK {pk}")
        time.sleep(delays.get("after_like", 5))
    except Exception as e:
        logger.warning(f"[{username}] Gagal like: {e}")
        if isinstance(e, ChallengeRequired) or "Challenge" in str(e):
            raise RuntimeError(f"[{username}] Verifikasi IG diperlukan.")

    # Comment media
    current = comment_counts.get(username, 0)
    if current >= max_comments:
        logger.info(f"[{username}] Skip komentar: limit tercapai ({current}/{max_comments})")
        return

    komentar = random.choice(comments) if comments else ""
    if not komentar:
        logger.info(f"[{username}] Tidak ada komentar tersedia.")
        return

    try:
        cl.media_comment(pk, komentar)
        comment_counts[username] = current + 1
        logger.info(f"[{username}] Berhasil komentar: '{komentar}' ({comment_counts[username]}/{max_comments})")
        time.sleep(delays.get("after_comment", 5))
        return
    except Exception as e:
        logger.warning(f"[{username}] media_comment gagal: {e}")
        if isinstance(e, ChallengeRequired) or "Challenge" in str(e):
            raise RuntimeError(f"[{username}] Verifikasi IG diperlukan.")
        if "Please wait" in str(e) or "action_blocked" in str(e).lower() or "429" in str(e):
            logger.error(f"[{username}] Terdeteksi rate-limit/action-blocked: {e}")
            return

    # Fallback comment method
    try:
        ok = _fallback_private_comment(cl, pk, komentar)
        if ok:
            comment_counts[username] = current + 1
            logger.info(f"[{username}] Berhasil komentar (fallback): '{komentar}' ({comment_counts[username]}/{max_comments})")
            time.sleep(delays.get("after_comment", 5))
        else:
            logger.warning(f"[{username}] Fallback komentar gagal setelah beberapa percobaan.")
    except Exception as e:
        logger.error(f"[{username}] Exception saat fallback komentar: {e}")
        if isinstance(e, ChallengeRequired) or "Challenge" in str(e):
            raise RuntimeError(f"[{username}] Verifikasi IG diperlukan.")

# ================================
# STREAMLIT UI SEDERHANA
# ================================
def main():
    st.set_page_config(page_title="Muda Gembira Pro", layout="wide")
    st.title("🤖 Sistem Muda Gembira Pro")
    st.markdown("Sistem Otomatisasi Instagram dengan Session Management")
    
    # Inisialisasi session state
    if "accounts_data" not in st.session_state:
        st.session_state.accounts_data = load_accounts_from_file()
    
    if "comments_data" not in st.session_state:
        st.session_state.comments_data = load_comments_from_file()
    
    if "logs" not in st.session_state:
        st.session_state["logs"] = []
    
    if "stop_requested" not in st.session_state:
        st.session_state["stop_requested"] = False
    
    if "process_running" not in st.session_state:
        st.session_state["process_running"] = False
    
    # Tab untuk memisahkan konfigurasi dan monitoring
    tab1, tab2, tab3 = st.tabs(["⚙️ Konfigurasi", "📊 Monitoring", "🛠️ Session Management"])
    
    with tab1:
        st.header("Pengaturan Dasar")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Akun Instagram")
            st.markdown("Format: `username,password` atau `username,password,2fa_code`")
            
            # Input akun dengan callback untuk auto-save
            def update_accounts():
                save_accounts_to_file(st.session_state.accounts_input)
                st.session_state.accounts_data = st.session_state.accounts_input
            
            accounts_input = st.text_area(
                "Masukkan akun-akun Anda:",
                value=st.session_state.accounts_data,
                height=150,
                help="Satu akun per baris",
                key="accounts_input",
                on_change=update_accounts
            )
            
            # Tombol refresh untuk memuat ulang dari file
            col_btn_acc = st.columns([1, 1])
            with col_btn_acc[0]:
                if st.button("🔄 Muat Ulang Akun dari File", use_container_width=True):
                    st.session_state.accounts_data = load_accounts_from_file()
                    st.rerun()
            with col_btn_acc[1]:
                if st.button("💾 Simpan Akun ke File", use_container_width=True):
                    if save_accounts_to_file(accounts_input):
                        st.success("Data akun berhasil disimpan!")
                    else:
                        st.error("Gagal menyimpan data akun!")
            
            st.subheader("Target Postingan")
            target_post = st.text_input(
                "URL postingan yang akan di-target:",
                value=DEFAULT_TARGET,
                help="Salin URL lengkap postingan Instagram"
            )
            
            # Validasi URL
            if target_post and not validate_instagram_url(target_post):
                st.warning("⚠️ Format URL Instagram tidak valid")
        
        with col2:
            st.subheader("Komentar")
            st.markdown("Satu komentar per baris")
            
            # Input komentar dengan callback untuk auto-save
            def update_comments():
                save_comments_to_file(st.session_state.comments_input)
                st.session_state.comments_data = st.session_state.comments_input
            
            comments_input = st.text_area(
                "Daftar komentar yang akan digunakan:",
                value=st.session_state.comments_data,
                height=150,
                help="Bot akan memilih komentar secara acak dari daftar ini",
                key="comments_input",
                on_change=update_comments
            )
            
            # Tombol refresh untuk memuat ulang komentar dari file
            col_btn_com = st.columns([1, 1])
            with col_btn_com[0]:
                if st.button("🔄 Muat Ulang Komentar", use_container_width=True):
                    st.session_state.comments_data = load_comments_from_file()
                    st.rerun()
            with col_btn_com[1]:
                if st.button("💾 Simpan Komentar", use_container_width=True):
                    if save_comments_to_file(comments_input):
                        st.success("Data komentar berhasil disimpan!")
                    else:
                        st.error("Gagal menyimpan data komentar!")
            
            st.subheader("Jumlah Aksi")
            col_a, col_b = st.columns(2)
            with col_a:
                max_comments = st.number_input(
                    "Max komentar per akun",
                    min_value=0,
                    max_value=50,
                    value=1,
                    help="Maksimal komentar yang dikirim per akun"
                )
            with col_b:
                iterations = st.number_input(
                    "Jumlah putaran",
                    min_value=1,
                    max_value=100,
                    value=1,
                    help="Berapa kali proses diulang"
                )
        
        # Pengaturan lanjutan dalam expander
        with st.expander("⚡ Pengaturan Lanjutan (Opsional)"):
            st.subheader("Delay Settings")
            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                delay_after_like = st.number_input("Delay setelah like (detik)", min_value=1, value=5)
            with col_d2:
                delay_after_comment = st.number_input("Delay setelah comment (detik)", min_value=1, value=5)
            with col_d3:
                delay_between_accounts = st.number_input("Delay antar akun (detik)", min_value=1, value=10)
            
            delay_between_rounds = st.number_input("Delay antar putaran (detik)", min_value=1, value=30)
            proxy_input = st.text_input("Proxy (opsional)", placeholder="http://user:pass@host:port")
            
            st.subheader("Keamanan")
            randomize_delay = st.checkbox("Randomize delay", value=True, 
                                         help="Tambahkan variasi random pada delay untuk terlihat lebih natural")
            enable_session_cleanup = st.checkbox("Auto-cleanup session lama", value=True,
                                                help="Hapus session file yang berusia lebih dari 7 hari")
        
        # Tombol aksi
        col_btn1, col_btn2, col_btn3 = st.columns([1,1,2])
        with col_btn1:
            start_button = st.button("🚀 JALANKAN BOT", type="primary", use_container_width=True, 
                                   disabled=st.session_state.get("process_running", False))
        with col_btn2:
            stop_button = st.button("⏹️ BERHENTI", use_container_width=True,
                                  disabled=not st.session_state.get("process_running", False))
        
        if stop_button:
            st.session_state["stop_requested"] = True
            st.warning("Menghentikan proses...")
    
    with tab2:
        st.header("Monitoring & Logs")
        
        # Statistik real-time
        accounts = parse_accounts_input(st.session_state.accounts_data)
        comments = [c.strip() for c in st.session_state.comments_data.splitlines() if c.strip()]
        
        col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
        with col_stat1:
            st.metric("Jumlah Akun", len(accounts))
        with col_stat2:
            st.metric("Komentar Tersedia", len(comments))
        with col_stat3:
            st.metric("Target Komentar/Akun", max_comments)
        with col_stat4:
            session_count = len([f for f in os.listdir(SESSION_DIR) if f.startswith("session_")]) if os.path.exists(SESSION_DIR) else 0
            st.metric("Session Tersimpan", session_count)
        
        # Progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Area log
        st.subheader("Log Aktivitas")
        log_container = st.container()
        with log_container:
            log_display = st.empty()
            
        # Tombol clear logs
        if st.button("🗑️ Clear Logs"):
            st.session_state["logs"] = []
            st.rerun()
    
    with tab3:
        st.header("Session Management")
        
        col_sess1, col_sess2 = st.columns(2)
        
        with col_sess1:
            st.subheader("Session Status")
            if os.path.exists(SESSION_DIR):
                session_files = [f for f in os.listdir(SESSION_DIR) if f.startswith("session_") and f.endswith(".json")]
                if session_files:
                    st.success(f"✅ {len(session_files)} session file ditemukan")
                    for sess_file in session_files[:10]:  # Tampilkan max 10 file
                        filepath = os.path.join(SESSION_DIR, sess_file)
                        file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                        st.write(f"• {sess_file} (modified: {file_time.strftime('%Y-%m-%d %H:%M')})")
                    if len(session_files) > 10:
                        st.info(f"... dan {len(session_files) - 10} session file lainnya")
                else:
                    st.warning("📝 Tidak ada session file yang ditemukan")
            else:
                st.info("📁 Direktori session belum dibuat")
        
        with col_sess2:
            st.subheader("Session Actions")
            
            if st.button("🔄 Cleanup Session Lama", help="Hapus session yang berusia lebih dari 7 hari"):
                cleanup_old_sessions()
                st.success("Cleanup session selesai!")
                st.rerun()
            
            if st.button("🗑️ Hapus Semua Session", help="Hapus semua session file"):
                if os.path.exists(SESSION_DIR):
                    session_files = [f for f in os.listdir(SESSION_DIR) if f.startswith("session_")]
                    for sess_file in session_files:
                        os.remove(os.path.join(SESSION_DIR, sess_file))
                    st.success(f"✅ {len(session_files)} session file dihapus")
                    st.rerun()
                else:
                    st.info("Tidak ada session yang perlu dihapus")
            
            st.subheader("Session Info")
            st.info("""
            **Fitur Session Management:**
            - ✅ Auto-save session setelah login
            - ✅ Auto-load session untuk login cepat
            - ✅ Session validation & repair
            - ✅ Periodic session backup
            - ✅ Session cleanup otomatis
            """)

    # Fungsi render logs
    def render_logs():
        logs = st.session_state.get("logs", [])
        to_display = "\n".join(logs[-100:])  # Tampilkan 100 baris terakhir
        log_display.text_area("Logs", value=to_display, height=400, label_visibility="collapsed")

    # Jalankan bot ketika tombol ditekan
    if start_button and not st.session_state.get("process_running"):
        st.session_state["process_running"] = True
        st.session_state["stop_requested"] = False
        
        # Validasi input
        accounts = parse_accounts_input(st.session_state.accounts_data)
        comments = [c.strip() for c in st.session_state.comments_data.splitlines() if c.strip()]
        
        if not accounts:
            st.error("❌ Tidak ada akun yang dimasukkan")
            st.session_state["process_running"] = False
            return
        if not comments:
            st.error("❌ Tidak ada komentar yang ditentukan")
            st.session_state["process_running"] = False
            return
        if not target_post.strip() or not validate_instagram_url(target_post):
            st.error("❌ Masukkan URL postingan target yang valid")
            st.session_state["process_running"] = False
            return
        
        # Cleanup session lama jika diaktifkan
        if enable_session_cleanup:
            cleanup_old_sessions()
        
        # Login semua akun
        with tab2:
            st.info("🔄 Proses login akun...")
            clients = {}
            successful_logins = 0
            
            for i, acc in enumerate(accounts):
                if st.session_state.get("stop_requested"):
                    break
                    
                username = acc["username"]
                password = acc["password"]
                twofa = acc.get("twofa")
                
                status_text.text(f"Login: {username} ({i+1}/{len(accounts)})")
                progress_bar.progress((i + 1) / len(accounts))
                
                try:
                    client = login_client_for_account(username, password, twofa, proxy_input or None)
                    clients[username] = client
                    successful_logins += 1
                    logger.info(f"✅ Login berhasil: {username}")
                except Exception as e:
                    logger.error(f"❌ Login gagal: {username} - {e}")
                
                # Delay antara login akun
                if i < len(accounts) - 1:
                    delay = delay_between_accounts
                    if randomize_delay:
                        delay = random.uniform(delay * 0.7, delay * 1.3)
                    for _ in range(int(delay)):
                        if st.session_state.get("stop_requested"):
                            break
                        time.sleep(1)
                render_logs()

            if successful_logins == 0:
                st.error("❌ Tidak ada akun yang berhasil login")
                st.session_state["process_running"] = False
                return

            st.success(f"✅ {successful_logins}/{len(accounts)} akun berhasil login")
            
            # Jalankan proses utama
            comment_counts = {acc["username"]: 0 for acc in accounts}
            delays_config = {
                "after_like": delay_after_like,
                "after_comment": delay_after_comment
            }
            clients_active = dict(clients)

            try:
                for round_idx in range(iterations):
                    if st.session_state.get("stop_requested"):
                        break
                        
                    status_text.text(f"🔄 Putaran {round_idx + 1}/{iterations}")
                    st.info(f"Putaran {round_idx + 1}/{iterations}")
                    
                    for j, (username, cl) in enumerate(list(clients_active.items())):
                        if st.session_state.get("stop_requested"):
                            break
                            
                        try:
                            run_buzzer_for_account(cl, username, target_post, comments, comment_counts, max_comments, delays_config)
                            # Simpan session setelah setiap aksi
                            try:
                                cl.dump_settings(get_session_path(username))
                            except Exception as e:
                                logger.warning(f"[{username}] Gagal backup session: {e}")
                        except RuntimeError as err:
                            logger.error(err)
                            try:
                                del clients_active[username]
                            except Exception:
                                pass
                            break
                        except Exception as e:
                            logger.error(f"[{username}] Error: {e}")
                            continue

                        # Delay antara akun
                        if j < len(clients_active) - 1:
                            delay = delay_between_accounts
                            if randomize_delay:
                                delay = random.uniform(delay * 0.7, delay * 1.3)
                            for _ in range(int(delay)):
                                if st.session_state.get("stop_requested"):
                                    break
                                time.sleep(1)
                        render_logs()

                    # Cek jika semua akun sudah mencapai limit
                    if all(count >= max_comments for count in comment_counts.values()):
                        logger.info("🎯 Semua akun mencapai limit komentar")
                        break

                    if st.session_state.get("stop_requested"):
                        break

                    # Delay antara putaran
                    if round_idx < iterations - 1:
                        delay = delay_between_rounds
                        if randomize_delay:
                            delay = random.uniform(delay * 0.8, delay * 1.5)
                        status_text.text(f"⏳ Tunggu {delay:.1f} detik sebelum putaran berikutnya...")
                        for _ in range(int(delay)):
                            if st.session_state.get("stop_requested"):
                                break
                            time.sleep(1)
                    render_logs()

                    if not clients_active:
                        logger.error("💥 Tidak ada akun aktif tersisa")
                        break

                # Tampilkan summary
                st.success("✅ Proses selesai!")
                st.subheader("📊 Hasil Akhir")
                
                total_comments = 0
                for username in comment_counts:
                    count = comment_counts[username]
                    status_icon = "✅" if count > 0 else "❌"
                    st.write(f"{status_icon} {username}: {count} komentar")
                    total_comments += count
                
                col_res1, col_res2 = st.columns(2)
                with col_res1:
                    st.metric("Total Komentar Dikirim", total_comments)
                with col_res2:
                    st.metric("Akun Aktif Akhir", f"{len(clients_active)}/{len(accounts)}")

            except Exception as e:
                st.error(f"❌ Error selama proses: {e}")
                logger.error(f"Error proses utama: {traceback.format_exc()}")
            finally:
                st.session_state["process_running"] = False
                render_logs()

    # Selama tidak berjalan, tetap render logs
    render_logs()

if __name__ == "__main__":
    main()
