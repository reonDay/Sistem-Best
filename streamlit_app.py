# streamlit_instabot.py
# Requirements:
# pip install instagrapi streamlit

import os
import time
import random
import logging
import io
import traceback
import json
import requests
from datetime import datetime
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
# Fungsi Session Helper (Baru)
# ================================

def ensure_session_dir(path: str):
    """Membuat direktori session jika belum ada"""
    os.makedirs(path, exist_ok=True)
    return path

def create_or_load_session(username: str, password: str, session_dir: str = "sessions", proxy: str = None, twofa_code: str = None):
    """
    Buat atau muat session file untuk username yang diberikan.
    Mengembalikan tuple (client, session_file_path)
    """
    ensure_session_dir(session_dir)
    session_file = os.path.join(session_dir, f"session_{username}.json")
    cl = Client()

    if proxy:
        cl.set_proxy(proxy)

    # Jika file session ada coba load dan login
    if os.path.exists(session_file):
        try:
            logger.info(f"[{username}] Mendeteksi session file: {session_file}. Mencoba load...")
            cl.load_settings(session_file)
            cl.login(username, password)
            logger.info(f"[{username}] Session berhasil dimuat dan login diperbaharui.")
            # Pastikan men-dump lagi agar setting tetap up-to-date
            cl.dump_settings(session_file)
            return cl, session_file
        except (TwoFactorRequired, ChallengeRequired, ClientError) as e:
            logger.warning(f"[{username}] Error saat load/login session: {e}. Menghapus file session dan mencoba login fresh.")
            try:
                os.remove(session_file)
            except Exception:
                pass
            # Lanjut ke blok login fresh
        except Exception as e:
            logger.warning(f"[{username}] Error tak terduga saat load session: {e}. Mencoba login fresh.")

    # Jika tidak ada session atau gagal load -> login dan dump
    try:
        logger.info(f"[{username}] Melakukan login fresh...")
        if twofa_code:
            cl.login(username, password, verification_code=twofa_code)
        else:
            cl.login(username, password)
    except TwoFactorRequired:
        logger.error(f"[{username}] Two factor authentication required but no code provided.")
        raise RuntimeError(f"[{username}] Diperlukan kode verifikasi 2FA")
    except ChallengeRequired:
        logger.error(f"[{username}] Challenge required — verifikasi manual diperlukan.")
        raise RuntimeError(f"[{username}] Diperlukan verifikasi challenge manual")
    except ClientError as e:
        logger.error(f"[{username}] ClientError saat login: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")
    except Exception as e:
        logger.error(f"[{username}] Error tak terduga saat login: {e}")
        raise RuntimeError(f"[{username}] Login gagal: {e}")

    # Simpan settings ke file session
    cl.dump_settings(session_file)
    logger.info(f"[{username}] Login sukses, session disimpan ke {session_file}")
    return cl, session_file

def save_session_to_github(session_file_path: str, github_token: str, repo: str, commit_message: str = None):
    """
    Menyimpan file session ke GitHub repository
    """
    try:
        # Baca konten file session
        with open(session_file_path, 'r', encoding='utf-8') as f:
            session_content = f.read()
        
        # Dapatkan nama file dari path
        filename = os.path.basename(session_file_path)
        
        # Persiapan API GitHub
        headers = {
            'Authorization': f'token {github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        
        # URL untuk create atau update file
        api_url = f"https://api.github.com/repos/{repo}/contents/sessions/{filename}"
        
        # Cek apakah file sudah ada
        response = requests.get(api_url, headers=headers)
        sha = None
        if response.status_code == 200:
            sha = response.json().get('sha')
        
        # Persiapkan data untuk upload
        content_encoded = session_content.encode('utf-8')
        import base64
        content_b64 = base64.b64encode(content_encoded).decode('utf-8')
        
        data = {
            "message": commit_message or f"Update session {filename} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "content": content_b64,
            "branch": "main"
        }
        
        if sha:
            data["sha"] = sha
        
        # Upload ke GitHub
        response = requests.put(api_url, headers=headers, json=data)
        
        if response.status_code in [200, 201]:
            logger.info(f"Session {filename} berhasil diupload ke GitHub")
            return True
        else:
            logger.error(f"Gagal upload session ke GitHub: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error saat menyimpan session ke GitHub: {e}")
        return False

def load_session_from_github(username: str, github_token: str, repo: str, session_dir: str = "sessions"):
    """
    Load session dari GitHub repository
    """
    try:
        filename = f"session_{username}.json"
        
        # Persiapan API GitHub
        headers = {
            'Authorization': f'token {github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        
        # URL untuk get file
        api_url = f"https://api.github.com/repos/{repo}/contents/sessions/{filename}"
        
        response = requests.get(api_url, headers=headers)
        
        if response.status_code == 200:
            file_data = response.json()
            import base64
            content_b64 = file_data['content']
            content = base64.b64decode(content_b64).decode('utf-8')
            
            # Simpan ke file lokal
            ensure_session_dir(session_dir)
            session_file_path = os.path.join(session_dir, filename)
            with open(session_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"Session {filename} berhasil di-download dari GitHub")
            return session_file_path
        else:
            logger.warning(f"Session {filename} tidak ditemukan di GitHub: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"Error saat load session dari GitHub: {e}")
        return None

# ================================
# Fungsi utama (Dimodifikasi)
# ================================

def login_client_for_account(username: str, password: str, two_factor_code: str = None, proxy_url: str = None, use_github: bool = False, github_token: str = None, github_repo: str = None):
    """
    Fungsi login yang dimodifikasi untuk mendukung GitHub session storage
    """
    # Coba load dari GitHub jika diaktifkan
    if use_github and github_token and github_repo:
        session_file = load_session_from_github(username, github_token, github_repo)
        if session_file and os.path.exists(session_file):
            try:
                cl = Client()
                if proxy_url:
                    cl.set_proxy(proxy_url)
                cl.load_settings(session_file)
                cl.login(username, password)
                logger.info(f"[{username}] Session berhasil dimuat dari GitHub")
                return cl
            except Exception as e:
                logger.info(f"[{username}] Gagal memuat session dari GitHub, login ulang: {e}")
    
    # Gunakan fungsi create_or_load_session yang baru
    try:
        cl, session_file = create_or_load_session(
            username=username,
            password=password,
            session_dir="sessions",
            proxy=proxy_url,
            twofa_code=two_factor_code
        )
        
        # Simpan ke GitHub jika diaktifkan
        if use_github and github_token and github_repo:
            save_session_to_github(
                session_file_path=session_file,
                github_token=github_token,
                repo=github_repo,
                commit_message=f"Auto session backup for {username} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        
        return cl
        
    except Exception as e:
        logger.error(f"[{username}] Login gagal: {e}")
        raise

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
# UI Streamlit (Dimodifikasi)
# ================================

def main():
    st.title("🤖 Sistem Bot Instagram dengan Session Backup GitHub")
    st.markdown("""
    **Fitur Baru:** 
    - ✅ Backup session otomatis ke GitHub
    - ✅ Load session dari GitHub
    - ✅ Sync session across devices
    
    **Disclaimer:** 
    - Gunakan dengan bijak dan bertanggung jawab
    - Patuhi Terms of Service Instagram
    - Risiko ditanggung pengguna
    - Jangan share GitHub token dan session files
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
        
        st.subheader("Session GitHub Backup")
        use_github = st.checkbox("Gunakan GitHub Session Backup", value=False, 
                               help="Backup session files ke GitHub repository")
        
        if use_github:
            github_token = st.text_input(
                "GitHub Personal Access Token",
                type="password",
                help="Dapatkan dari GitHub Settings > Developer Settings > Personal Access Tokens"
            )
            github_repo = st.text_input(
                "GitHub Repository (format: username/repo)",
                value="username/repository",
                help="Contoh: johndoe/instagram-sessions"
            )
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔧 Test GitHub Connection"):
                    if github_token and github_repo:
                        try:
                            headers = {'Authorization': f'token {github_token}'}
                            response = requests.get(f"https://api.github.com/repos/{github_repo}", headers=headers)
                            if response.status_code == 200:
                                st.success("✅ Koneksi GitHub berhasil!")
                            else:
                                st.error(f"❌ Gagal: {response.status_code} - {response.json().get('message', 'Unknown error')}")
                        except Exception as e:
                            st.error(f"❌ Error: {e}")
                    else:
                        st.warning("Masukkan token dan repository GitHub")
            
            with col2:
                if st.button("📥 Download All Sessions"):
                    if github_token and github_repo:
                        accounts = parse_accounts(accounts_input)
                        downloaded = 0
                        for acc in accounts:
                            username = acc['username']
                            if load_session_from_github(username, github_token, github_repo):
                                downloaded += 1
                        st.success(f"✅ {downloaded}/{len(accounts)} session berhasil di-download")
                    else:
                        st.warning("Masukkan token dan repository GitHub")
        else:
            github_token = None
            github_repo = None
        
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
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Jumlah Akun", len(accounts))
    with col2:
        st.metric("Jumlah Komentar", len(comments))
    with col3:
        st.metric("Target Komentar", f"{max_comments_per_account} per akun")
    with col4:
        session_status = "✅ GitHub" if use_github else "📱 Lokal"
        st.metric("Session Storage", session_status)
    
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
        
        if use_github and (not github_token or not github_repo):
            st.error("❌ Untuk GitHub Backup, masukkan token dan repository")
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
                client = login_client_for_account(
                    username=username, 
                    password=password, 
                    two_factor_code=twofa, 
                    proxy_url=proxy_url,
                    use_github=use_github,
                    github_token=github_token,
                    github_repo=github_repo
                )
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
        
        # Backup final sessions ke GitHub
        if use_github and github_token and github_repo:
            st.info("💾 Menyimpan session terakhir ke GitHub...")
            backup_success = 0
            for username in clients.keys():
                session_file = f"sessions/session_{username}.json"
                if os.path.exists(session_file):
                    if save_session_to_github(
                        session_file_path=session_file,
                        github_token=github_token,
                        repo=github_repo,
                        commit_message=f"Final session backup after bot run - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    ):
                        backup_success += 1
            st.success(f"✅ {backup_success} session berhasil di-backup ke GitHub")

if __name__ == "__main__":
    main()
