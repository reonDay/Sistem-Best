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
import base64
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
# Fungsi Session Management
# ================================

def ensure_session_dir(path: str = "sessions"):
    """Membuat direktori session jika belum ada"""
    os.makedirs(path, exist_ok=True)
    return path

def create_or_load_session(username: str, password: str, session_dir: str = "sessions", proxy: str = None):
    """
    Buat atau muat session file untuk username yang diberikan.
    Mengembalikan tuple (client, session_file_path)
    """
    ensure_session_dir(session_dir)
    session_file = os.path.join(session_dir, f"session_{username}.json")
    cl = Client()
    
    if proxy:
        cl.set_proxy(proxy)
        logger.info(f"[{username}] Menggunakan proxy: {proxy}")

    # Jika file session ada, coba load
    if os.path.exists(session_file):
        try:
            logger.info(f"[{username}] Mendeteksi session file: {session_file}. Mencoba load...")
            cl.load_settings(session_file)
            cl.login(username, password)  # refresh/login agar session valid
            logger.info(f"[{username}] Session berhasil dimuat dan login diperbaharui.")
            # pastikan men-dump lagi agar setting tetap up-to-date
            cl.dump_settings(session_file)
            return cl, session_file
        except (TwoFactorRequired, ChallengeRequired, ClientError) as e:
            logger.warning(f"[{username}] Error saat load session: {e}. Menghapus file session dan mencoba login fresh.")
            try:
                os.remove(session_file)
            except Exception:
                pass
            # lanjut ke blok login fresh
        except Exception as e:
            logger.warning(f"[{username}] Error tak terduga saat load session: {e}. Mencoba login fresh.")

    # Jika tidak ada session atau gagal load -> login fresh
    try:
        logger.info(f"[{username}] Melakukan login fresh...")
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
    cl.dump_settings(session_file)
    logger.info(f"[{username}] Login sukses, session disimpan ke {session_file}")
    return cl, session_file

def upload_session_to_github(session_file_path: str, github_token: str, repo_name: str, github_username: str = None):
    """
    Upload session file ke GitHub repository
    """
    try:
        with open(session_file_path, 'r', encoding='utf-8') as f:
            session_content = f.read()
        
        # Encode content to base64
        session_content_b64 = base64.b64encode(session_content.encode('utf-8')).decode('utf-8')
        
        # Extract filename
        filename = os.path.basename(session_file_path)
        
        # GitHub API URL
        if github_username:
            url = f"https://api.github.com/repos/{github_username}/{repo_name}/contents/sessions/{filename}"
        else:
            # Assume repo_name is full repository path
            url = f"https://api.github.com/repos/{repo_name}/contents/sessions/{filename}"
        
        # Check if file already exists
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # Get SHA if file exists
        sha = None
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            sha = response.json().get('sha')
        
        # Prepare data for upload
        data = {
            "message": f"Update session {filename} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "content": session_content_b64,
            "branch": "main"
        }
        
        if sha:
            data["sha"] = sha
        
        # Upload file
        response = requests.put(url, headers=headers, json=data)
        
        if response.status_code in [200, 201]:
            logger.info(f"✅ Session {filename} berhasil diupload ke GitHub")
            return True
        else:
            logger.error(f"❌ Gagal upload session ke GitHub: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error saat upload session ke GitHub: {e}")
        return False

def download_session_from_github(filename: str, github_token: str, repo_name: str, github_username: str = None, session_dir: str = "sessions"):
    """
    Download session file dari GitHub repository
    """
    try:
        ensure_session_dir(session_dir)
        
        # GitHub API URL
        if github_username:
            url = f"https://api.github.com/repos/{github_username}/{repo_name}/contents/sessions/{filename}"
        else:
            url = f"https://api.github.com/repos/{repo_name}/contents/sessions/{filename}"
        
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            content_b64 = response.json().get('content', '')
            content = base64.b64decode(content_b64).decode('utf-8')
            
            session_file_path = os.path.join(session_dir, filename)
            with open(session_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"✅ Session {filename} berhasil didownload dari GitHub")
            return True
        else:
            logger.error(f"❌ Gagal download session dari GitHub: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error saat download session dari GitHub: {e}")
        return False

def list_github_sessions(github_token: str, repo_name: str, github_username: str = None):
    """
    List semua session files di GitHub repository
    """
    try:
        if github_username:
            url = f"https://api.github.com/repos/{github_username}/{repo_name}/contents/sessions"
        else:
            url = f"https://api.github.com/repos/{repo_name}/contents/sessions"
        
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            files = response.json()
            session_files = [f['name'] for f in files if f['name'].startswith('session_') and f['name'].endswith('.json')]
            return session_files
        else:
            logger.error(f"❌ Gagal list session dari GitHub: {response.status_code}")
            return []
            
    except Exception as e:
        logger.error(f"❌ Error saat list session dari GitHub: {e}")
        return []

# ================================
# Fungsi utama bot
# ================================

def login_client_for_account(username: str, password: str, two_factor_code: str = None, proxy_url: str = None, session_dir: str = "sessions"):
    """
    Fungsi login yang menggunakan sistem session management baru
    """
    try:
        cl, session_file = create_or_load_session(
            username=username,
            password=password,
            session_dir=session_dir,
            proxy=proxy_url
        )
        return cl
    except RuntimeError as e:
        # Jika butuh 2FA, coba dengan kode yang diberikan
        if "2FA" in str(e) and two_factor_code:
            try:
                cl = Client()
                if proxy_url:
                    cl.set_proxy(proxy_url)
                cl.login(username, password, verification_code=two_factor_code)
                session_file = os.path.join(session_dir, f"session_{username}.json")
                cl.dump_settings(session_file)
                logger.info(f"[{username}] Login dengan 2FA berhasil, session disimpan")
                return cl
            except Exception as e2:
                logger.error(f"[{username}] Gagal login dengan 2FA: {e2}")
                raise RuntimeError(f"[{username}] Login dengan 2FA gagal: {e2}")
        else:
            raise e

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
    st.title("🤖 Sistem Bot Instagram dengan Session Management")
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
        
        session_dir = st.text_input(
            "Direktori Session",
            value="sessions",
            help="Direktori untuk menyimpan file session"
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
    
    # Tab untuk Session Management dan GitHub
    tab1, tab2 = st.tabs(["🤖 Bot Instagram", "📁 Session Management & GitHub"])
    
    with tab1:
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
                    client = login_client_for_account(username, password, twofa, proxy_url, session_dir)
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
    
    with tab2:
        st.header("📁 Session Management & GitHub Integration")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Local Session Management")
            
            # Tampilkan session files yang ada
            ensure_session_dir(session_dir)
            session_files = [f for f in os.listdir(session_dir) if f.startswith('session_') and f.endswith('.json')]
            
            if session_files:
                st.write("**Session files yang tersedia:**")
                for session_file in session_files:
                    file_path = os.path.join(session_dir, session_file)
                    file_size = os.path.getsize(file_path)
                    col_file1, col_file2, col_file3 = st.columns([3, 1, 1])
                    
                    with col_file1:
                        st.write(f"`{session_file}` ({file_size} bytes)")
                    
                    with col_file2:
                        if st.button(f"Download", key=f"dl_{session_file}"):
                            with open(file_path, 'rb') as f:
                                st.download_button(
                                    label="Download File",
                                    data=f,
                                    file_name=session_file,
                                    mime="application/json",
                                    key=f"dd_{session_file}"
                                )
                    
                    with col_file3:
                        if st.button(f"Hapus", key=f"del_{session_file}"):
                            try:
                                os.remove(file_path)
                                st.success(f"Session {session_file} dihapus")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Gagal menghapus: {e}")
            else:
                st.info("Tidak ada session files yang ditemukan")
        
        with col2:
            st.subheader("GitHub Integration")
            
            github_token = st.text_input(
                "GitHub Token",
                type="password",
                help="Personal access token untuk GitHub API"
            )
            
            repo_name = st.text_input(
                "Repository Name",
                placeholder="username/repo-name atau repo-name",
                help="Nama repository GitHub (format: username/repo atau repo-name)"
            )
            
            if github_token and repo_name:
                st.info("🔗 Terhubung ke GitHub")
                
                # Upload session ke GitHub
                if session_files:
                    session_to_upload = st.selectbox("Pilih session untuk diupload", session_files)
                    
                    if st.button("📤 Upload Session ke GitHub"):
                        with st.spinner("Mengupload session ke GitHub..."):
                            success = upload_session_to_github(
                                session_file_path=os.path.join(session_dir, session_to_upload),
                                github_token=github_token,
                                repo_name=repo_name
                            )
                            if success:
                                st.success("✅ Session berhasil diupload ke GitHub")
                            else:
                                st.error("❌ Gagal upload session ke GitHub")
                
                # Download session dari GitHub
                st.subheader("Download dari GitHub")
                
                if st.button("🔄 Refresh Session List dari GitHub"):
                    with st.spinner("Mengambil daftar session dari GitHub..."):
                        github_sessions = list_github_sessions(
                            github_token=github_token,
                            repo_name=repo_name
                        )
                        
                        if github_sessions:
                            st.write("**Session files di GitHub:**")
                            for gh_session in github_sessions:
                                col_gh1, col_gh2 = st.columns([3, 1])
                                
                                with col_gh1:
                                    st.write(f"`{gh_session}`")
                                
                                with col_gh2:
                                    if st.button(f"Download", key=f"gh_dl_{gh_session}"):
                                        with st.spinner(f"Mendownload {gh_session}..."):
                                            success = download_session_from_github(
                                                filename=gh_session,
                                                github_token=github_token,
                                                repo_name=repo_name,
                                                session_dir=session_dir
                                            )
                                            if success:
                                                st.success(f"✅ {gh_session} berhasil didownload")
                                                st.rerun()
                                            else:
                                                st.error(f"❌ Gagal download {gh_session}")
                        else:
                            st.info("Tidak ada session files di GitHub repository")
                
                # Manual download
                manual_session = st.text_input(
                    "Nama session file (manual download)",
                    placeholder="session_username.json",
                    help="Masukkan nama file session yang ingin didownload"
                )
                
                if manual_session and st.button("📥 Download Session Manual"):
                    with st.spinner(f"Mendownload {manual_session}..."):
                        success = download_session_from_github(
                            filename=manual_session,
                            github_token=github_token,
                            repo_name=repo_name,
                            session_dir=session_dir
                        )
                        if success:
                            st.success(f"✅ {manual_session} berhasil didownload")
                            st.rerun()
                        else:
                            st.error(f"❌ Gagal download {manual_session}")
            
            else:
                st.warning("Masukkan GitHub Token dan Repository Name untuk mengaktifkan fitur GitHub")

if __name__ == "__main__":
    main()
