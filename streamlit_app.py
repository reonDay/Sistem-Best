# streamlit_instabot.py
# Requirements:
# pip install instagrapi streamlit requests

import os
import time
import random
import logging
import io
import traceback
import json
import base64
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
# Fungsi untuk GitHub Operations
# ================================

class GitHubSessionManager:
    def __init__(self, token, repo_owner, repo_name):
        self.token = token
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.base_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
    
    def check_repo_access(self):
        """Cek akses ke repository"""
        try:
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}"
            response = requests.get(url, headers=self.headers)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error checking repo access: {e}")
            return False
    
    def file_exists(self, file_path):
        """Cek apakah file sudah ada di GitHub"""
        try:
            url = f"{self.base_url}/{file_path}"
            response = requests.get(url, headers=self.headers)
            return response.status_code == 200
        except:
            return False
    
    def get_file_sha(self, file_path):
        """Dapatkan SHA file yang sudah ada"""
        try:
            url = f"{self.base_url}/{file_path}"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.json().get("sha")
            return None
        except Exception as e:
            logger.error(f"Error getting file SHA: {e}")
            return None
    
    def upload_session_file(self, session_file_path, github_file_path, commit_message=None):
        """Upload session file ke GitHub"""
        try:
            if not os.path.exists(session_file_path):
                logger.error(f"Session file tidak ditemukan: {session_file_path}")
                return False
            
            with open(session_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Encode content ke base64
            content_bytes = content.encode('utf-8')
            content_base64 = base64.b64encode(content_bytes).decode('utf-8')
            
            # Cek apakah file sudah ada
            sha = self.get_file_sha(github_file_path)
            
            # Siapkan payload
            payload = {
                "message": commit_message or f"Auto-save session {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "content": content_base64
            }
            
            # Jika file sudah ada, tambahkan SHA untuk update
            if sha:
                payload["sha"] = sha
            
            url = f"{self.base_url}/{github_file_path}"
            response = requests.put(url, headers=self.headers, json=payload)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Session berhasil diupload ke GitHub: {github_file_path}")
                return True
            else:
                logger.error(f"❌ Gagal upload session: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error uploading session to GitHub: {e}")
            return False

# ================================
# Fungsi untuk Membuat Session
# ================================

def create_session_only(username: str, password: str, two_factor_code: str = None, proxy_url: str = None, github_manager: GitHubSessionManager = None):
    """
    Hanya membuat session tanpa menjalankan bot
    """
    session_file = f"session_{username}.json"
    cl = Client()
    
    # Set proxy jika ada
    if proxy_url and proxy_url.strip():
        try:
            cl.set_proxy(proxy_url.strip())
            logger.info(f"[{username}] Menggunakan proxy: {proxy_url}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")
    
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
    
    # Simpan session lokal
    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Session disimpan ke {session_file}")
        
        # Upload session ke GitHub
        if github_manager:
            github_session_path = f"sessions/session_{username}.json"
            success = github_manager.upload_session_file(
                session_file, 
                github_session_path,
                f"Auto-save session untuk {username}"
            )
            if success:
                # Hapus file session lokal setelah berhasil upload ke GitHub
                try:
                    os.remove(session_file)
                    logger.info(f"[{username}] Session lokal dihapus setelah berhasil upload ke GitHub")
                except:
                    pass
            return success
        return True
            
    except Exception as e:
        logger.warning(f"[{username}] Gagal menyimpan session: {e}")
        return False

# ================================
# Fungsi Bot (tetap sama)
# ================================

def login_client_for_account(username: str, password: str, two_factor_code: str = None, proxy_url: str = None, github_manager: GitHubSessionManager = None):
    session_file = f"session_{username}.json"
    cl = Client()
    
    # Set proxy jika ada
    if proxy_url and proxy_url.strip():
        try:
            cl.set_proxy(proxy_url.strip())
            logger.info(f"[{username}] Menggunakan proxy: {proxy_url}")
        except Exception as e:
            logger.warning(f"[{username}] Gagal set proxy: {e}")
    
    # Coba download session dari GitHub terlebih dahulu
    if github_manager:
        github_session_path = f"sessions/session_{username}.json"
        if github_manager.file_exists(github_session_path):
            try:
                if github_manager.download_session_file(github_session_path, session_file):
                    logger.info(f"[{username}] Session berhasil didownload dari GitHub")
            except Exception as e:
                logger.warning(f"[{username}] Gagal download session dari GitHub: {e}")
    
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
    
    # Simpan session lokal
    try:
        cl.dump_settings(session_file)
        logger.info(f"[{username}] Session disimpan ke {session_file}")
        
        # Upload session ke GitHub
        if github_manager:
            github_session_path = f"sessions/session_{username}.json"
            github_manager.upload_session_file(
                session_file, 
                github_session_path,
                f"Auto-save session baru untuk {username}"
            )
            
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
    st.title("🤖 Sistem Bot Instagram dengan Session Manager")
    
    # Buat tabs untuk memisahkan fungsi
    tab1, tab2 = st.tabs(["🔐 Session Manager", "🤖 Bot Runner"])
    
    with tab1:
        st.header("🔐 Session Manager - Buat & Simpan Session ke GitHub")
        st.markdown("""
        **Fitur:**
        - ✅ Buat session dari akun Instagram
        - ✅ Simpan session langsung ke GitHub
        - ✅ Tidak menjalankan bot, hanya membuat session
        
        **Cara penggunaan:**
        1. Isi konfigurasi GitHub
        2. Masukkan akun Instagram (format: username,password[,2fa_code])
        3. Klik "Buat Session & Upload ke GitHub"
        """)
        
        with st.expander("⚙️ Konfigurasi GitHub", expanded=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                github_token = st.text_input(
                    "GitHub Personal Access Token",
                    type="password",
                    key="session_github_token",
                    help="Dapatkan dari GitHub Settings > Developer settings > Personal access tokens"
                )
            with col2:
                github_repo_owner = st.text_input(
                    "Repo Owner",
                    value="your-username",
                    key="session_repo_owner",
                    help="Nama user/organization pemilik repo"
                )
            with col3:
                github_repo_name = st.text_input(
                    "Repo Name", 
                    value="instagram-sessions",
                    key="session_repo_name",
                    help="Nama repository untuk menyimpan session"
                )
            
            # Test GitHub connection
            if st.button("Test GitHub Connection", key="test_session_github"):
                if github_token and github_repo_owner and github_repo_name:
                    github_mgr = GitHubSessionManager(github_token, github_repo_owner, github_repo_name)
                    if github_mgr.check_repo_access():
                        st.success("✅ Koneksi GitHub berhasil!")
                    else:
                        st.error("❌ Gagal terhubung ke GitHub. Periksa token dan nama repo.")
                else:
                    st.warning("⚠️ Isi semua field GitHub terlebih dahulu")
        
        with st.expander("📱 Input Akun Instagram", expanded=True):
            session_accounts_input = st.text_area(
                "Masukkan akun (format: username,password[,2fa_code])",
                value="username1,password1\nusername2,password2,123456",
                height=200,
                key="session_accounts",
                help="Satu akun per baris. Untuk 2FA, tambahkan kode setelah password dipisahkan koma"
            )
            
            proxy_url_session = st.text_input(
                "Proxy (opsional)",
                value="",
                key="session_proxy",
                help="Format: http://user:pass@host:port"
            )
            
            create_session_button = st.button("🚀 Buat Session & Upload ke GitHub", type="primary", key="create_session_btn")
        
        # Parsing function untuk session manager
        def parse_accounts_for_session(text):
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
        
        # Area log untuk session manager
        st.subheader("📋 Log Session Manager")
        session_log_container = st.empty()
        
        def render_session_logs():
            logs = st.session_state.get('logs', [])
            display_text = "\n".join(logs[-30:])  # Tampilkan 30 log terakhir
            session_log_container.text_area("Session Logs", value=display_text, height=200, label_visibility="collapsed")
        
        # Jalankan proses pembuatan session
        if create_session_button:
            if not github_token or not github_repo_owner or not github_repo_name:
                st.error("❌ Harap isi konfigurasi GitHub terlebih dahulu")
                return
                
            session_accounts = parse_accounts_for_session(session_accounts_input)
            if not session_accounts:
                st.error("❌ Tidak ada akun yang valid")
                return
            
            # Inisialisasi GitHub Manager
            github_manager = GitHubSessionManager(github_token, github_repo_owner, github_repo_name)
            if not github_manager.check_repo_access():
                st.error("❌ Gagal terhubung ke GitHub. Periksa token dan nama repo.")
                return
            
            st.session_state.stop_requested = False
            
            # Proses pembuatan session
            successful_sessions = 0
            total_accounts = len(session_accounts)
            
            st.info(f"🔐 Memproses {total_accounts} akun...")
            progress_bar = st.progress(0)
            
            for i, acc in enumerate(session_accounts):
                if st.session_state.get('stop_requested'):
                    break
                    
                username = acc['username']
                password = acc['password']
                twofa = acc.get('twofa')
                
                try:
                    logger.info(f"🔄 Membuat session untuk: {username}")
                    success = create_session_only(username, password, twofa, proxy_url_session, github_manager)
                    if success:
                        successful_sessions += 1
                        logger.info(f"✅ Session berhasil dibuat dan diupload: {username}")
                    else:
                        logger.error(f"❌ Gagal membuat session: {username}")
                except Exception as e:
                    logger.error(f"❌ Error saat membuat session {username}: {e}")
                
                progress_bar.progress((i + 1) / total_accounts)
                time.sleep(3)  # Delay antar akun untuk menghindari rate limit
                render_session_logs()
            
            # Summary
            st.success(f"✅ Proses selesai! {successful_sessions}/{total_accounts} session berhasil dibuat")
            
            if successful_sessions > 0:
                st.balloons()
    
    with tab2:
        st.header("🤖 Bot Runner - Jalankan Bot Instagram")
        st.markdown("""
        **Fitur:** 
        - ✅ Jalankan bot like & komentar
        - ✅ Auto-load session dari GitHub
        - ✅ Multi-akun support
        
        **Disclaimer:** 
        - Gunakan dengan bijak dan bertanggung jawab
        - Patuhi Terms of Service Instagram
        - Risiko ditanggung pengguna
        """)
        
        with st.sidebar:
            st.header("⚙️ Konfigurasi Bot")
            
            st.subheader("🔐 GitHub Configuration")
            github_token_bot = st.text_input(
                "GitHub Personal Access Token",
                type="password",
                key="bot_github_token",
                help="Dapatkan dari GitHub Settings > Developer settings > Personal access tokens"
            )
            github_repo_owner_bot = st.text_input(
                "Repo Owner",
                value="your-username",
                key="bot_repo_owner",
                help="Nama user/organization pemilik repo"
            )
            github_repo_name_bot = st.text_input(
                "Repo Name", 
                value="instagram-sessions",
                key="bot_repo_name",
                help="Nama repository untuk menyimpan session"
            )
            
            # Test GitHub connection untuk bot
            if st.button("Test GitHub Connection", key="test_bot_github"):
                if github_token_bot and github_repo_owner_bot and github_repo_name_bot:
                    github_mgr = GitHubSessionManager(github_token_bot, github_repo_owner_bot, github_repo_name_bot)
                    if github_mgr.check_repo_access():
                        st.success("✅ Koneksi GitHub berhasil!")
                    else:
                        st.error("❌ Gagal terhubung ke GitHub. Periksa token dan nama repo.")
                else:
                    st.warning("⚠️ Isi semua field GitHub terlebih dahulu")
            
            st.subheader("📱 Akun Instagram")
            accounts_input = st.text_area(
                "Masukkan akun (format: username,password[,2fa_code])",
                value="jmoriarty50,Drake1243",
                height=150,
                help="Satu akun per baris. Untuk 2FA, tambahkan kode setelah password dipisahkan koma"
            )
            
            st.subheader("🎯 Target")
            target_post = st.text_input(
                "URL Postingan Target",
                value="https://www.instagram.com/p/DPOOZrfE8y6/",
                help="URL lengkap postingan Instagram"
            )
            
            st.subheader("💬 Komentar")
            comments_input = st.text_area(
                "Daftar Komentar",
                value="Keren!\nMantap!\nBagus sekali!",
                height=120,
                help="Satu komentar per baris"
            )
            
            st.subheader("⚡ Pengaturan")
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
        
        # Area log untuk bot
        st.subheader("📋 Log Bot Runner")
        bot_log_container = st.empty()
        
        def render_bot_logs():
            logs = st.session_state.get('logs', [])
            display_text = "\n".join(logs[-50:])  # Tampilkan 50 log terakhir
            bot_log_container.text_area("Bot Logs", value=display_text, height=300, label_visibility="collapsed")
        
        # Parsing input untuk bot
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
        
        # Tampilkan summary untuk bot
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Jumlah Akun", len(accounts))
        with col2:
            st.metric("Jumlah Komentar", len(comments))
        with col3:
            st.metric("Target Komentar", f"{max_comments_per_account} per akun")
        with col4:
            github_status = "✅ Aktif" if github_token_bot and github_repo_owner_bot and github_repo_name_bot else "❌ Nonaktif"
            st.metric("GitHub Sync", github_status)
        
        # Jalankan proses bot
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
            
            # Inisialisasi GitHub Manager untuk bot
            github_manager_bot = None
            if github_token_bot and github_repo_owner_bot and github_repo_name_bot:
                github_manager_bot = GitHubSessionManager(github_token_bot, github_repo_owner_bot, github_repo_name_bot)
                if not github_manager_bot.check_repo_access():
                    st.error("❌ Gagal terhubung ke GitHub. Proses tetap berjalan tanpa sync session.")
                    github_manager_bot = None
                else:
                    st.success("✅ GitHub sync aktif - Session akan di-load dari GitHub")
            
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
                    client = login_client_for_account(username, password, twofa, proxy_url, github_manager_bot)
                    clients[username] = client
                    successful_logins += 1
                    logger.info(f"✅ Login berhasil: {username}")
                except Exception as e:
                    logger.error(f"❌ Login gagal: {username} - {e}")
                
                progress_bar.progress((i + 1) / len(accounts))
                time.sleep(2)
                render_bot_logs()
            
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
                    
                    render_bot_logs()
                
                # Delay antar putaran
                if round_num < iterations - 1 and not st.session_state.get('stop_requested'):
                    logger.info(f"⏳ Menunggu {delay_between_rounds} detik sebelum putaran berikutnya...")
                    for remaining in range(delay_between_rounds, 0, -1):
                        if st.session_state.get('stop_requested'):
                            break
                        time.sleep(1)
                    render_bot_logs()
            
            # Summary
            st.success("✅ Proses selesai!")
            st.subheader("📊 Summary")
            for username, count in comment_counts.items():
                st.write(f"- {username}: {count} komentar")
            
            total_comments = sum(comment_counts.values())
            st.metric("Total Komentar Dikirim", total_comments)

if __name__ == "__main__":
    main()
