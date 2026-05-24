import subprocess
import json
from pathlib import Path


FORMAT_TO_MIME = {
    "mp4": "video/mp4",
    "mov": "video/mp4",
    "m4a": "audio/mp4",
    "3gp": "video/3gpp",
    "3g2": "video/3gpp2",
    "mj2": "video/mj2",
    
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "opus": "audio/opus",
    "oga": "audio/ogg",
        
    "matroska": "video/x-matroska",
    "webm": "video/webm",
    
    "avi": "video/x-msvideo",
    "mpeg": "video/mpeg",

    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "tiff": "image/tiff",
    "ico": "image/x-icon",
    "avif": "image/avif",
    "heic": "image/heic",
}   
    

def format_to_mime(format_name):
    print (f"Looking up mime for {format_name}")

    for fmt in format_name.split(","):
        fmt = fmt.strip()
    
        if fmt in FORMAT_TO_MIME:
            return FORMAT_TO_MIME[fmt]
    
    return "application/octet-stream"


def get_media_info(file_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration,format_name",
        "-of", "json",
        file_path 
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )

    data = json.loads(result.stdout)
    format_data = data.get("format", {})

    format_name = format_data.get("format_name")
    duration = format_data.get("duration")
    
    if not format_name:
        raise ValueError("Missing format info")

    #mime = format_to_mime(format_name)
    #if mime == "application/octet-stream":
        # try again
    ext = Path(file_path).suffix.lower()
    mime = format_to_mime(ext.split('.')[1]) if len(ext) > 1 and ext[0] == '.' else "application/octet-stream"

    print (f"mime type is {mime}")

    return {
        "mime": mime,
        "duration": float(duration) if duration else None,
        "format": format_name,
   }

