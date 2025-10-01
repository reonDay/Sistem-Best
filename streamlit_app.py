# streamlit_instabot.py
# Requirements:
# pip install instagrapi streamlit

import os
import time
import random
import logging
import io
import traceback
import streamlit as st
from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, ClientError

# -----------------------
# Konfigurasi untuk server gratis
# -----------------------
st.set_page_config(
    page_title="Sistem Bot Instagram",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Handler untuk logging di Streamlit
class StreamlitLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        if 'logs' not in st.session_state:
            st.session_state['logs'] = []
    
    def emit(self, record):
        msg = self.format(record)
        st.session_state['logs'].append(msg)
        # Batasi jumlah log yang disimpan
        if len(st.session_state['logs']) > 1000:
            st.session_state['logs'] = st.session_state['logs'][-1000:]

def create_logger():
    logger = logging.getLogger("instagrapi_streamlit")
    logger.setLevel(logging.DEBUG)
    
    # Hapus handler lama
    logger.handlers = []
    
    # Format untuk log
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    
    # Handler untuk Streamlit
    sh = StreamlitLogHandler()
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    
    # Handler untuk console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    
    return logger

logger = create_logger()

# ================================
# Fungsi utama
# ================================

def login_client_for_account(username: str, password: str, two_factor_code: str = None, proxy_url: str = None):
    session_file = f"session_{username}.json"
    cl = Client()
    
    # Set proxy jika ada
    if proxy_url and proxy_url.strip():
        try:
            cl.set_proxy(proxy_url.strip())
            logger.info(f"[{username}] Menggunakan proxy: {proxy_url}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")
    
    # Coba load session yang tersimpan
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            cl.login(username, password)
            logger.info(f"[{username}] Session berhasil dimuat dari {session_file}")
            return cl
        except Exception as e:
            logger.info(f"[{username}] Gagal memuat session, login ulang: {e}")
            try:
                os.remove(session_file)
            except:
                pass
    
    # Login normal
    try:
        if two_factor_code:
            cl.login(username, password, verification_code=two_factor_code)
        else:
            cl.login(username, password)
    except TwoFactorRequired:
        logger.error(f"[{username}] Diperlukan kode 2FA")
        raise RuntimeError(f"[{username}] Diperlukan kode verifikasi 2FA")
    except ChallengeRequired:
        logger.error(f"[{username}] Diperlukan verifikasi challenge")
        raise RuntimeError(f"[{username}] Diperlukan verifikasi challenge manual")
    except ClientError as e:
        logger.error(f"[{username}] Error client: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")
    except Exception as e:
        logger.error(f"[{username}] Error tak terduga: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")
    
    # Simpan session
    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Session disimpan ke {session_file}")
    except Exception as e:
        logger.warning(f"[{username}] Gagal menyimpan session: {e}")
    
    return cl

def run_buzzer_for_account(cl: Client, username: str, target_post_url: str, comments: list, comment_counts: dict, max_comments: int):
    try:
        # Dapatkan media PK dari URL
        media_pk = cl.media_pk_from_url(target_post_url)
        media_info = cl.media_info(media_pk)
        logger.info(f"[{username}] Memproses postingan: {media_info.code}")
        
        # Like postingan
        try:
            cl.media_like(media_pk)
            logger.info(f"[{username}] Berhasil like postingan")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"[{username}] Gagal like: {e}")
        
        # Komentar
        current_count = comment_counts.get(username, 0)
        if current_count < max_comments and comments:
            comment_text = random.choice(comments)
            try:
                cl.media_comment(media_pk, comment_text)
                comment_counts[username] = current_count + 1
                logger.info(f"[{username}] Berhasil komentar: '{comment_text}' ({comment_counts[username]}/{max_comments})")
                time.sleep(2)
            except Exception as e:
                logger.warning(f"[{username}] Gagal komentar: {e}")
        else:
            logger.info(f"[{username}] Skip komentar (limit tercapai atau tidak ada komentar)")
            
    except ChallengeRequired:
        raise RuntimeError(f"[{username}] Diperlukan verifikasi challenge")
    except Exception as e:
        logger.error(f"[{username}] Error saat memproses postingan: {e}")
        raise

# ================================
# UI Streamlit
# ================================

def main():
    st.title("🤖 Sistem Bot Instagram")
    st.markdown("""
    **Disclaimer:** 
    - Gunakan dengan bijak dan bertanggung jawab
    - Patuhi Terms of Service Instagram
    - Risiko ditanggung pengguna
    """)
    
    with st.sidebar:
        st.header("⚙️ Konfigurasi")
        
        st.subheader("Akun Instagram")
        accounts_input = st.text_area(
            "Masukkan akun (format: username,password[,2fa_code])",
            value="jmoriarty50,Drake1243",
            height=150,
            help="Satu akun per baris. Untuk 2FA, tambahkan kode setelah password dipisahkan koma"
        )
        
        st.subheader("Target")
        target_post = st.text_input(
            "URL Postingan Target",
            value="https://www.instagram.com/p/DPOOZrfE8y6/",
            help="URL lengkap postingan Instagram"
        )
        
        st.subheader("Komentar")
        comments_input = st.text_area(
            "Daftar Komentar",
            value="Keren!\nMantap!\nBagus sekali!",
            height=120,
            help="Satu komentar per baris"
        )
        
        st.subheader("Pengaturan")
        max_comments_per_account = st.number_input(
            "Max Komentar per Akun",
            min_value=0,
            max_value=50,
            value=1,
            help="Maksimal komentar yang dikirim per akun"
        )
        
        iterations = st.number_input(
            "Jumlah Putaran",
            min_value=1,
            max_value=100,
            value=1,
            help="Berapa kali proses diulang untuk semua akun"
        )
        
        delay_between_accounts = st.slider(
            "Delay Antar Akun (detik)",
            min_value=1,
            max_value=60,
            value=5
        )
        
        delay_between_rounds = st.slider(
            "Delay Antar Putaran (detik)", 
            min_value=1,
            max_value=300,
            value=30
        )
        
        proxy_url = st.text_input(
            "Proxy (opsional)",
            value="",
            help="Format: http://user:pass@host:port"
        )
        
        run_button = st.button("🚀 Jalankan Bot", type="primary")
        stop_button = st.button("⏹️ Berhenti")
        
        if stop_button:
            st.session_state.stop_requested = True
            st.warning("Menghentikan proses...")
    
    # Area log
    st.header("📋 Log Aktivitas")
    log_container = st.empty()
    
    def render_logs():
        logs = st.session_state.get('logs', [])
        display_text = "\n".join(logs[-50:])  # Tampilkan 50 log terakhir
        log_container.text_area("Logs", value=display_text, height=300, label_visibility="collapsed")
    
    # Parsing input
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
    
    # Tampilkan summary
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Jumlah Akun", len(accounts))
    with col2:
        st.metric("Jumlah Komentar", len(comments))
    with col3:
        st.metric("Target Komentar", f"{max_comments_per_account} per akun")
    
    # Jalankan proses
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
        
        # Login semua akun
        clients = {}
        successful_logins = 0
        
        st.info("🔐 Proses login...")
        progress_bar = st.progress(0)
        
        for i, acc in enumerate(accounts):
            if st.session_state.get('stop_requested'):
                break
                
            username = acc['username']
            password = acc['password']
            twofa = acc.get('twofa')
            
            try:
                logger.info(f"Login akun: {username}")
                client = login_client_for_account(username, password, twofa, proxy_url)
                clients[username] = client
                successful_logins += 1
                logger.info(f"✅ Login berhasil: {username}")
            except Exception as e:
                logger.error(f"❌ Login gagal: {username} - {e}")
            
            progress_bar.progress((i + 1) / len(accounts))
            time.sleep(2)
            render_logs()
        
        if successful_logins == 0:
            st.error("❌ Tidak ada akun yang berhasil login")
            return
        
        st.success(f"✅ {successful_logins}/{len(accounts)} akun berhasil login")
        
        # Eksekusi bot
        comment_counts = {acc['username']: 0 for acc in accounts}
        
        for round_num in range(iterations):
            if st.session_state.get('stop_requested'):
                break
                
            st.info(f"🔄 Putaran {round_num + 1}/{iterations}")
            round_progress = st.progress(0)
            
            active_accounts = list(clients.keys())
            for i, username in enumerate(active_accounts):
                if st.session_state.get('stop_requested'):
                    break
                    
                try:
                    run_buzzer_for_account(
                        clients[username], 
                        username, 
                        target_post, 
                        comments, 
                        comment_counts, 
                        max_comments_per_account
                    )
                except Exception as e:
                    logger.error(f"Error pada akun {username}: {e}")
                    # Hapus client yang error
                    try:
                        del clients[username]
                    except:
                        pass
                
                round_progress.progress((i + 1) / len(active_accounts))
                
                # Delay antar akun
                if i < len(active_accounts) - 1:  # Tidak delay untuk akun terakhir
                    time.sleep(delay_between_accounts)
                
                render_logs()
            
            # Delay antar putaran
            if round_num < iterations - 1 and not st.session_state.get('stop_requested'):
                logger.info(f"⏳ Menunggu {delay_between_rounds} detik sebelum putaran berikutnya...")
                for remaining in range(delay_between_rounds, 0, -1):
                    if st.session_state.get('stop_requested'):
                        break
                    time.sleep(1)
                render_logs()
        
        # Summary
        st.success("✅ Proses selesai!")
        st.subheader("📊 Summary")
        for username, count in comment_counts.items():
            st.write(f"- {username}: {count} komentar")
        
        total_comments = sum(comment_counts.values())
        st.metric("Total Komentar Dikirim", total_comments)

if __name__ == "__main__":
    main()
