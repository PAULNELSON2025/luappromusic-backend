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
    format: str = "mp3" # mp3, wav, mp4
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

@app.get("/api/health")
def health_check():
    return {"status": "online", "message": "API activa"}


@app.post("/api/download")
async def download_media(req: DownloadRequest):
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Por favor proporciona un enlace válido.")

    url = req.url.strip()
    file_id = str(uuid.uuid4())[:8]
    out_template = str(DOWNLOADS_DIR / f"%(title)s_{file_id}.%(ext)s")

    # Detectar plataforma para optimizar parámetros
    is_tiktok = "tiktok.com" in url.lower()
    is_instagram = "instagram.com" in url.lower()
    is_facebook = "facebook.com" in url.lower() or "fb.watch" in url.lower()

    ydl_opts = {
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'ffmpeg_location': FFMPEG_BIN if os.path.exists(str(FFMPEG_BIN)) else None,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Sec-Fetch-Mode': 'navigate',
        },
        'extract_flat': False,
    }

    if req.format in ["mp3", "wav"]:
        # Para TikTok e Instagram, buscar cualquier stream de audio o extraer del video
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': req.format,
            'preferredquality': '320' if req.format == 'mp3' else None,
        }]
    else:
        # Video MP4
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise HTTPException(status_code=400, detail="No se pudo obtener información del enlace multimedia.")

            # Manejar listas o entradas únicas
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            title = info.get('title') or info.get('description') or 'multimedia_descargado'
            # Limpiar caracteres especiales del título
            title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_', '.')]).rstrip()[:80]
            
            # Buscar el archivo generado en downloads
            matched_files = list(DOWNLOADS_DIR.glob(f"*{file_id}*"))
            if not matched_files:
                # Búsqueda fallback
                matched_files = sorted(DOWNLOADS_DIR.glob("*.*"), key=os.path.getmtime, reverse=True)
                if not matched_files:
                    raise HTTPException(status_code=500, detail="El archivo se procesó pero no se pudo localizar en el almacenamiento.")

            final_file = matched_files[0]
            relative_url = f"/static/downloads/{final_file.name}"

            # Detectar red social de origen
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
                "uploader": info.get("uploader") or info.get("creator") or platform_detected
            }

    except Exception as e:
        error_msg = str(e)
        if "Unsupported URL" in error_msg:
            raise HTTPException(status_code=400, detail="El enlace ingresado no es compatible o no contiene audio/video público.")
        raise HTTPException(status_code=500, detail=f"Error al procesar descarga: {error_msg}")

@app.get("/api/download-file/{filename}")
async def get_download_file(filename: str):
    file_path = DOWNLOADS_DIR / filename
    if not file_path.exists():
        file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="El archivo solicitado ya no existe o caducó.")

    media_type = "audio/mpeg"
    if filename.endswith(".mp4"):
        media_type = "video/mp4"
    elif filename.endswith(".wav"):
        media_type = "audio/wav"

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
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
    try:
        temp_input = DOWNLOADS_DIR / f"demucs_{uuid.uuid4().hex[:8]}_{file.filename}"
        with open(temp_input, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Ejecutar Demucs
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems", "vocals",
            "-o", str(OUTPUT_DIR),
            str(temp_input)
        ]
        
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Demucs error: {process.stderr}")

        track_name = Path(temp_input).stem
        demucs_out = OUTPUT_DIR / "htdemucs" / track_name

        vocals_path = demucs_out / "vocals.wav"
        no_vocals_path = demucs_out / "no_vocals.wav"

        return {
            "success": True,
            "message": "Pistas separadas con éxito",
            "vocals_url": f"/static/output/htdemucs/{track_name}/vocals.wav" if vocals_path.exists() else None,
            "instrumental_url": f"/static/output/htdemucs/{track_name}/no_vocals.wav" if no_vocals_path.exists() else None
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Servir Frontend Web Estático directamente en la raíz
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend_app")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

