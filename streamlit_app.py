# streamlit_instabot.py
# Requirements:
# pip install instagrapi streamlit

import os
import time
import random
import logging
import traceback
import streamlit as st
from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, ClientError

# Optional: pydantic ValidationError (tidak wajib; fallback ke Exception)
try:
    from pydantic import ValidationError
except Exception:
    ValidationError = Exception

# ========================
# Streamlit + File Logger
# ========================
LOG_FILENAME = "bot_stealth.log"

class StreamlitLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        if 'logs' not in st.session_state:
            st.session_state['logs'] = []

    def emit(self, record):
        msg = self.format(record)
        st.session_state['logs'].append(msg)
        # keep last 1000 lines
        if len(st.session_state['logs']) > 1000:
            st.session_state['logs'] = st.session_state['logs'][-1000:]

def create_logger():
    logger = logging.getLogger("instagrapi_streamlit")
    logger.setLevel(logging.DEBUG)

    # Clear existing handlers to avoid duplicates on rerun
    logger.handlers = []

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Streamlit handler
    sh = StreamlitLogHandler()
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # File handler
    fh = logging.FileHandler(LOG_FILENAME, mode='a', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger

logger = create_logger()

# ================================
# Helper: safe media_info (tahan error pydantic)
# ================================
def safe_media_info(cl: Client, media_pk):
    """
    Coba ambil media_info. Jika parsing pydantic gagal (mis. scans_profile missing),
    kembalikan None tapi jangan lempar sehingga proses like/comment bisa dilanjutkan.
    """
    try:
        return cl.media_info(media_pk)
    except ValidationError as ve:
        logger.warning(f"media_info parsing failed (ValidationError): {ve} — lanjut tanpa media_info")
        return None
    except Exception as e:
        # Tangani pesan yang biasa muncul saat scans_profile missing
        msg = str(e)
        if "scans_profile" in msg or "image_versions2.candidates" in msg or "Field required" in msg:
            logger.warning(f"media_info parsing/response missing field: {e} — lanjut tanpa media_info")
            return None
        # Untuk kesalahan lain, log dan kembalikan None (tidak menghentikan flow)
        logger.warning(f"media_info error (ignored): {e}")
        return None

# ================================
# Login function (dari skrip yang kamu kirim; disesuaikan untuk Streamlit)
# ================================
def login_client_for_account(username: str, password: str, two_factor_code: str = None, proxy_url: str = None):
    session_file = f"session_{username}.json"
    cl = Client()
    if proxy_url and proxy_url.strip():
        try:
            cl.set_proxy(proxy_url.strip())
            logger.info(f"[{username}] Menggunakan proxy: {proxy_url}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")

    # coba load session
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            # login tanpa memasukkan credentials lagi jika session valid
            cl.login(username, password)
            logger.info(f"[{username}] Session berhasil dimuat dari {session_file}")
            return cl
        except Exception as e:
            logger.info(f"[{username}] Gagal memuat session atau session expired: {e}")
            try:
                os.remove(session_file)
            except Exception:
                pass

    # login normal, dukung two_factor_code jika diberikan
    try:
        if two_factor_code:
            # beberapa versi instagrapi menerima verification_code param
            try:
                cl.login(username, password, verification_code=two_factor_code)
            except TypeError:
                # fallback jika method signature berbeda
                try:
                    # some versions provide two_factor_login method
                    cl.two_factor_login(two_factor_code)
                except Exception:
                    # final fallback: try normal login and hope for the best
                    cl.login(username, password)
        else:
            cl.login(username, password)
    except TwoFactorRequired:
        logger.error(f"[{username}] Diperlukan kode 2FA (tidak disediakan)")
        raise RuntimeError(f"[{username}] Diperlukan kode verifikasi 2FA")
    except ChallengeRequired:
        logger.error(f"[{username}] Diperlukan verifikasi challenge")
        raise RuntimeError(f"[{username}] Diperlukan verifikasi challenge manual")
    except ClientError as e:
        logger.error(f"[{username}] Error client saat login: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")
    except Exception as e:
        logger.error(f"[{username}] Error tak terduga saat login: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")

    # simpan session
    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Session disimpan ke {session_file}")
    except Exception as e:
        logger.warning(f"[{username}] Gagal menyimpan session: {e}")

    return cl

# ================================
# run_buzzer_for_account: gunakan safe_media_info untuk menghindari ValidationError
# ================================
def run_buzzer_for_account(cl: Client, username: str, target_post_url: str, comments: list, comment_counts: dict, max_comments: int):
    try:
        media_pk = cl.media_pk_from_url(target_post_url)
    except Exception as e:
        logger.error(f"[{username}] Gagal konversi URL ke media_pk: {e}")
        raise

    # Coba ambil media_info tetapi jangan biarkan error parsing menghentikan flow
    media_info = safe_media_info(cl, media_pk)
    try:
        if media_info is not None:
            code = getattr(media_info, "code", None) or getattr(media_info, "shortcode", None)
            logger.info(f"[{username}] Memproses postingan: {code or media_pk}")
        else:
            logger.info(f"[{username}] Memproses postingan (tanpa media_info): pk={media_pk}")

        # Like postingan
        try:
            cl.media_like(media_pk)
            logger.info(f"[{username}] Berhasil like postingan (pk={media_pk})")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"[{username}] Gagal like (pk={media_pk}): {e}")

        # Komentar jika belum melewati batas
        current_count = comment_counts.get(username, 0)
        if current_count < max_comments and comments:
            comment_text = random.choice(comments)
            try:
                cl.media_comment(media_pk, comment_text)
                comment_counts[username] = current_count + 1
                logger.info(f"[{username}] Berhasil komentar: '{comment_text}' ({comment_counts[username]}/{max_comments})")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[{username}] Gagal komentar (pk={media_pk}): {e}")
        else:
            logger.info(f"[{username}] Skip komentar (limit tercapai atau tidak ada komentar)")

    except ChallengeRequired:
        raise RuntimeError(f"[{username}] Diperlukan verifikasi challenge")
    except Exception as e:
        logger.error(f"[{username}] Error saat memproses postingan: {e}")
        # bubble up agar caller bisa mengambil tindakan (mis. hapus client)
        raise

# ================================
# Streamlit UI
# ================================
def main():
    st.title("🤖 Sistem Bot Instagram (Stealth)")

    st.markdown("""
    **Disclaimer:**  
    - Gunakan dengan bijak. Patuhi Terms of Service Instagram.  
    - Risiko ditanggung pengguna.
    """)

    with st.sidebar:
        st.header("⚙️ Konfigurasi")

        st.subheader("Akun Instagram (format: username,password[,2fa_code])")
        accounts_input = st.text_area(
            "Masukkan akun (satu per baris)",
            value="jmoriarty50,Drake1243",
            height=150,
            help="Untuk 2FA tambahkan kode setelah password, dipisah koma"
        )

        st.subheader("Target")
        target_post = st.text_input(
            "URL Postingan Target",
            value="https://www.instagram.com/p/DPQP0tHEqSe/",
            help="URL lengkap postingan Instagram"
        )

        st.subheader("Komentar")
        comments_input = st.text_area(
            "Daftar Komentar (satu per baris)",
            value="Mantap Gibran",
            height=120
        )

        st.subheader("Pengaturan")
        max_comments_per_account = st.number_input(
            "Max Komentar per Akun",
            min_value=0, max_value=1000, value=1
        )

        iterations = st.number_input(
            "Jumlah Putaran",
            min_value=1, max_value=100, value=1
        )

        delay_between_accounts = st.slider("Delay Antar Akun (detik)", 1, 60, 5)
        delay_between_rounds = st.slider("Delay Antar Putaran (detik)", 1, 300, 30)

        proxy_url = st.text_input("Proxy (opsional)", value="")

        run_button = st.button("🚀 Jalankan Bot")
        stop_button = st.button("⏹️ Berhenti")

        if stop_button:
            st.session_state.stop_requested = True
            st.warning("Menghentikan proses...")

    # area log
    st.header("📋 Log Aktivitas")
    log_container = st.empty()

    def render_logs():
        logs = st.session_state.get('logs', [])
        display_text = "\n".join(logs[-200:])
        log_container.text_area("Logs", value=display_text, height=300, label_visibility="collapsed")

    # parsing akun
    def parse_accounts(text):
        accounts = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split(',', 2)]
            if len(parts) >= 2 and parts[0] and parts[1]:
                account = {
                    'username': parts[0],
                    'password': parts[1],
                    'twofa': parts[2] if len(parts) > 2 else None
                }
                accounts.append(account)
        return accounts

    comments = [c.strip() for c in comments_input.splitlines() if c.strip()]
    accounts = parse_accounts(accounts_input)

    # summary metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Jumlah Akun", len(accounts))
    with col2:
        st.metric("Jumlah Komentar", len(comments))
    with col3:
        st.metric("Target Komentar", f"{max_comments_per_account} per akun")

    render_logs()

    # main run
    if run_button:
        if not accounts:
            st.error("❌ Tidak ada akun yang valid")
            return
        if not comments:
            st.error("❌ Tidak ada komentar yang ditentukan")
            return
        if not target_post.strip():
            st.error("❌ Masukkan URL postingan target")
            return

        st.session_state.stop_requested = False

        # login semua akun
        clients = {}
        successful_logins = 0
        progress_bar = st.progress(0)

        for i, acc in enumerate(accounts):
            if st.session_state.get('stop_requested'):
                break
            username = acc['username']; password = acc['password']; twofa = acc.get('twofa')
            try:
                logger.info(f"Login akun: {username}")
                client = login_client_for_account(username, password, twofa, proxy_url)
                clients[username] = client
                successful_logins += 1
                logger.info(f"✅ Login berhasil: {username}")
            except Exception as e:
                logger.error(f"❌ Login gagal: {username} - {e}")
            progress_bar.progress((i + 1) / len(accounts))
            time.sleep(1)
            render_logs()

        if successful_logins == 0:
            st.error("❌ Tidak ada akun yang berhasil login")
            return

        st.success(f"✅ {successful_logins}/{len(accounts)} akun berhasil login")
        render_logs()

        # eksekusi buzzer dengan iterasi dan delay
        comment_counts = {acc['username']: 0 for acc in accounts}
        for round_num in range(int(iterations)):
            if st.session_state.get('stop_requested'):
                break
            st.info(f"🔄 Putaran {round_num + 1}/{int(iterations)}")
            round_progress = st.progress(0)

            active_accounts = list(clients.keys())
            for i, username in enumerate(active_accounts):
                if st.session_state.get('stop_requested'):
                    break
                cl = clients.get(username)
                if cl is None:
                    continue
                try:
                    run_buzzer_for_account(
                        cl,
                        username,
                        target_post,
                        comments,
                        comment_counts,
                        int(max_comments_per_account)
                    )
                    # simpan settings setelah aksi agar session tetap up-to-date
                    try:
                        cl.dump_settings(f"session_{username}.json")
                    except Exception as e:
                        logger.warning(f"[{username}] Gagal dump_settings: {e}")
                except RuntimeError as err:
                    logger.error(f"Error pada akun {username}: {err}")
                    # hapus client yang butuh verifikasi manual
                    try:
                        del clients[username]
                    except Exception:
                        pass
                except Exception as e:
                    # log & lanjut
                    logger.error(f"[{username}] Error tak terduga: {e}")
                    logger.debug(traceback.format_exc())

                round_progress.progress((i + 1) / max(1, len(active_accounts)))

                # delay antar akun
                if i < len(active_accounts) - 1:
                    for remaining in range(delay_between_accounts, 0, -1):
                        if st.session_state.get('stop_requested'):
                            break
                        time.sleep(1)
                    render_logs()

            # delay antar putaran
            if round_num < int(iterations) - 1 and not st.session_state.get('stop_requested'):
                logger.info(f"⏳ Menunggu {delay_between_rounds} detik sebelum putaran berikutnya...")
                for remaining in range(delay_between_rounds, 0, -1):
                    if st.session_state.get('stop_requested'):
                        break
                    time.sleep(1)
                render_logs()

            if not clients:
                st.error("Semua akun butuh verifikasi. Proses dihentikan.")
                break

        # summary
        st.success("✅ Proses selesai!")
        st.subheader("📊 Summary")
        for username, count in comment_counts.items():
            st.write(f"- {username}: {count} komentar")
        st.metric("Total Komentar Dikirim", sum(comment_counts.values()))
        render_logs()

if __name__ == "__main__":
    main()
