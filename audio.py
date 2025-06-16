import logging
from flask import Flask, render_template, request, Response, jsonify
import yt_dlp
import os
import json
import queue
import threading
from collections import defaultdict

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)

OUTPUT_DIRECTORY = 'C:\\Users\\muham\\Music\\Music\\'
FFMPEG_PATH = 'C:\\ffmpeg\\ffmpeg.exe'

if not os.path.exists(OUTPUT_DIRECTORY):
    try:
        os.makedirs(OUTPUT_DIRECTORY)
        logging.info(f"Direktori '{OUTPUT_DIRECTORY}' berhasil dibuat.")
    except OSError as e:
        logging.critical(f"KRITIS: Gagal membuat direktori output '{OUTPUT_DIRECTORY}': {e}. Aplikasi akan berhenti.")
        exit()

if not os.path.exists(FFMPEG_PATH):
    logging.warning(f"PERINGATAN: FFMPEG tidak ditemukan di '{FFMPEG_PATH}'. Proses konversi audio mungkin akan gagal.")

cancellation_events = defaultdict(threading.Event)

class DownloadManager:
    def __init__(self, download_id):
        self.progress_queue = queue.Queue()
        self.download_id = download_id
        self.cancel_event = cancellation_events[self.download_id]

    def progress_hook(self, d):
        if self.cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Unduhan dibatalkan oleh pengguna.")

        if d['status'] == 'downloading':
            self.progress_queue.put({
                'status': 'downloading',
                'percent': d.get('_percent_str', '0%').replace('%', '').strip(),
            })
        elif d['status'] == 'finished':
            self.progress_queue.put({'status': 'processing'})
            info_dict = d.get('info_dict', {})
            final_filepath = info_dict.get('filepath')
            filename = "audio.mp3" # Default
            if final_filepath:
                final_ext = info_dict.get('postprocessor_args', {}).get('preferredcodec', 'mp3')
                filename = f"{os.path.splitext(os.path.basename(final_filepath))[0]}.{final_ext}"
            threading.Timer(1.5, lambda: self.progress_queue.put({
                'status': 'completed', 'filename': filename
            })).start()

    def get_ydl_opts(self, format_id):
        if format_id == 'mp3':
            ydl_format = 'bestaudio'
            preferred_codec = 'mp3'
        else:
            ydl_format = format_id
            preferred_codec = 'mp3'

        return {
            'format': ydl_format,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': preferred_codec,
            }],
            'outtmpl': os.path.join(OUTPUT_DIRECTORY, '%(title)s.%(ext)s'),
            'ffmpeg_location': FFMPEG_PATH,
            'progress_hooks': [self.progress_hook],
            'noplaylist': True,
            'quiet': True,
        }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get-info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'error': 'URL tidak disediakan'}), 400

    logging.info(f"Mengambil info untuk URL: {url}")
    try:
        ydl_opts = {'noplaylist': True, 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            audio_formats = []
            # Opsi MP3 paling atas (akan convert bestaudio)
            audio_formats.append({
                'format_id': 'mp3',
                'label': 'MP3 - Audio Only (Direkomendasikan)'
            })

            AUDIO_EXTS = {"mp3", "m4a", "opus", "webm", "ogg", "wav", "aac"}
            for f in info.get('formats', []):
                ext = f.get('ext')
                if (
                    f.get('vcodec') == 'none'
                    and f.get('acodec') not in [None, '', 'none']
                    and ext in AUDIO_EXTS
                ):
                    bitrate = f.get('abr')
                    filesize = f.get('filesize') or f.get('filesize_approx')
                    filesize_mb = f"~{filesize / 1024 / 1024:.2f} MB" if filesize else ""
                    label = f"{ext.upper()} - {f.get('acodec')} ({int(bitrate)}kbps) {filesize_mb}" if bitrate else f"{ext.upper()} - {f.get('acodec')}"
                    audio_formats.append({
                        'format_id': f.get('format_id'),
                        'label': label,
                    })

            return jsonify({
                'success': True,
                'title': info.get('title', 'Judul tidak ditemukan'),
                'thumbnail': info.get('thumbnail', ''),
                'formats': audio_formats
            })
    except yt_dlp.utils.DownloadError as e:
        error_message = str(e)
        logging.error(f"yt-dlp Gagal mengambil info untuk '{url}': {error_message}")
        user_error = error_message.split('ERROR:')[-1].strip()
        if "confirm your age" in user_error:
            user_error = "Video ini memerlukan verifikasi usia."
        elif "Private video" in user_error:
            user_error = "Video ini bersifat pribadi."
        elif "Video unavailable" in user_error:
            user_error = "Video ini tidak tersedia."
        return jsonify({'success': False, 'error': user_error}), 400
    except Exception as e:
        logging.error(f"Kesalahan umum saat mengambil info untuk '{url}': {str(e)}")
        return jsonify({'success': False, 'error': 'Terjadi kesalahan tak terduga di server.'}), 500

@app.route('/cancel')
def cancel_download():
    download_id = request.args.get('id')
    if download_id and download_id in cancellation_events:
        cancellation_events[download_id].set()
        logging.info(f"Permintaan pembatalan diterima untuk ID: {download_id}")
        return jsonify({'status': 'cancelled'}), 200
    return jsonify({'status': 'not_found'}), 404

@app.route('/download')
def download():
    url = request.args.get('url')
    download_id = request.args.get('id')
    format_id = request.args.get('format_id')

    if not all([url, download_id, format_id]):
        return Response("data: " + json.dumps({'status': 'error', 'message': 'Parameter tidak lengkap.'}) + "\n\n",
                        content_type='text/event-stream')

    manager = DownloadManager(download_id)

    def download_thread_target():
        logging.info(f"Memulai unduhan untuk ID: {download_id} dengan format: {format_id}")
        try:
            with yt_dlp.YoutubeDL(manager.get_ydl_opts(format_id)) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            if "dibatalkan oleh pengguna" in str(e):
                logging.info(f"Unduhan {download_id} berhasil dibatalkan.")
            else:
                manager.progress_queue.put({'status': 'error', 'message': 'Proses unduhan gagal.'})
        except Exception as e:
            logging.error(f"Error tak terduga di thread {download_id}: {e}")
            manager.progress_queue.put({'status': 'error', 'message': 'Terjadi kesalahan tak terduga.'})
        finally:
            manager.progress_queue.put(None) 

    def generate_events():
        thread = threading.Thread(target=download_thread_target, name=f"Downloader-{download_id}")
        thread.start()
        while True:
            progress = manager.progress_queue.get()
            if progress is None: break
            yield f"data: {json.dumps(progress)}\n\n"
            if progress['status'] in ['completed', 'error']: break
        thread.join()
        if download_id in cancellation_events:
            del cancellation_events[download_id]
        logging.info(f"Sesi untuk ID {download_id} telah selesai.")

    return Response(generate_events(), content_type='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True, threaded=True)
