# streamlit_instabot_ref_streamlit.py
# Requirements:
# pip install -U instagrapi streamlit

import os
import time
import random
import logging
import traceback
import streamlit as st
from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, ClientError

# ================================
# KONFIGURASI LOGGING (file + console + streamlit)
# ================================
LOG_FILENAME = "bot_stealth.log"

logger = logging.getLogger("instagrapi_logger")
logger.setLevel(logging.DEBUG)

# Hanya tambahkan handler jika belum ada (menghindari duplikat saat hot-reload)
if not logger.handlers:
    # file handler
    file_handler = logging.FileHandler(LOG_FILENAME, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)

    # console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Streamlit log handler (ke session_state)
class StreamlitLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        if "logs" not in st.session_state:
            st.session_state["logs"] = []

    def emit(self, record):
        msg = self.format(record)
        st.session_state["logs"].append(msg)
        # batasi ukuran
        if len(st.session_state["logs"]) > 2000:
            st.session_state["logs"] = st.session_state["logs"][-2000:]

# Tambah streamlit handler ke logger (hanya jika belum)
if not any(isinstance(h, StreamlitLogHandler) for h in logger.handlers):
    sh = StreamlitLogHandler()
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

# ================================
# DEFAULT KONFIGURASI (bisa diubah di sidebar)
# ================================
DEFAULT_ACCOUNTS = "jmoriarty50,Drake1243"
DEFAULT_TARGET = "https://www.instagram.com/p/DPQP0tHEqSe/"
DEFAULT_COMMENTS = "Mantap Gibran"

# Fallback / retry config
FALLBACK_RETRIES = 3
FALLBACK_BACKOFF = 2  # multiplier

# ================================
# UTILITY & CORE LOGIC (berasal dari skrip referensi + perbaikan)
# ================================

def parse_accounts_input(text):
    """
    Format per baris: username,password[,2fa_code]
    """
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

    # coba load settings
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            # coba login untuk menyegarkan token (beberapa versi butuh login)
            try:
                cl.login(username, password)
            except Exception:
                # kadang settings sudah cukup; lanjutkan
                logger.debug(f"[{username}] load_settings ok, login refresh gagal tapi lanjut.")
            logger.info(f"[{username}] Session loaded dari {session_file}.")
            return cl
        except Exception:
            try:
                os.remove(session_file)
            except Exception:
                pass

    # login normal
    try:
        if twofa:
            cl.login(username, password, verification_code=twofa)
        else:
            cl.login(username, password)
    except TwoFactorRequired:
        # di Streamlit kita tidak bisa input() aman; lempar error agar UI menampilkan pesan
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

    # simpan session
    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Login sukses, session disimpan.")
    except Exception as e:
        logger.warning(f"[{username}] Gagal menyimpan session: {e}")

    return cl

def _fallback_private_comment(cl: Client, media_pk: int, comment_text: str) -> bool:
    """
    Fallback: gunakan private_request ke endpoint media/{pk}/comment/
    """
    for attempt in range(1, FALLBACK_RETRIES + 1):
        try:
            endpoint = f"media/{media_pk}/comment/"
            data = {"comment_text": comment_text}
            logger.debug(f"Fallback attempt {attempt} -> {endpoint}")
            resp = cl.private_request(endpoint, data=data)
            # jika respons dict dan status ok -> sukses
            if isinstance(resp, dict) and (resp.get("status") == "ok" or "comment_id" in resp or "comment" in resp):
                return True
            # toleransi: jika tidak error, anggap sukses
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
    """
    Versi Streamlit dari run_buzzer yang menghindari media_info() dan menambahkan fallback komentar.
    delays = dict with keys: after_like, after_comment
    """
    logger.info(f"[{username}] Memproses postingan: {target_url}")
    try:
        pk = cl.media_pk_from_url(target_url)
    except Exception as e:
        logger.error(f"[{username}] Gagal konversi URL ke media_pk: {e}")
        return

    # Like (safe)
    try:
        cl.media_like(pk)
        logger.info(f"[{username}] Liked media PK {pk}")
        time.sleep(delays.get("after_like", 5))
    except Exception as e:
        logger.warning(f"[{username}] Gagal like: {e}")
        if isinstance(e, ChallengeRequired) or "Challenge" in str(e):
            raise RuntimeError(f"[{username}] Verifikasi IG diperlukan.")
        # lanjut (like gagal tidak mencegah fallback komentar)

    # Komentar bila belum mencapai limit
    current = comment_counts.get(username, 0)
    if current >= max_comments:
        logger.info(f"[{username}] Skip komentar: limit tercapai ({current}/{max_comments})")
        return

    komentar = random.choice(comments) if comments else ""
    if not komentar:
        logger.info(f"[{username}] Tidak ada komentar tersedia.")
        return

    # Coba media_comment langsung (beberapa versi menerima PK)
    try:
        cl.media_comment(pk, komentar)
        comment_counts[username] = current + 1
        logger.info(f"[{username}] Berhasil komentar: '{komentar}' ({comment_counts[username]}/{max_comments})")
        time.sleep(delays.get("after_comment", 5))
        return
    except Exception as e:
        logger.warning(f"[{username}] media_comment gagal: {e}")
        # jika challenge -> lempar
        if isinstance(e, ChallengeRequired) or "Challenge" in str(e):
            raise RuntimeError(f"[{username}] Verifikasi IG diperlukan.")
        # deteksi rate-limit/action-block
        if "Please wait" in str(e) or "action_blocked" in str(e).lower() or "429" in str(e):
            logger.error(f"[{username}] Terdeteksi rate-limit/action-blocked: {e}")
            return

    # Fallback: private_request
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
# STREAMLIT UI
# ================================
def main():
    st.set_page_config(page_title="Sistem Bot Instagram (Streamlit + Fix)", layout="wide")
    st.title("🤖 Sistem Bot Instagram — Streamlit (menggunakan referensi skrip Anda)")

    with st.sidebar:
        st.header("⚙️ Konfigurasi Utama (dari referensi)")
        accounts_input = st.text_area("Masukkan akun (username,password[,2fa]) — satu per baris",
                                      value=DEFAULT_ACCOUNTS, height=140)
        proxy_input = st.text_input("Proxy (opsional, format http://user:pass@host:port)", value="")
        target_post = st.text_input("URL Postingan Target", value=DEFAULT_TARGET)
        comments_input = st.text_area("Daftar Komentar (satu per baris)", value=DEFAULT_COMMENTS, height=120)
        max_comments = st.number_input("Max Komentar per Akun", min_value=0, max_value=1000, value=300)
        iterations = st.number_input("Jumlah Putaran (per akun)", min_value=1, max_value=10000, value=100)
        delay_after_like = st.number_input("Delay setelah like (detik)", min_value=0, value=5)
        delay_after_comment = st.number_input("Delay setelah comment (detik)", min_value=0, value=5)
        delay_between_accounts = st.number_input("Delay antar akun (detik)", min_value=0, value=5)
        delay_between_rounds = st.number_input("Delay antar putaran (detik)", min_value=0, value=5)

        start_button = st.button("🚀 Jalankan Bot")
        stop_button = st.button("⏹️ Berhenti")

        if stop_button:
            st.session_state["stop_requested"] = True
            st.warning("Menghentikan proses...")

    # area logs
    st.header("📋 Log Aktivitas")
    log_box = st.empty()

    def render_logs():
        logs = st.session_state.get("logs", [])
        # tampilkan 200 baris terakhir
        to_display = "\n".join(logs[-200:])
        log_box.text_area("Logs", value=to_display, height=360, label_visibility="collapsed")

    # parsing
    accounts = parse_accounts_input(accounts_input)
    comments = [c.strip() for c in comments_input.splitlines() if c.strip()]

    # metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("Jumlah Akun", len(accounts))
    col2.metric("Jumlah Komentar Konfigurasi", len(comments))
    col3.metric("Max Komentar/Akun", max_comments)

    # state init
    if "logs" not in st.session_state:
        st.session_state["logs"] = []
    if "stop_requested" not in st.session_state:
        st.session_state["stop_requested"] = False

    # run
    if start_button:
        st.session_state["stop_requested"] = False

        if not accounts:
            st.error("❌ Tidak ada akun valid")
            return
        if not comments:
            st.error("❌ Tidak ada komentar yang ditentukan")
            return
        if not target_post.strip():
            st.error("❌ Masukkan URL postingan target")
            return

        # login semua akun (synchronous)
        clients = {}
        successful_logins = 0
        progress = st.progress(0)
        total = len(accounts)
        for i, acc in enumerate(accounts):
            if st.session_state.get("stop_requested"):
                break

            username = acc["username"]
            password = acc["password"]
            twofa = acc.get("twofa")
            try:
                logger.info(f"Login akun: {username}")
                client = login_client_for_account(username, password, twofa, proxy_input or None)
                clients[username] = client
                successful_logins += 1
                logger.info(f"✅ Login berhasil: {username}")
            except Exception as e:
                logger.error(f"❌ Login gagal: {username} - {e}")
            progress.progress((i + 1) / total)
            time.sleep(1)
            render_logs()

        if successful_logins == 0:
            st.error("❌ Tidak ada akun yang berhasil login")
            return

        st.success(f"✅ {successful_logins}/{len(accounts)} akun berhasil login")
        # init comment counts
        comment_counts = {acc["username"]: 0 for acc in accounts}
        delays = {"after_like": delay_after_like, "after_comment": delay_after_comment}
        clients_active = dict(clients)  # salinan

        # run main loop (synchronous; stop via stop_button)
        try:
            for round_idx in range(iterations):
                if st.session_state.get("stop_requested"):
                    break
                st.info(f"🔄 Putaran {round_idx + 1}/{iterations}")
                for j, (username, cl) in enumerate(list(clients_active.items())):
                    if st.session_state.get("stop_requested"):
                        break
                    try:
                        run_buzzer_for_account(cl, username, target_post, comments, comment_counts, max_comments, delays)
                        # save session periodically
                        try:
                            cl.dump_settings(f"session_{username}.json")
                        except Exception:
                            pass
                    except RuntimeError as err:
                        logger.error(err)
                        # akun butuh verifikasi -> keluarkan
                        try:
                            del clients_active[username]
                        except Exception:
                            pass
                        break
                    except Exception as e:
                        logger.error(f"[{username}] Error tak terduga: {e}\n{traceback.format_exc()}")
                        # lanjut ke akun berikutnya
                        continue

                    # delay antar akun
                    if j < len(clients_active) - 1:
                        logger.info(f"[{username}] Menunggu {delay_between_accounts} detik sebelum akun berikutnya...")
                        for _ in range(delay_between_accounts):
                            if st.session_state.get("stop_requested"):
                                break
                            time.sleep(1)
                    render_logs()

                # cek semua akun capai limit?
                if all(count >= max_comments for count in comment_counts.values()):
                    logger.info("Semua akun telah mencapai limit komentar. Proses dihentikan.")
                    break

                # delay antar putaran
                logger.info(f"Selesai satu putaran. Menunggu {delay_between_rounds} detik...")
                for _ in range(delay_between_rounds):
                    if st.session_state.get("stop_requested"):
                        break
                    time.sleep(1)
                render_logs()

                if not clients_active:
                    logger.error("Tidak ada akun aktif tersisa (mungkin verifikasi diperlukan). Proses dihentikan.")
                    break

            st.success("✅ Proses selesai!")
            # summary
            st.subheader("📊 Summary")
            total_comments = 0
            for username in comment_counts:
                st.write(f"- {username}: {comment_counts[username]} komentar")
                total_comments += comment_counts[username]
            st.metric("Total Komentar Dikirim", total_comments)
            st.metric("Akun Aktif Setelah Run", f"{len(clients_active)}/{len(accounts)}")

        except KeyboardInterrupt:
            logger.info("Proses dihentikan oleh user (KeyboardInterrupt).")
        finally:
            render_logs()

    # selalu render logs saat UI idle
    render_logs()

if __name__ == "__main__":
    main()
