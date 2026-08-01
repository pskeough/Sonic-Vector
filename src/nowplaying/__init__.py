"""Now-playing sources.

The app needs one thing from the outside world: what is playing right now.
Everything downstream (tags, centroid matching, the curve, the preference log)
is local. So this package exists to make that one question answerable by more
than one means, and to make the answer the same shape whichever means replied.

The contract, which predates this package and is what the monitor loop
consumes, is a dict:

    {track_uri, name, artist, album, image_url, is_playing, is_private_session}

or None when nothing is playing. A source also answers `is_available()`.
"""

from .smtc import SmtcNowPlaying, local_key

__all__ = ["SmtcNowPlaying", "local_key"]
