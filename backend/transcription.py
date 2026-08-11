"""
Video-to-Notes AI Platform — STEP 2: Whisper fallback
Used only when a video has NO captions (youtube-transcript-api came up empty).

Pipeline (Section 4, step 4 of the plan):
  download audio only with yt-dlp -> transcribe locally with faster-whisper.

This never uploads audio anywhere — everything runs on the machine hosting the backend.
The 'small' model is the default: a reasonable CPU speed/accuracy tradeoff for lecture
and talk-style speech, as called out in Section 11 ("Key Considerations").
"""

import os
import tempfile
import shutil
from dataclasses import dataclass

from fastapi import HTTPException

# faster-whisper loads its model lazily and reuses it across requests instead of
# reloading (which is slow) on every single video.
_whisper_model = None

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")  # tiny/base/small/medium
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 = fastest on CPU


@dataclass
class WhisperResult:
    text: str
    language: str
    duration_seconds: float


def _get_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "faster-whisper is not installed. Run "
                    "'pip install -r requirements.txt' inside backend/ and try again."
                ),
            ) from exc

        # Downloads the model from Hugging Face on first use only, then caches it locally.
        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
        )
    return _whisper_model


def download_audio(video_url: str, workdir: str) -> str:
    """Download ONLY the audio track (not the full video) for a YouTube URL via yt-dlp."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "yt-dlp is not installed. Run 'pip install -r requirements.txt' "
                "inside backend/ and try again."
            ),
        ) from exc

    output_template = os.path.join(workdir, "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(video_url, download=True)
    except Exception as exc:  # yt-dlp raises its own broad DownloadError
        raise HTTPException(
            status_code=422,
            detail=f"Could not download audio for this video: {exc}",
        ) from exc

    # yt-dlp picks the final extension based on what the site served (m4a/webm/etc.)
    downloaded = [f for f in os.listdir(workdir) if f.startswith("audio.")]
    if not downloaded:
        raise HTTPException(status_code=422, detail="Audio download produced no file.")
    return os.path.join(workdir, downloaded[0])


def transcribe_audio(audio_path: str) -> WhisperResult:
    """Run local faster-whisper transcription on a downloaded audio file."""
    model = _get_model()
    segments, info = model.transcribe(audio_path, beam_size=5, vad_filter=True)
    text = " ".join(segment.text.strip() for segment in segments)
    return WhisperResult(
        text=text,
        language=info.language,
        duration_seconds=info.duration,
    )


def transcribe_video_with_whisper(video_url: str) -> WhisperResult:
    """Full fallback pipeline: download audio to a temp dir, transcribe, clean up."""
    workdir = tempfile.mkdtemp(prefix="v2n_audio_")
    try:
        audio_path = download_audio(video_url, workdir)
        return transcribe_audio(audio_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
