import os
import time
import random
import logging
import traceback
import glob
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
SESSIONS_DIR = "sessions"
DEFAULT_ACCOUNTS = """restisukawati,jasuke00
devanoaditama21,jasuke00
lia.santika24,jasuke00"""
DEFAULT_TARGET = "https://www.instagram.com/p/DPWSqohCp2a/"
DEFAULT_COMMENTS = "Keren!\nMantap!\nGood content!"

FALLBACK_RETRIES = 3
FALLBACK_BACKOFF = 2

# ================================
# FUNGSI UTILITAS SESSION
# ================================

def ensure_sessions_dir():
    """Membuat directory sessions jika belum ada"""
    if not os.path.exists(SESSIONS_DIR):
        os.makedirs(SESSIONS_DIR)
        logger.info(f"Directory sessions dibuat: {SESSIONS_DIR}")

def get_session_path(username: str) -> str:
    """Mendapatkan path file session"""
    ensure_sessions_dir()
    return os.path.join(SESSIONS_DIR, f"session_{username}.json")

def cleanup_expired_sessions(days_old: int = 30):
    """Membersihkan session file yang sudah expired"""
    try:
        current_time = time.time()
        expired_count = 0
        
        for session_file in glob.glob(os.path.join(SESSIONS_DIR, "session_*.json")):
            try:
                file_time = os.path.getmtime(session_file)
                if current_time - file_time > days_old * 86400:  # days to seconds
                    os.remove(session_file)
                    expired_count += 1
                    logger.info(f"Session expired dihapus: {os.path.basename(session_file)}")
            except Exception as e:
                logger.warning(f"Gagal menghapus session file {session_file}: {e}")
        
        if expired_count > 0:
            logger.info(f"Total session expired dihapus: {expired_count}")
            
    except Exception as e:
        logger.error(f"Error saat cleanup sessions: {e}")

def validate_session_age(session_file: str, max_age_days: int = 30) -> bool:
    """Validasi usia session file"""
    if not os.path.exists(session_file):
        return False
    
    file_time = os.path.getmtime(session_file)
    current_time = time.time()
    
    return (current_time - file_time) <= (max_age_days * 86400)

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
            # Buat file default jika tidak ada
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
            # Buat file default jika tidak ada
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
# FUNGSI LOGIN & SESSION MANAGEMENT
# ================================

def login_client_for_account(username: str, password: str, twofa: str = None, proxy: str = None) -> Client:
    """Login dengan session management yang ditingkatkan"""
    session_file = get_session_path(username)
    cl = Client()
    
    # Set proxy jika ada
    if proxy:
        try:
            cl.set_proxy(proxy)
            logger.info(f"[{username}] Proxy set: {proxy}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")

    # Coba load session yang ada dengan validasi usia
    if os.path.exists(session_file) and validate_session_age(session_file):
        try:
            cl.load_settings(session_file)
            logger.info(f"[{username}] Session loaded dari {session_file}")
            
            # Test session dengan request ringan
            try:
                user_id = cl.user_id
                logger.debug(f"[{username}] Session valid, user_id: {user_id}")
                return cl
            except Exception as e:
                logger.warning(f"[{username}] Session expired atau invalid: {e}")
                # Session tidak valid, hapus dan login ulang
                try:
                    os.remove(session_file)
                    logger.info(f"[{username}] Session invalid dihapus")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[{username}] Gagal load session: {e}")
            try:
                os.remove(session_file)
            except Exception:
                pass

    # Login baru diperlukan
    logger.info(f"[{username}] Melakukan login baru...")
    try:
        if twofa:
            cl.login(username, password, verification_code=twofa)
        else:
            cl.login(username, password)
    except TwoFactorRequired:
        logger.error(f"[{username}] Diperlukan 2FA/OTP.")
        raise RuntimeError(f"[{username}] Diperlukan kode 2FA/OTP. Tambahkan kode pada input akun (username,password,2fa).")
    except ChallengeRequired:
        logger.error(f"[{username}] ChallengeRequired: Verifikasi manual diperlukan.")
        raise RuntimeError(f"[{username}] Verifikasi IG (challenge) diperlukan; verifikasi manual lewat Instagram.")
    except ClientError as e:
        logger.error(f"[{username}] ClientError saat login: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")
    except Exception as e:
        logger.error(f"[{username}] Error tak terduga saat login: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")

    # Simpan session setelah login sukses
    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Login sukses, session disimpan di {session_file}")
    except Exception as e:
        logger.warning(f"[{username}] Gagal menyimpan session: {e}")

    return cl

def backup_session_files():
    """Membuat backup session files"""
    try:
        backup_dir = os.path.join(SESSIONS_DIR, "backup")
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"sessions_backup_{timestamp}")
        
        import shutil
        shutil.copytree(SESSIONS_DIR, backup_path, ignore=shutil.ignore_patterns('backup'))
        logger.info(f"Backup session dibuat: {backup_path}")
        return True
    except Exception as e:
        logger.warning(f"Gagal membuat backup session: {e}")
        return False

# ================================
# FUNGSI UTAMA BOT
# ================================

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
    """Menjalankan aksi like dan comment untuk satu akun"""
    try:
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
    st.set_page_config(page_title="Muda Gembira", layout="wide")
    st.title("🤖 Sistem Muda Gembira - Enhanced")
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
    
    # Tab untuk memisahkan konfigurasi dan monitoring
    tab1, tab2, tab3 = st.tabs(["⚙️ Konfigurasi", "📊 Monitoring", "🔧 Session Management"])
    
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
                height=120,
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
                height=120,
                help="Bot akan memilih komentar secara acak dari daftar ini",
                key="comments_input",
                on_change=update_comments
            )
            
            # Tombol refresh untuk memuat ulang komentar dari file
            col_btn_com = st.columns([1, 1])
            with col_btn_com[0]:
                if st.button("🔄 Muat Ulang Komentar dari File", use_container_width=True):
                    st.session_state.comments_data = load_comments_from_file()
                    st.rerun()
            with col_btn_com[1]:
                if st.button("💾 Simpan Komentar ke File", use_container_width=True):
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
                delay_between_accounts = st.number_input("Delay antar akun (detik)", min_value=1, value=5)
            
            delay_between_rounds = st.number_input("Delay antar putaran (detik)", min_value=1, value=10)
            proxy_input = st.text_input("Proxy (opsional)", placeholder="http://user:pass@host:port")
            
            st.subheader("Session Settings")
            session_max_age = st.number_input("Maksimal usia session (hari)", min_value=1, max_value=90, value=30)
            auto_cleanup = st.checkbox("Auto-cleanup session expired", value=True)
        
        # Tombol aksi
        col_btn1, col_btn2, col_btn3 = st.columns([1,1,2])
        with col_btn1:
            start_button = st.button("🚀 JALANKAN BOT", type="primary", use_container_width=True)
        with col_btn2:
            stop_button = st.button("⏹️ BERHENTI", use_container_width=True)
        
        if stop_button:
            st.session_state["stop_requested"] = True
            st.warning("Menghentikan proses...")
    
    with tab2:
        st.header("Monitoring & Logs")
        
        # Statistik real-time
        col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
        with col_stat1:
            accounts = parse_accounts_input(st.session_state.accounts_data)
            st.metric("Jumlah Akun", len(accounts))
        with col_stat2:
            comments = [c.strip() for c in st.session_state.comments_data.splitlines() if c.strip()]
            st.metric("Komentar Tersedia", len(comments))
        with col_stat3:
            st.metric("Target Komentar/Akun", max_comments)
        with col_stat4:
            # Hitung jumlah session yang ada
            session_files = glob.glob(os.path.join(SESSIONS_DIR, "session_*.json"))
            valid_sessions = [sf for sf in session_files if validate_session_age(sf)]
            st.metric("Session Valid", f"{len(valid_sessions)}/{len(accounts)}")
        
        # Progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Area log
        st.subheader("Log Aktivitas")
        log_container = st.container()
        with log_container:
            log_display = st.empty()
    
    with tab3:
        st.header("Session Management")
        
        col_sess1, col_sess2 = st.columns(2)
        
        with col_sess1:
            st.subheader("Session Operations")
            
            if st.button("🔄 Cleanup Expired Sessions", use_container_width=True):
                with st.spinner("Membersihkan session expired..."):
                    cleanup_expired_sessions()
                    st.success("Cleanup session selesai!")
                    st.rerun()
            
            if st.button("💾 Backup All Sessions", use_container_width=True):
                with st.spinner("Membuat backup session..."):
                    if backup_session_files():
                        st.success("Backup session berhasil!")
                    else:
                        st.error("Gagal membuat backup session!")
            
            if st.button("🗑️ Delete All Sessions", use_container_width=True):
                if st.checkbox("Yakin hapus semua session?"):
                    session_files = glob.glob(os.path.join(SESSIONS_DIR, "session_*.json"))
                    deleted_count = 0
                    for sf in session_files:
                        try:
                            os.remove(sf)
                            deleted_count += 1
                        except Exception as e:
                            st.error(f"Gagal hapus {sf}: {e}")
                    st.success(f"Berhasil menghapus {deleted_count} session files!")
                    st.rerun()
        
        with col_sess2:
            st.subheader("Session Info")
            
            session_files = glob.glob(os.path.join(SESSIONS_DIR, "session_*.json"))
            if session_files:
                st.write(f"**Total Session Files:** {len(session_files)}")
                
                # Tampilkan info session
                for sf in session_files[:10]:  # Batasi tampilan
                    username = os.path.basename(sf).replace("session_", "").replace(".json", "")
                    file_time = os.path.getmtime(sf)
                    age_days = (time.time() - file_time) / 86400
                    
                    col_info1, col_info2 = st.columns([2,1])
                    with col_info1:
                        st.write(f"`{username}`")
                    with col_info2:
                        st.write(f"{age_days:.1f} hari")
                
                if len(session_files) > 10:
                    st.info(f"Dan {len(session_files) - 10} session lainnya...")
            else:
                st.info("Tidak ada session files yang ditemukan.")

    # Fungsi render logs
    def render_logs():
        logs = st.session_state.get("logs", [])
        to_display = "\n".join(logs[-100:])  # Tampilkan 100 baris terakhir
        log_display.text_area("Logs", value=to_display, height=300, label_visibility="collapsed")

    # Jalankan bot ketika tombol ditekan
    if start_button:
        st.session_state["stop_requested"] = False
        
        # Auto-cleanup session jika diaktifkan
        if auto_cleanup:
            with st.spinner("Membersihkan session expired..."):
                cleanup_expired_sessions(session_max_age)
        
        # Validasi input
        accounts = parse_accounts_input(st.session_state.accounts_data)
        comments = [c.strip() for c in st.session_state.comments_data.splitlines() if c.strip()]
        
        if not accounts:
            st.error("❌ Tidak ada akun yang dimasukkan")
            return
        if not comments:
            st.error("❌ Tidak ada komentar yang ditentukan")
            return
        if not target_post.strip():
            st.error("❌ Masukkan URL postingan target")
            return
        
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
                
                time.sleep(1)
                render_logs()

            if successful_logins == 0:
                st.error("❌ Tidak ada akun yang berhasil login")
                return

            st.success(f"✅ {successful_logins}/{len(accounts)} akun berhasil login")
            
            # Jalankan proses utama
            comment_counts = {acc["username"]: 0 for acc in accounts}
            delays = {"after_like": delay_after_like, "after_comment": delay_after_comment}
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
                            run_buzzer_for_account(cl, username, target_post, comments, comment_counts, max_comments, delays)
                            # Backup session periodik
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

                        if j < len(clients_active) - 1:
                            for _ in range(delay_between_accounts):
                                if st.session_state.get("stop_requested"):
                                    break
                                time.sleep(1)
                        render_logs()

                    if all(count >= max_comments for count in comment_counts.values()):
                        logger.info("Semua akun mencapai limit komentar")
                        break

                    for _ in range(delay_between_rounds):
                        if st.session_state.get("stop_requested"):
                            break
                        time.sleep(1)
                    render_logs()

                    if not clients_active:
                        logger.error("Tidak ada akun aktif tersisa")
                        break

                # Tampilkan summary
                st.success("✅ Proses selesai!")
                st.subheader("📊 Hasil Akhir")
                
                total_comments = 0
                for username in comment_counts:
                    count = comment_counts[username]
                    st.write(f"- {username}: {count} komentar")
                    total_comments += count
                
                col_res1, col_res2 = st.columns(2)
                with col_res1:
                    st.metric("Total Komentar Dikirim", total_comments)
                with col_res2:
                    st.metric("Akun Aktif Akhir", f"{len(clients_active)}/{len(accounts)}")

            except Exception as e:
                st.error(f"Error selama proses: {e}")
                logger.error(f"Error selama proses: {traceback.format_exc()}")
            finally:
                # Final backup sessions
                backup_session_files()
                render_logs()

    # Selama tidak berjalan, tetap render logs
    render_logs()

if __name__ == "__main__":
    # Cleanup session expired saat start
    cleanup_expired_sessions(30)
    main()
