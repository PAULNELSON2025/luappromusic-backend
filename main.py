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

import time
from datetime import date

def get_stats_data():
    today_str = str(date.today())
    default_stats = {
        "total_visits": 2450,
        "unique_visitors": 1820,
        "today_visits": 142,
        "today_date": today_str,
        "recent_hits": []
    }
    try:
        if STATS_FILE.exists():
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("today_date") != today_str:
                    data["today_date"] = today_str
                    data["today_visits"] = 0
                return data
    except Exception:
        pass
    return default_stats

def record_visitor(is_new_session: bool = True):
    stats = get_stats_data()
    stats["total_visits"] = stats.get("total_visits", 2450) + 1
    stats["today_visits"] = stats.get("today_visits", 0) + 1
    if is_new_session:
        stats["unique_visitors"] = stats.get("unique_visitors", 1820) + 1

    now = time.time()
    # Mantener hits de los últimos 15 minutos para usuarios en línea
    recent = stats.get("recent_hits", [])
    recent = [t for t in recent if now - t < 900]
    recent.append(now)
    stats["recent_hits"] = recent

    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f)
    except Exception:
        pass

    online_count = max(3, len(recent) + 2) # Base activa en vivo
    return {
        "total_visits": stats["total_visits"],
        "unique_visitors": stats["unique_visitors"],
        "today_visits": stats["today_visits"],
        "online_now": online_count,
        "formatted_total": f"{stats['total_visits']:,}"
    }

@app.get("/api/health")
def health_check():
    return {"status": "online", "message": "API activa", "author": "Paul Nelson Curasi"}

@app.get("/api/visitors")
def visitor_counter(new_session: bool = False):
    stats = record_visitor(is_new_session=new_session)
    return {"success": True, **stats}



@app.post("/api/download")
async def download_media(req: DownloadRequest):
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Por favor proporciona un enlace válido.")

    url = req.url.strip()
    file_id = str(uuid.uuid4())[:8]
    out_template = str(DOWNLOADS_DIR / f"%(title)s_{file_id}.%(ext)s")

    # Resolver acortadores de URL comunes (fb.watch, vt.tiktok.com, vm.tiktok.com, youtu.be)
    if "fb.watch" in url.lower() or "vt.tiktok.com" in url.lower() or "vm.tiktok.com" in url.lower():
        try:
            import requests
            resp = requests.head(url, allow_redirects=True, timeout=5, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'})
            if resp.url and resp.url != url:
                url = resp.url
        except Exception:
            pass

    # Detectar plataforma
    is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()
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
        'restrictfilenames': True,
        'windowsfilenames': True,
        'cookiefile': cookie_path,
        'ffmpeg_location': FFMPEG_BIN if os.path.exists(str(FFMPEG_BIN)) else None,
        'extract_flat': False,
    }

    if is_youtube:
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': ['android'],
                'player_skip': ['webpage', 'configs'],
            }
        }
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 13; SM-G981B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Sec-Fetch-Mode': 'navigate',
        }
    elif is_tiktok:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.tiktok.com/',
        }
    elif is_facebook:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
            'Sec-Fetch-Mode': 'navigate',
        }
    elif is_instagram:
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.instagram.com/',
        }
        ydl_opts['extractor_args'] = {
            'instagram': {
                'api': ['web'],
            }
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

            raw_title = info.get('title') or info.get('description') or 'multimedia_descargado'
            clean_title = "".join([c for c in raw_title if c.isalnum() or c in (' ', '-', '_', '.')]).rstrip()[:80]
            
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
                "title": clean_title or final_file.name,
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
        if "login required" in error_msg.lower() or "empty media response" in error_msg.lower():
            raise HTTPException(status_code=400, detail="El enlace solicitado es privado o requiere inicio de sesión en Instagram/Facebook para acceder.")
        elif "unsupported url" in error_msg.lower():
            raise HTTPException(status_code=400, detail="El enlace ingresado no es compatible o no contiene un video/audio público.")
        raise HTTPException(status_code=500, detail=f"Error al procesar descarga: {error_msg}")


@app.get("/api/download-file/{filepath:path}")
async def get_download_file(filepath: str):
    import urllib.parse
    import re
    import time
    
    unquoted_path = urllib.parse.unquote(filepath)
    raw_path = filepath
    
    file_path = None
    # 1. Búsqueda directa con decodificación de caracteres especiales
    for candidate_name in [unquoted_path, raw_path, Path(unquoted_path).name, Path(raw_path).name]:
        for base_dir in [DOWNLOADS_DIR, OUTPUT_DIR, BASE_DIR]:
            p = base_dir / candidate_name
            if p.exists() and p.is_file():
                file_path = p
                break
        if file_path:
            break

    # 2. Búsqueda recursiva por nombre exacto o coincidencia de ID
    if not file_path:
        target_name = Path(unquoted_path).name
        for base_dir in [DOWNLOADS_DIR, OUTPUT_DIR]:
            matches = list(base_dir.rglob(target_name))
            if matches:
                file_path = matches[0]
                break
        
        # 3. Búsqueda por ID parcial (_xxxxxxxx)
        if not file_path:
            stem = Path(target_name).stem
            if "_" in stem:
                suffix_id = stem.split("_")[-1]
                for base_dir in [DOWNLOADS_DIR, OUTPUT_DIR]:
                    matches = list(base_dir.rglob(f"*{suffix_id}*"))
                    if matches:
                        file_path = matches[0]
                        break

        # 4. Búsqueda por prefijo del título (sin extensión)
        if not file_path:
            stem_clean = "".join([c for c in Path(target_name).stem if c.isalnum() or c in (' ', '-', '_')]).strip()[:30]
            if stem_clean:
                for base_dir in [DOWNLOADS_DIR, OUTPUT_DIR]:
                    matches = list(base_dir.rglob(f"*{stem_clean}*"))
                    if matches:
                        file_path = matches[0]
                        break

        # 5. Fallback al archivo descargado más reciente si fue en los últimos 5 minutos
        if not file_path:
            recent_files = sorted(DOWNLOADS_DIR.glob("*.*"), key=os.path.getmtime, reverse=True)
            if recent_files and (time.time() - os.path.getmtime(recent_files[0])) < 300:
                file_path = recent_files[0]

    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="El archivo solicitado ya no existe o caducó.")

    filename = file_path.name
    # Sanitizar nombre ASCII puro para cabecera HTTP estándar
    safe_ascii_name = re.sub(r'[^\x20-\x7E]', '_', filename)
    if not safe_ascii_name.strip() or safe_ascii_name == "_.mp3":
        safe_ascii_name = f"audio_luap_{filename[-8:]}"

    encoded_filename = urllib.parse.quote(filename)

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
    start_time: float = Form(0.0),
    end_time: Optional[float] = Form(None),
    fade_in_sec: float = Form(0.0),
    fade_out_sec: float = Form(0.0),
    volume_gain: float = Form(0.0),
    speed: float = Form(1.0),
    eq_bass: float = Form(0.0),
    eq_mid: float = Form(0.0),
    eq_treble: float = Form(0.0),
    normalize: bool = Form(False),
    reverse: bool = Form(False),
    export_format: str = Form("mp3")
):
    temp_input = None
    try:
        file_ext = Path(file.filename).suffix or ".mp3"
        safe_id = uuid.uuid4().hex[:8]
        temp_input = DOWNLOADS_DIR / f"raw_{safe_id}{file_ext}"
        
        with open(temp_input, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Construir filtros de audio profesionales
        filters = []

        # 1. Ecualizador paramétrico de 3 bandas
        if eq_bass != 0:
            filters.append(f"equalizer=f=100:width_type=h:width=100:g={eq_bass}")
        if eq_mid != 0:
            filters.append(f"equalizer=f=1000:width_type=h:width=1000:g={eq_mid}")
        if eq_treble != 0:
            filters.append(f"equalizer=f=8000:width_type=h:width=3000:g={eq_treble}")

        # 2. Ganancia de Volumen (dB)
        if volume_gain != 0:
            filters.append(f"volume={volume_gain}dB")

        # 3. Velocidad / Pitch (atempo)
        if speed != 1.0 and 0.5 <= speed <= 2.0:
            filters.append(f"atempo={speed}")

        # 4. Fade In
        if fade_in_sec > 0:
            filters.append(f"afade=t=in:ss=0:d={fade_in_sec}")

        # 5. Fade Out
        if fade_out_sec > 0 and end_time and end_time > start_time:
            duration = (end_time - start_time) / (speed if speed > 0 else 1.0)
            fade_start = max(0.0, duration - fade_out_sec)
            filters.append(f"afade=t=out:st={fade_start}:d={fade_out_sec}")

        # 6. Efecto Reverse
        if reverse:
            filters.append("areverse")

        # 7. Normalización Profesional EBU R128 (-14 LUFS Spotify)
        if normalize:
            filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")

        # Formato de exportación
        export_ext = export_format.lower()
        if export_ext not in ["mp3", "wav", "flac", "m4a", "ogg"]:
            export_ext = "mp3"

        out_name = f"master_{safe_id}.{export_ext}"
        temp_output = OUTPUT_DIR / out_name

        cmd = [str(FFMPEG_BIN), "-y"]
        if start_time > 0:
            cmd.extend(["-ss", str(start_time)])

        cmd.extend(["-i", str(temp_input)])

        if end_time and end_time > start_time:
            cmd.extend(["-to", str(end_time)])

        if filters:
            cmd.extend(["-af", ",".join(filters)])

        # Codecs según formato de alta fidelidad
        if export_ext == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
        elif export_ext == "wav":
            cmd.extend(["-c:a", "pcm_s24le"])
        elif export_ext == "flac":
            cmd.extend(["-c:a", "flac"])
        elif export_ext == "m4a":
            cmd.extend(["-c:a", "aac", "-b:a", "320k"])
        elif export_ext == "ogg":
            cmd.extend(["-c:a", "libvorbis", "-q:a", "7"])

        cmd.append(str(temp_output))

        env = os.environ.copy()
        env["PATH"] = f"{BASE_DIR}{os.pathsep}{env.get('PATH', '')}"

        process = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Error en masterización: {process.stderr}")

        original_stem = Path(file.filename).stem
        final_filename = f"{original_stem}_master.{export_ext}"

        return {
            "success": True,
            "message": "Masterización completada con éxito",
            "filename": final_filename,
            "stream_url": f"/static/output/{out_name}",
            "download_url": f"/api/download-file/{out_name}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_input and temp_input.exists():
            temp_input.unlink(missing_ok=True)


@app.post("/api/mixdown")
async def multitrack_mixdown(
    files: List[UploadFile] = File(...),
    configs: str = Form("[]"),
    master_gain: float = Form(0.0),
    normalize: bool = Form(False),
    export_format: str = Form("mp3")
):
    temp_files = []
    try:
        import json
        track_configs = json.loads(configs)
        safe_id = uuid.uuid4().hex[:8]

        cmd = [str(FFMPEG_BIN), "-y"]
        filter_complex_parts = []

        for idx, upload_file in enumerate(files):
            ext = Path(upload_file.filename).suffix or ".mp3"
            t_path = DOWNLOADS_DIR / f"multi_{safe_id}_{idx}{ext}"
            temp_files.append(t_path)

            with open(t_path, "wb") as buffer:
                shutil.copyfileobj(upload_file.file, buffer)

            cmd.extend(["-i", str(t_path)])

            cfg = track_configs[idx] if idx < len(track_configs) else {}
            vol = float(cfg.get("volume", 1.0))
            offset_ms = int(float(cfg.get("offset", 0.0)) * 1000)
            
            track_filters = []
            if offset_ms > 0:
                track_filters.append(f"adelay={offset_ms}|{offset_ms}")
            if vol != 1.0:
                track_filters.append(f"volume={vol}")

            filt_str = ",".join(track_filters) if track_filters else "anull"
            filter_complex_parts.append(f"[{idx}:a]{filt_str}[a{idx}]")

        inputs_count = len(files)
        amix_inputs = "".join([f"[a{i}]" for i in range(inputs_count)])
        mix_filter = f"{amix_inputs}amix=inputs={inputs_count}:duration=longest:dropout_transition=0"

        master_filters = []
        if master_gain != 0:
            master_filters.append(f"volume={master_gain}dB")
        if normalize:
            master_filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")

        if master_filters:
            mix_filter += f",{','.join(master_filters)}"

        mix_filter += "[out]"
        full_filter_complex = ";".join(filter_complex_parts) + ";" + mix_filter

        cmd.extend(["-filter_complex", full_filter_complex, "-map", "[out]"])

        export_ext = export_format.lower()
        if export_ext not in ["mp3", "wav", "flac", "m4a", "ogg"]:
            export_ext = "mp3"

        out_name = f"multitrack_{safe_id}.{export_ext}"
        temp_output = OUTPUT_DIR / out_name

        if export_ext == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
        elif export_ext == "wav":
            cmd.extend(["-c:a", "pcm_s24le"])
        elif export_ext == "flac":
            cmd.extend(["-c:a", "flac"])
        elif export_ext == "m4a":
            cmd.extend(["-c:a", "aac", "-b:a", "320k"])
        elif export_ext == "ogg":
            cmd.extend(["-c:a", "libvorbis", "-q:a", "7"])

        cmd.append(str(temp_output))

        env = os.environ.copy()
        env["PATH"] = f"{BASE_DIR}{os.pathsep}{env.get('PATH', '')}"

        process = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Error en mezcla multitrack: {process.stderr}")

        return {
            "success": True,
            "message": f"Mezcla de {inputs_count} pistas completada con éxito",
            "filename": f"mezcla_master_{safe_id}.{export_ext}",
            "stream_url": f"/static/output/{out_name}",
            "download_url": f"/api/download-file/{out_name}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        for t in temp_files:
            if t.exists():
                t.unlink(missing_ok=True)


@app.post("/api/master")
async def master_audio_track(
    file: UploadFile = File(...),
    preset: str = Form("streaming"),
    target_lufs: float = Form(-14.0),
    sub_bass: float = Form(0.0),
    warmth: float = Form(0.0),
    presence: float = Form(0.0),
    air_high: float = Form(0.0),
    stereo_width: float = Form(1.2),
    analog_warmth: bool = Form(True),
    glue_comp: bool = Form(True),
    export_format: str = Form("mp3")
):
    temp_input = None
    try:
        file_ext = Path(file.filename).suffix or ".mp3"
        safe_id = uuid.uuid4().hex[:8]
        temp_input = DOWNLOADS_DIR / f"raw_master_{safe_id}{file_ext}"

        with open(temp_input, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Aplicar presets automáticos si se solicita
        if preset == "club_bass":
            sub_bass += 3.5
            presence += 1.5
            target_lufs = -11.0
            stereo_width = 1.3
        elif preset == "radio_hit":
            warmth += 1.0
            presence += 2.5
            air_high += 3.0
            target_lufs = -12.0
            stereo_width = 1.25
        elif preset == "acoustic_warm":
            warmth += 2.0
            air_high += 1.5
            target_lufs = -16.0
            stereo_width = 1.15
        elif preset == "loud_heavy":
            sub_bass += 2.0
            presence += 2.0
            target_lufs = -9.0
            stereo_width = 1.35
        elif preset == "streaming":
            target_lufs = -14.0
            stereo_width = 1.2

        filters = []

        # 1. Filtro Highpass de corte infrasónico (elimina ruidos por debajo de 28Hz para dar pegada)
        filters.append("highpass=f=28")

        # 2. Ecualización de Masterización Profesional
        if sub_bass != 0:
            filters.append(f"equalizer=f=60:width_type=q:width=1.2:g={sub_bass}")
        if warmth != 0:
            filters.append(f"equalizer=f=350:width_type=q:width=1.0:g={warmth}")
        if presence != 0:
            filters.append(f"equalizer=f=3500:width_type=q:width=1.2:g={presence}")
        if air_high != 0:
            filters.append(f"equalizer=f=12500:width_type=q:width=0.8:g={air_high}")

        # 3. Saturación Analógica & Armónicos de Cinta (Tape Saturation)
        if analog_warmth:
            filters.append("aexciter=freq=4000:amount=1.5:drive=8.5")

        # 4. Expansión de Campo Estéreo 3D (Stereo Widener)
        if stereo_width > 1.0:
            filters.append(f"extrastereo=m={stereo_width}")

        # 5. Compresor Glue de Pegada Analógica
        if glue_comp:
            filters.append("acompressor=threshold=0.125:ratio=2.5:attack=15:release=120:makeup=1.2")

        # 6. Limitador Brickwall & Maximización EBU R128
        filters.append(f"loudnorm=I={target_lufs}:TP=-1.0:LRA=8")


        export_ext = export_format.lower()
        if export_ext not in ["mp3", "wav", "flac", "m4a"]:
            export_ext = "mp3"

        out_name = f"master_studio_{safe_id}.{export_ext}"
        temp_output = OUTPUT_DIR / out_name

        cmd = [str(FFMPEG_BIN), "-y", "-i", str(temp_input)]
        if filters:
            cmd.extend(["-af", ",".join(filters)])

        if export_ext == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
        elif export_ext == "wav":
            cmd.extend(["-c:a", "pcm_s24le"])
        elif export_ext == "flac":
            cmd.extend(["-c:a", "flac"])
        elif export_ext == "m4a":
            cmd.extend(["-c:a", "aac", "-b:a", "320k"])

        cmd.append(str(temp_output))

        env = os.environ.copy()
        env["PATH"] = f"{BASE_DIR}{os.pathsep}{env.get('PATH', '')}"

        process = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if process.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Error en motor de masterización: {process.stderr}")

        original_stem = Path(file.filename).stem
        final_filename = f"{original_stem}_MASTER_PRO.{export_ext}"

        return {
            "success": True,
            "message": "Pista masterizada con éxito con motor analógico de alta fidelidad",
            "filename": final_filename,
            "preset_applied": preset.upper(),
            "target_lufs": target_lufs,
            "stream_url": f"/static/output/{out_name}",
            "download_url": f"/api/download-file/{out_name}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_input and temp_input.exists():
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

