import logging
from flask import Flask, render_template, request, Response, jsonify
import yt_dlp
import os
import json
import queue
import threading

# --- Konfigurasi Aplikasi ---
# Menggunakan folder 'templates' default, yang merupakan praktik terbaik di Flask.
app = Flask(__name__) 

# Konfigurasi logging yang lebih detail untuk memudahkan debugging.
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)

# --- Konfigurasi Path (Sesuai Permintaan Anda) ---
# CATATAN: Hardcoding path seperti ini cocok untuk penggunaan pribadi di komputer Anda.
# Untuk aplikasi yang akan didistribusikan atau di-deploy, disarankan menggunakan 
# variabel lingkungan (environment variables) atau file konfigurasi agar lebih fleksibel.
OUTPUT_DIRECTORY = 'C:\\Users\\muham\\Music\\Music\\'
FFMPEG_PATH = 'C:\\ffmpeg\\ffmpeg.exe'

# --- Validasi Path Saat Startup ---
# Memeriksa dan membuat direktori jika belum ada untuk mencegah error saat runtime.
if not os.path.exists(OUTPUT_DIRECTORY):
    try:
        os.makedirs(OUTPUT_DIRECTORY)
        logging.info(f"Direktori '{OUTPUT_DIRECTORY}' berhasil dibuat.")
    except OSError as e:
        logging.critical(f"KRITIS: Gagal membuat direktori output '{OUTPUT_DIRECTORY}': {e}. Aplikasi akan berhenti.")
        exit()

if not os.path.exists(FFMPEG_PATH):
    logging.warning(f"PERINGATAN: FFMPEG tidak ditemukan di '{FFMPEG_PATH}'. Proses konversi ke MP3 akan gagal.")


class DownloadManager:
    """
    Mengelola state untuk satu sesi unduhan secara terisolasi dan thread-safe.
    Setiap permintaan ke /download akan membuat instance baru dari kelas ini.
    """
    def __init__(self):
        self.progress_queue = queue.Queue()

    def progress_hook(self, d):
        """
        Hook untuk yt_dlp. Dipanggil pada setiap update progres.
        Fungsi ini berjalan di dalam thread yt_dlp, sehingga aman untuk memasukkan item ke queue.
        """
        try:
            if d['status'] == 'downloading':
                progress = {
                    'status': 'downloading',
                    'percent': d.get('_percent_str', '0%').replace('%', '').strip(),
                    'speed': d.get('_speed_str', 'N/A'),
                    'eta': d.get('_eta_str', 'N/A')
                }
                self.progress_queue.put(progress)
            
            elif d['status'] == 'finished':
                # Status 'finished' berarti file asli (misal .webm) sudah terunduh
                # dan post-processing (konversi ke mp3) akan dimulai atau telah selesai.
                self.progress_queue.put({'status': 'processing'})
                
                # Ekstrak nama file final dari info_dict
                info_dict = d.get('info_dict', {})
                final_filepath = info_dict.get('filepath')
                filename = "audio.mp3" # Nama file default jika tidak ditemukan
                
                if final_filepath:
                    # Ambil nama dasar file dan pastikan ekstensinya .mp3
                    base = os.path.basename(final_filepath)
                    filename = os.path.splitext(base)[0] + '.mp3'

                # Beri jeda singkat agar pesan 'processing' terlihat di UI sebelum mengirim 'completed'.
                # Ini meningkatkan pengalaman pengguna (UX).
                threading.Timer(1.5, lambda: self.progress_queue.put({
                    'status': 'completed',
                    'filename': filename
                })).start()
        except Exception as e:
            logging.error(f"Error di dalam progress_hook: {e}")


    def get_ydl_opts(self):
        """Mendapatkan opsi untuk yt_dlp dengan path yang sudah ditentukan."""
        return {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(OUTPUT_DIRECTORY, '%(title)s.%(ext)s'),
            'ffmpeg_location': FFMPEG_PATH,
            'progress_hooks': [self.progress_hook],
            'noplaylist': True, # Hanya unduh satu video, bukan seluruh playlist
            'quiet': True, # Mengurangi output log dari yt-dlp di console
        }

# --- Rute Aplikasi (Endpoints) ---

@app.route('/')
def index():
    """Menampilkan halaman utama dari file 'index.html' di dalam folder 'templates'."""
    return render_template('index.html')

@app.route('/get-info', methods=['POST'])
def get_info():
    """Endpoint untuk mengambil info video (thumbnail & judul) untuk fitur pratinjau."""
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'error': 'URL tidak disediakan'}), 400

    logging.info(f"Mengambil info untuk URL: {url}")
    try:
        # Opsi untuk hanya mengambil informasi tanpa mengunduh
        ydl_opts = {'noplaylist': True, 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'success': True,
                'title': info.get('title', 'Judul tidak ditemukan'),
                'thumbnail': info.get('thumbnail', '')
            })
    except Exception as e:
        logging.error(f"Gagal mengambil info untuk URL '{url}': {e}")
        return jsonify({'success': False, 'error': 'Gagal mengambil informasi video. URL mungkin tidak valid.'}), 500


@app.route('/download')
def download():
    """Endpoint untuk streaming progres unduhan menggunakan Server-Sent Events (SSE)."""
    url = request.args.get('url')
    if not url:
        return Response("data: " + json.dumps({'status': 'error', 'message': 'URL tidak disediakan di parameter.'}) + "\n\n",
                        content_type='text/event-stream')

    manager = DownloadManager()

    def download_thread_target():
        """Fungsi yang dijalankan di thread terpisah untuk proses unduhan agar tidak memblokir server."""
        logging.info(f"Memulai thread unduhan untuk URL: {url}")
        try:
            with yt_dlp.YoutubeDL(manager.get_ydl_opts()) as ydl:
                ydl.download([url])
        except Exception as e:
            logging.error(f"Kesalahan pada thread unduhan untuk URL '{url}': {e}")
            manager.progress_queue.put({'status': 'error', 'message': 'Proses unduhan gagal. Periksa URL dan coba lagi.'})

    def generate_events():
        """Generator yang mengambil progres dari queue dan mengirimkannya ke client."""
        thread = threading.Thread(target=download_thread_target, name=f"Downloader-{threading.active_count()}")
        thread.start()
        
        try:
            while True:
                progress = manager.progress_queue.get() # Akan menunggu sampai ada item baru
                yield f"data: {json.dumps(progress)}\n\n"
                
                if progress['status'] in ['completed', 'error']:
                    logging.info(f"Stream selesai untuk URL: {url} dengan status: {progress['status']}")
                    break
        finally:
            # Pastikan thread selalu di-join untuk membersihkan resource, bahkan jika client disconnect.
            thread.join()
            logging.info(f"Thread unduhan untuk URL '{url}' telah di-join.")

    return Response(generate_events(), content_type='text/event-stream')


if __name__ == '__main__':
    # Menjalankan server Flask dalam mode debug dan mengizinkan threading
    app.run(debug=True, threaded=True)
