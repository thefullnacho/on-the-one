from pathlib import Path


def song_audio(d):
    """songs/<song>/audio.*, lossless first: the repo ships a small audio.mp3 preview next to your own audio.wav."""
    files = sorted(Path(d).glob("audio.*"), key=lambda p: (p.suffix.lower() not in (".wav", ".flac"), p.name))
    return files[0] if files else None
