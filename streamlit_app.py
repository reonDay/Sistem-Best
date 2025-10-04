import os
import time
import random
import logging
import traceback
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
DEFAULT_ACCOUNTS = """username1,password1
username2,password2"""
DEFAULT_TARGET = "https://www.instagram.com/p/Cxxxxxxxxxx/"
DEFAULT_COMMENTS = "Keren!\nMantap!\nGood content!"

FALLBACK_RETRIES = 3
FALLBACK_BACKOFF = 2

# ================================
# FUNGSI UTAMA
# ================================

def parse_accounts_input(text):
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

def login_client_for_account(username: str, password: str, twofa: str = None, proxy: str = None) -> Client:
    session_file = f"session_{username}.json"
    cl = Client()
    if proxy:
        try:
            cl.set_proxy(proxy)
            logger.info(f"[{username}] Proxy set: {proxy}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")

    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            try:
                cl.login(username, password)
            except Exception:
                logger.debug(f"[{username}] load_settings ok, login refresh gagal tapi lanjut.")
            logger.info(f"[{username}] Session loaded dari {session_file}.")
            return cl
        except Exception:
            try:
                os.remove(session_file)
            except Exception:
                pass

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

    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Login sukses, session disimpan.")
    except Exception as e:
        logger.warning(f"[{username}] Gagal menyimpan session: {e}")

    return cl

def _fallback_private_comment(cl: Client, media_pk: int, comment_text: str) -> bool:
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
    try:
        pk = cl.media_pk_from_url(target_url)
    except Exception as e:
        logger.error(f"[{username}] Gagal konversi URL ke media_pk: {e}")
        return

    try:
        cl.media_like(pk)
        logger.info(f"[{username}] Liked media PK {pk}")
        time.sleep(delays.get("after_like", 5))
    except Exception as e:
        logger.warning(f"[{username}] Gagal like: {e}")
        if isinstance(e, ChallengeRequired) or "Challenge" in str(e):
            raise RuntimeError(f"[{username}] Verifikasi IG diperlukan.")

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
    st.set_page_config(page_title="Instagram Bot - Simple", layout="wide")
    st.title("🤖 Instagram Bot Sederhana")
    st.markdown("Bot otomatis untuk like dan comment di Instagram")
    
    # Tab untuk memisahkan konfigurasi dan monitoring
    tab1, tab2 = st.tabs(["⚙️ Konfigurasi", "📊 Monitoring"])
    
    with tab1:
        st.header("Pengaturan Dasar")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Akun Instagram")
            st.markdown("Format: `username,password`")
            st.markdown("Untuk 2FA: `username,password,kode2fa`")
            accounts_input = st.text_area(
                "Masukkan akun-akun Anda:",
                value=DEFAULT_ACCOUNTS,
                height=120,
                help="Satu akun per baris"
            )
            
            st.subheader("Target Postingan")
            target_post = st.text_input(
                "URL postingan yang akan di-target:",
                value=DEFAULT_TARGET,
                help="Salin URL lengkap postingan Instagram"
            )
        
        with col2:
            st.subheader("Komentar")
            st.markdown("Satu komentar per baris")
            comments_input = st.text_area(
                "Daftar komentar yang akan digunakan:",
                value=DEFAULT_COMMENTS,
                height=120,
                help="Bot akan memilih komentar secara acak dari daftar ini"
            )
            
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
        col_stat1, col_stat2, col_stat3 = st.columns(3)
        with col_stat1:
            accounts = parse_accounts_input(accounts_input)
            st.metric("Jumlah Akun", len(accounts))
        with col_stat2:
            comments = [c.strip() for c in comments_input.splitlines() if c.strip()]
            st.metric("Komentar Tersedia", len(comments))
        with col_stat3:
            st.metric("Target Komentar/Akun", max_comments)
        
        # Progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Area log
        st.subheader("Log Aktivitas")
        log_container = st.container()
        with log_container:
            log_display = st.empty()

    # Fungsi render logs
    def render_logs():
        logs = st.session_state.get("logs", [])
        to_display = "\n".join(logs[-100:])  # Tampilkan 100 baris terakhir
        log_display.text_area("Logs", value=to_display, height=300, label_visibility="collapsed")

    # Inisialisasi session state
    if "logs" not in st.session_state:
        st.session_state["logs"] = []
    if "stop_requested" not in st.session_state:
        st.session_state["stop_requested"] = False

    # Jalankan bot ketika tombol ditekan
    if start_button:
        st.session_state["stop_requested"] = False
        
        # Validasi input
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
                            try:
                                cl.dump_settings(f"session_{username}.json")
                            except Exception:
                                pass
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
            finally:
                render_logs()

    # Selama tidak berjalan, tetap render logs
    render_logs()

if __name__ == "__main__":
    main()
