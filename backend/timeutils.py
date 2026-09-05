"""
timeutils.py — shared time utilities for Notewell V2.

Used by:
  - main.py            (parsing user start_time/end_time; picking marker density; building
                         "[MM:SS-MM:SS]" prefixes for chunk summaries)
  - learning_modes.py   (mode skeletons instruct the model to echo these formatted timestamps
                         into H2 headings — this module doesn't need to be imported there,
                         just knows the format the skeletons expect)

"""

import re


def parse_timestamp(value) -> float:
    """
    Accepts:
      - int or float seconds directly           -> 90
      - a numeric string                         -> "90"
      - "MM:SS"                                  -> "12:30"
      - "HH:MM:SS"                                -> "1:02:00"
    Returns seconds as a float.
    Raises ValueError on anything unparseable.
    """
    if value is None:
        raise ValueError("parse_timestamp() received None")

    # Already numeric (int or float) — nothing to parse
    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    # Plain numeric string, e.g. "90" or "90.5"
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)

    # "MM:SS" or "HH:MM:SS"
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Unrecognized timestamp format: {value!r}")

    try:
        parts = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"Unrecognized timestamp format: {value!r}")

    if len(parts) == 2:
        hours = 0.0
        minutes, seconds = parts
    else:
        hours, minutes, seconds = parts

    if not (0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError(f"Minutes/seconds out of range in: {value!r}")

    return hours * 3600 + minutes * 60 + seconds


def format_timestamp(seconds: float) -> str:
    """
    seconds -> "MM:SS" if under an hour, else "H:MM:SS".
    Used for markdown H2 headings and frontend chip labels.
    """
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# Snapped to "clean" intervals so headings never land on an odd number like 7 or 23 minutes.
CLEAN_INTERVALS_MIN = [5, 10, 15, 20, 30, 45, 60]
TARGET_MARKERS = 10  # aim for ~8 section headings regardless of video length


def pick_interval_seconds(duration_seconds: float) -> int:
    """
    Picks a chunking interval (in seconds) so a video of the given duration ends up
    with roughly TARGET_MARKERS section headings, snapped to a clean interval.

    Examples:
      12-minute video -> target = (12/8) = 1.5 min -> smallest clean interval >= 1.5 is 5  -> 300s
      3-hour video     -> target = (180/8) = 22.5   -> smallest clean interval >= 22.5 is 30 -> 1800s
    """
    if duration_seconds <= 0:
        return CLEAN_INTERVALS_MIN[0] * 60

    target_minutes = (duration_seconds / 60) / TARGET_MARKERS

    for minutes in CLEAN_INTERVALS_MIN:
        if minutes >= target_minutes:
            return minutes * 60

    return CLEAN_INTERVALS_MIN[-1] * 60