import os
import sys
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
OUTPUT_DIR = BASE_DIR / "output"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Inicializar rutas de FFmpeg y FFprobe de manera universal
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

# Detectar ruta de ffmpeg de manera universal (Render Linux / Windows)
try:
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    local_ffmpeg = BASE_DIR / "ffmpeg.exe"
    FFMPEG_BIN = str(local_ffmpeg) if local_ffmpeg.exists() else "ffmpeg"



app = FastAPI(title="LUAP PRO MUSIC API", version="2.0.0")

# Habilitar CORS completo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Montar carpetas estáticas para descargas directas y streaming
app.mount("/static/downloads", StaticFiles(directory=str(DOWNLOADS_DIR)), name="downloads")
app.mount("/static/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

class DownloadRequest(BaseModel):
    url: str
    media_type: str = "audio" # audio o video
    format: str = "mp3_320" # mp3_320, mp3_192, wav, m4a, flac, ogg, mp4_1080, mp4_720, mp4_480, webm, mkv
    normalize: bool = False
    trim_silence: bool = False

@app.get("/")
def serve_home():
    index_file = BASE_DIR.parent / "frontend" / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {
        "status": "online",
        "service": "LUAP PRO MUSIC API",
        "version": "2.0.0",
        "message": "Servidor activo y listo para procesar audio y video."
    }

STATS_FILE = BASE_DIR / "stats.json"

def get_visit_count():
    try:
        if STATS_FILE.exists():
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("visits", 1340)
    except Exception:
        pass
    return 1340

def increment_visit_count():
    visits = get_visit_count() + 1
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump({"visits": visits}, f)
    except Exception:
        pass
    return visits

@app.get("/api/health")
def health_check():
    return {"status": "online", "message": "API activa", "author": "Paul Nelson Curasi"}

@app.get("/api/visitors")
def visitor_counter():
    count = increment_visit_count()
    return {"success": True, "visits": count, "formatted": f"{count:,}"}


@app.post("/api/download")
async def download_media(req: DownloadRequest):
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Por favor proporciona un enlace válido.")

    url = req.url.strip()
    file_id = str(uuid.uuid4())[:8]
    out_template = str(DOWNLOADS_DIR / f"%(title)s_{file_id}.%(ext)s")

    # Detectar plataforma
    is_tiktok = "tiktok.com" in url.lower()
    is_instagram = "instagram.com" in url.lower()
    is_facebook = "facebook.com" in url.lower() or "fb.watch" in url.lower()

    cookies_file = BASE_DIR / "cookies.txt"
    cookie_path = str(cookies_file) if cookies_file.exists() else None

    ydl_opts = {
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'geo_bypass': True,
        'cookiefile': cookie_path,
        'ffmpeg_location': FFMPEG_BIN if os.path.exists(str(FFMPEG_BIN)) else None,
        'extractor_args': {
            'youtube': {
                'player_client': ['android'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 13; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Sec-Fetch-Mode': 'navigate',
        },
        'extract_flat': False,
    }

    # Configuración de Formatos de Audio y Video
    fmt = req.format.lower()

    # --- FORMATOS DE AUDIO ---
    if fmt == "mp3_320" or fmt == "mp3":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]
    elif fmt == "mp3_192":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    elif fmt == "wav":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'wav'}]
    elif fmt == "m4a" or fmt == "aac":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'm4a'}]
    elif fmt == "flac":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'flac'}]
    elif fmt == "ogg":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'vorbis'}]

    # --- FORMATOS DE VIDEO ---
    elif fmt == "mp4_1080":
        ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]/best'
    elif fmt == "mp4_720":
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best'
    elif fmt == "mp4_480" or fmt == "mp4":
        ydl_opts['format'] = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]/best'
    elif fmt == "webm":
        ydl_opts['format'] = 'bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'
    elif fmt == "mkv":
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mkv'
    else:
        # Por defecto MP3
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise HTTPException(status_code=400, detail="No se pudo obtener información del enlace multimedia.")

            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            title = info.get('title') or info.get('description') or 'multimedia_descargado'
            title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_', '.')]).rstrip()[:80]
            
            matched_files = list(DOWNLOADS_DIR.glob(f"*{file_id}*"))
            if not matched_files:
                matched_files = sorted(DOWNLOADS_DIR.glob("*.*"), key=os.path.getmtime, reverse=True)
                if not matched_files:
                    raise HTTPException(status_code=500, detail="El archivo se procesó pero no se pudo localizar en el almacenamiento.")

            final_file = matched_files[0]
            relative_url = f"/static/downloads/{final_file.name}"

            platform_detected = "YouTube"
            if is_tiktok: platform_detected = "TikTok"
            elif is_instagram: platform_detected = "Instagram"
            elif is_facebook: platform_detected = "Facebook"
            elif "soundcloud.com" in url.lower(): platform_detected = "SoundCloud"

            return {
                "success": True,
                "title": title,
                "platform": platform_detected,
                "filename": final_file.name,
                "download_url": f"/api/download-file/{final_file.name}",
                "stream_url": relative_url,
                "duration": info.get("duration", 0),
                "format_selected": fmt.upper(),
                "uploader": info.get("uploader") or info.get("creator") or platform_detected
            }

    except Exception as e:
        error_msg = str(e)
        if "Unsupported URL" in error_msg:
            raise HTTPException(status_code=400, detail="El enlace ingresado no es compatible o no contiene audio/video público.")
        raise HTTPException(status_code=500, detail=f"Error al procesar descarga: {error_msg}")

@app.get("/api/download-file/{filepath:path}")
async def get_download_file(filepath: str):

    file_path = DOWNLOADS_DIR / filepath
    if not file_path.exists():
        file_path = OUTPUT_DIR / filepath
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="El archivo solicitado ya no existe o caducó.")

    import re
    import urllib.parse
    
    filename = file_path.name
    # Sanitizar nombre ASCII puro para compatibilidad con cabeceras HTTP latin-1
    safe_ascii_name = re.sub(r'[^\x20-\x7E]', '_', filename)
    if not safe_ascii_name.strip() or safe_ascii_name == "_.mp3":
        safe_ascii_name = f"audio_luap_{filename[-8:]}"

    encoded_filename = urllib.parse.quote(filename)

    # application/octet-stream fuerza el diálogo de guardar archivo
    return FileResponse(
        path=str(file_path),
        media_type="application/octet-stream",
        filename=safe_ascii_name,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_ascii_name}"; filename*=UTF-8\'\'{encoded_filename}',
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )


@app.post("/api/edit")
async def edit_audio(
    file: UploadFile = File(...),
    start_time: str = Form("0"),
    end_time: Optional[str] = Form(None),
    fade_in: bool = Form(False),
    fade_out: bool = Form(False),
    normalize: bool = Form(False)
):
    try:
        file_ext = Path(file.filename).suffix or ".mp3"
        temp_input = DOWNLOADS_DIR / f"temp_{uuid.uuid4().hex[:8]}{file_ext}"
        output_filename = f"edit_{uuid.uuid4().hex[:8]}.mp3"
        temp_output = OUTPUT_DIR / output_filename

        with open(temp_input, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Construir filtros FFmpeg
        filters = []
        if normalize:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if fade_in:
            filters.append("afade=t=in:ss=0:d=2")

        cmd = [str(FFMPEG_BIN), "-y", "-ss", str(start_time), "-i", str(temp_input)]
        
        if end_time and end_time.strip() and end_time != "0":
            cmd.extend(["-to", str(end_time)])

        if filters:
            cmd.extend(["-af", ",".join(filters)])

        cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k", str(temp_output)])

        process = subprocess.run(cmd, capture_output=True, text=True)
        
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {process.stderr}")

        return FileResponse(
            path=str(temp_output),
            media_type="audio/mpeg",
            filename=f"luap_editado_{file.filename}"
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_input.exists():
            temp_input.unlink(missing_ok=True)

@app.post("/api/demucs")
async def separate_tracks(file: UploadFile = File(...)):
    temp_input = None
    try:
        ext = Path(file.filename).suffix or ".mp3"
        safe_base = f"track_{uuid.uuid4().hex[:8]}"
        temp_input = DOWNLOADS_DIR / f"{safe_base}{ext}"
        
        with open(temp_input, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Ejecutar Demucs con salida directa en MP3 de 320 kbps
        cmd = [
            sys.executable, "-m", "demucs",
            "--mp3",
            "--mp3-bitrate", "320",
            "--two-stems", "vocals",
            "-o", str(OUTPUT_DIR),
            str(temp_input)
        ]
        
        env = os.environ.copy()
        env["PATH"] = f"{BASE_DIR}{os.pathsep}{env.get('PATH', '')}"

        process = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Demucs error: {process.stderr or process.stdout}")

        track_folder = safe_base
        demucs_out = OUTPUT_DIR / "htdemucs" / track_folder

        vocals_file = demucs_out / "vocals.mp3"
        if not vocals_file.exists():
            vocals_file = demucs_out / "vocals.wav"

        no_vocals_file = demucs_out / "no_vocals.mp3"
        if not no_vocals_file.exists():
            no_vocals_file = demucs_out / "no_vocals.wav"

        original_stem = Path(file.filename).stem

        return {
            "success": True,
            "message": "Separación por IA completada con éxito",
            "track_name": original_stem,
            "vocals_stream": f"/static/output/htdemucs/{track_folder}/{vocals_file.name}",
            "vocals_download": f"/api/download-file/htdemucs/{track_folder}/{vocals_file.name}",
            "instrumental_stream": f"/static/output/htdemucs/{track_folder}/{no_vocals_file.name}",
            "instrumental_download": f"/api/download-file/htdemucs/{track_folder}/{no_vocals_file.name}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_input and temp_input.exists():
            temp_input.unlink(missing_ok=True)

# Servir Frontend Web Estático directamente en la raíz

FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend_app")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

