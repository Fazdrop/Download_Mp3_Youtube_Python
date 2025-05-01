import logging
from flask import Flask, render_template, request, Response
import yt_dlp
import os
import json
import time
import threading
from urllib.parse import urlparse

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

# Direktori penyimpanan

output_directory = 'your_path_here'  # Ganti dengan path yang sesuai
if not os.path.exists(output_directory):
    os.makedirs(output_directory)

# Daftar untuk menyimpan pembaruan progres
progress_updates = []
download_completed = False  # Flag untuk menandai bahwa unduhan selesai

def is_valid_youtube_url(url):
    """
    Memeriksa apakah URL yang diberikan adalah URL valid dari YouTube.

    Args:
        url (str): URL yang akan divalidasi.

    Returns:
        bool: True jika URL valid untuk YouTube, False jika tidak.
    """
    try:
        parsed = urlparse(url)
        return parsed.netloc in ['www.youtube.com', 'youtube.com', 'youtu.be']
    except:
        return False

def progress_hook(d):
    """
    Hook callback yang dijalankan oleh yt_dlp saat proses download berlangsung.

    Args:
        d (dict): Dictionary berisi status dan data progres unduhan.
    """
    global download_completed
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '0%').replace('%', '').strip()
        speed = d.get('_speed_str', '0B/s')
        eta = d.get('_eta_str', 'N/A')
        progress = {
            'status': 'downloading',
            'percent': percent,
            'speed': speed,
            'eta': eta
        }
        logging.debug(f"Pembaruan progres: {progress}")
        progress_updates.append(progress)
    elif d['status'] == 'finished':
        logging.debug("Unduhan selesai, sedang memproses...")
        progress_updates.append({'status': 'processing'})
        download_completed = True
    elif d['status'] == 'error':
        logging.debug("Terjadi kesalahan unduhan")
        progress_updates.append({'status': 'error', 'message': 'Unduhan gagal'})

def download_youtube_audio(url, output_path=output_directory, ffmpeg_path='C:\\ffmpeg\\ffmpeg.exe'):
    """
    Mengunduh audio dari URL YouTube dan mengirim progres melalui SSE (Server-Sent Events).

    Args:
        url (str): URL video YouTube yang akan diunduh.
        output_path (str): Direktori tempat file audio akan disimpan.
        ffmpeg_path (str): Lokasi file executable ffmpeg.

    Returns:
        Response: Response streaming (SSE) yang mengirim pembaruan progres unduhan.
    """
    global download_completed
    progress_updates.clear()
    download_completed = False

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
        'ffmpeg_location': ffmpeg_path,
        'progress_hooks': [progress_hook],
        'noplaylist': True,
        'progress_delta': 0.1,
    }

    def download():
        """
        Fungsi generator untuk menjalankan proses unduhan dan mengirimkan progres secara real-time.
        """
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info.get('formats'):
                    yield f"data: {json.dumps({'status': 'error', 'message': 'Format audio tidak tersedia'})}\n\n"
                    return

                # Simulasi progres awal
                for i in range(0, 30, 5):
                    progress = {'status': 'downloading', 'percent': str(i), 'speed': 'N/A', 'eta': 'N/A'}
                    progress_updates.append(progress)
                    yield f"data: {json.dumps(progress)}\n\n"
                    time.sleep(0.2)

                def run_download():
                    """Menjalankan proses unduhan secara terpisah dalam thread."""
                    ydl.download([url])

                download_thread = threading.Thread(target=run_download)
                download_thread.start()

                current_percent = 30
                while not download_completed and current_percent < 90:
                    progress = {'status': 'downloading', 'percent': str(current_percent), 'speed': 'N/A', 'eta': 'N/A'}
                    progress_updates.append(progress)
                    yield f"data: {json.dumps(progress)}\n\n"
                    time.sleep(0.2)
                    current_percent += 5

                download_thread.join()

                for i in range(current_percent, 101, 5):
                    progress = {'status': 'downloading', 'percent': str(i), 'speed': 'N/A', 'eta': 'N/A'}
                    progress_updates.append(progress)
                    yield f"data: {json.dumps(progress)}\n\n"
                    time.sleep(0.2)

                yield f"data: {json.dumps({'status': 'processing'})}\n\n"
                time.sleep(0.5)
                yield f"data: {json.dumps({'status': 'completed'})}\n\n"
        except yt_dlp.utils.DownloadError as e:
            yield f"data: {json.dumps({'status': 'error', 'message': f'Gagal mengunduh: {str(e)}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': f'Kesalahan tak terduga: {str(e)}'})}\n\n"

    return Response(download(), content_type='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive'
    })

@app.route('/')
def index():
    """
    Menampilkan halaman utama (index.html) ke pengguna.
    
    Returns:
        str: Render dari template HTML.
    """
    return render_template('index.html')

@app.route('/download', methods=['GET', 'POST'])
def download():
    """
    Endpoint untuk memulai proses pengunduhan audio dari YouTube.

    Returns:
        Response: Progres unduhan atau pesan kesalahan jika URL tidak valid.
    """
    url = request.args.get('url') or request.form.get('url')
    if not url or not is_valid_youtube_url(url):
        return "URL tidak valid", 400
    return download_youtube_audio(url)

if __name__ == '__main__':
    # Menjalankan server Flask dengan debug dan multithread aktif.
    app.run(debug=True, threaded=True)
