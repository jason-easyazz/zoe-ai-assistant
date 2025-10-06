from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess
import tempfile
import os
import uuid
import shutil
import hashlib
import time
from typing import List, Optional
from pathlib import Path

app = FastAPI(title="Zoe TTS Service")

# Cache directory for TTS audio files
CACHE_DIR = Path("/tmp/tts_cache")
CACHE_DIR.mkdir(exist_ok=True)

# Cache for TTS responses
tts_cache = {}

class TTSRequest(BaseModel):
    text: str
    voice: str = "default"
    speed: float = 1.0  # Multiplier 0.5–2.0 mapped to espeak -s based on base 140
    use_cache: bool = True  # Enable caching by default


class BatchItem(BaseModel):
    text: str
    voice: Optional[str] = None
    speed: Optional[float] = None


class BatchRequest(BaseModel):
    items: List[BatchItem]
    voice: str = "default"
    speed: float = 1.0

@app.get("/health")
async def health():
    return {"status": "healthy", "engine": "espeak-optimized"}

def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def _scale_speed_to_wpm(speed_factor: float, base_wpm: int = 140) -> int:
    # clamp to 0.5–2.0 per requirements
    safe_factor = _clamp(speed_factor, 0.5, 2.0)
    return max(80, int(base_wpm * safe_factor))


def _get_cache_key(text: str, voice: str, speed_factor: float) -> str:
    """Generate cache key for TTS request"""
    content = f"{text}|{voice}|{speed_factor}"
    return hashlib.md5(content.encode()).hexdigest()

def _synthesize_single(text: str, voice: str, speed_factor: float, use_cache: bool = True) -> str:
    # Check cache first
    if use_cache:
        cache_key = _get_cache_key(text, voice, speed_factor)
        cached_file = CACHE_DIR / f"{cache_key}.wav"
        
        if cached_file.exists():
            # Return cached file path
            return str(cached_file)
    
    # Create unique filename
    raw_filename = f"/tmp/tts_{uuid.uuid4().hex}.wav"

    # Map speed factor to espeak -s WPM
    espeak_wpm = _scale_speed_to_wpm(speed_factor)

    # Use espeak with optimized settings for clarity
    # -s speed (based on base 140)
    # -a amplitude 150
    # -g word gap 10ms
    espeak_cmd = [
        "espeak",
        "-w", raw_filename,
        "-s", str(espeak_wpm),
        "-a", "150",
        "-g", "10",
    ]

    # Voice handling: "default" lets espeak pick default; otherwise pass -v
    if voice and voice != "default":
        espeak_cmd.extend(["-v", voice])

    espeak_cmd.append(text)

    result = subprocess.run(espeak_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"TTS failed: {result.stderr}")

    # Post-process audio for better quality
    processed_filename = f"/tmp/tts_processed_{uuid.uuid4().hex}.wav"

    # ffmpeg: highpass/lowpass, loudness normalization, 16kHz mono
    # Use EBU R128 loudness normalization for consistent levels
    enhance_cmd = [
        "ffmpeg", "-y", "-i", raw_filename,
        "-af", "highpass=f=200,lowpass=f=3000,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "16000",
        "-ac", "1",
        processed_filename,
    ]

    enhance_result = subprocess.run(enhance_cmd, capture_output=True, text=True)

    final_file = processed_filename if enhance_result.returncode == 0 else raw_filename
    # If enhancement succeeded, remove raw
    if enhance_result.returncode == 0:
        try:
            os.remove(raw_filename)
        except OSError:
            pass

    # Cache the result if caching is enabled
    if use_cache and enhance_result.returncode == 0:
        cache_key = _get_cache_key(text, voice, speed_factor)
        cached_file = CACHE_DIR / f"{cache_key}.wav"
        try:
            shutil.copy2(final_file, cached_file)
        except OSError:
            pass

    return final_file


@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest, background_tasks: BackgroundTasks):
    """Generate clearer speech optimized for Whisper with caching"""
    try:
        final_file = _synthesize_single(
            text=request.text,
            voice=request.voice,
            speed_factor=request.speed,
            use_cache=request.use_cache,
        )

        # Only cleanup if not cached (cached files should persist)
        if not request.use_cache or not final_file.startswith(str(CACHE_DIR)):
            background_tasks.add_task(lambda p: os.path.exists(p) and os.remove(p), final_file)

        return FileResponse(
            final_file,
            media_type="audio/wav",
            filename="speech.wav",
            headers={
                "X-Audio-Quality": "optimized-for-stt",
                "X-Cache-Status": "hit" if final_file.startswith(str(CACHE_DIR)) else "miss"
            }
        )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/voices")
async def list_voices():
    try:
        proc = subprocess.run(["espeak", "--voices"], capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to list voices: {proc.stderr}")

        lines = proc.stdout.strip().splitlines()
        # Skip header lines (first 1-2 lines)
        data_lines = [ln for ln in lines if ln and not ln.lower().startswith("pty") and not ln.lower().startswith("enabled")] 

        voices = []
        for ln in data_lines:
            parts = ln.split()
            if len(parts) < 4:
                continue
            # Heuristic parsing: language code may be parts[1], name near the end
            language = parts[1]
            name = parts[-1]
            voices.append({
                "id": name,
                "name": name,
                "language": language,
            })

        # Always include a default option
        voices.insert(0, {"id": "default", "name": "default", "language": "auto"})
        return {"voices": voices}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cache/stats")
async def get_cache_stats():
    """Get TTS cache statistics"""
    try:
        cache_files = list(CACHE_DIR.glob("*.wav"))
        total_size = sum(f.stat().st_size for f in cache_files)
        
        return {
            "cache_enabled": True,
            "cache_directory": str(CACHE_DIR),
            "cached_files": len(cache_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cache/clear")
async def clear_cache():
    """Clear TTS cache"""
    try:
        cache_files = list(CACHE_DIR.glob("*.wav"))
        deleted_count = 0
        
        for cache_file in cache_files:
            try:
                cache_file.unlink()
                deleted_count += 1
            except OSError:
                pass
        
        return {
            "success": True,
            "deleted_files": deleted_count,
            "message": f"Cleared {deleted_count} cached files"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/synthesize/batch")
async def synthesize_batch(request: BatchRequest, background_tasks: BackgroundTasks):
    try:
        if not request.items:
            raise HTTPException(status_code=400, detail="No items provided")

        temp_dir = tempfile.mkdtemp(prefix="tts_batch_")
        output_files: List[str] = []

        for idx, item in enumerate(request.items):
            text = item.text
            voice = item.voice if item.voice is not None else request.voice
            speed_factor = item.speed if item.speed is not None else request.speed
            out_path = _synthesize_single(text=text, voice=voice, speed_factor=speed_factor)
            final_name = os.path.join(temp_dir, f"item_{idx + 1}.wav")
            shutil.move(out_path, final_name)
            output_files.append(final_name)

        # Zip the outputs
        zip_base = os.path.join("/tmp", f"tts_batch_{uuid.uuid4().hex}")
        zip_path = shutil.make_archive(zip_base, 'zip', temp_dir)

        # Cleanup temp_dir after response
        def _cleanup(paths: List[str]):
            for p in paths:
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    elif os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass

        background_tasks.add_task(_cleanup, [temp_dir, zip_path])

        return FileResponse(zip_path, media_type="application/zip", filename="tts_batch.zip")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
