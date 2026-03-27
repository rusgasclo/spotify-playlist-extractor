#!/usr/bin/env python3
"""
Standalone track resolver for m3u playlists.
Attempts to resolve Spotify playlist entries to actual local music files.

Usage:
    python resolve_tracks.py                    # Process all m3u files in playlists/
    python resolve_tracks.py "Playlist Name"    # Process specific playlist
"""

import os
import re
import sys
import argparse
from mutagen import File as MutagenFile

# ===== Configuration =====
MUSIC_ROOT = r""  # <-- change this to your music library root
HYBRID_MODE = True  # Enable second-pass search if normal album match fails ##currently not in use all seaches are hybrid mode
DEBUG = True

def debug(*args):
    if DEBUG:
        print("[DEBUG]", *args)

# ===== Helper Functions (from sple.py) =====

def normalize_path(path):
    path = path.replace("\\", "/")
    path = re.sub(r"/+", "/", path)
    return path.lower()

def normalize_text(s):
    """Normalize text for fuzzy matching (remove punctuation, lowercase)."""
    return re.sub(r"[^a-z0-9]", "", normalize_path(s))

def find_best_match(root, target):
    """Return the best matching folder inside root with a minimum similarity threshold."""
    if not os.path.isdir(root):
        return None

    norm_target = normalize_text(target)
    best = None
    best_score = 0

    for folder in os.listdir(root):
        full = os.path.join(root, folder)
        if not os.path.isdir(full):
            continue

        norm_folder = normalize_text(folder)

        # Basic fuzzy scoring
        score = 0
        if norm_target in norm_folder:
            score += 2
        if norm_folder in norm_target:
            score += 1

        # Compute overlap ratio
        overlap = len(set(norm_target) & set(norm_folder))
        max_len = max(len(norm_target), len(norm_folder))
        similarity = overlap / max_len if max_len else 0

        # Reject weak matches
        if score < 2 and similarity < 0.5:
            continue

        if score > best_score:
            best_score = score
            best = folder

    return best

def strip_soundtrack_noise(name):
    noise = [
        "music from the motion picture",
        "original motion picture soundtrack",
        "motion picture soundtrack",
        "original soundtrack",
        "soundtrack",
        "ost",
        "deluxe edition",
        "expanded edition",
        "special edition",
        "remastered",
        "edition"
    ]

    n = name.lower()

    for word in noise:
        n = n.replace(word, "")

    # THEN normalize
    return normalize_text(n)

def clean_track_title(title):
    title = re.sub(r"\(feat[^\)]*\)", "", title, flags=re.IGNORECASE)
    t = normalize_text(title)

    soundtrack_terms = [
        "musicfromthemotionpicture",
        "fromthemotionpicture",
        "originalmotionpicturesoundtrack",
        "motionpicturesoundtrack",
        "originalsoundtrack",
        "soundtrack",
        "ost",
    ]
    for term in soundtrack_terms:
        t = t.replace(term, "")

    edition_terms = [
        "deluxeedition",
        "expandededition",
        "specialedition",
        "remastered",
        "remaster",
        "bonustrack",
        "singleversion",
        "radioedit",
        "liveat",
        "livefrom",
        "live",
    ]
    for term in edition_terms:
        t = t.replace(term, "")

    return normalize_text(t).strip()


def clean_artist_name(name):
    n = name.lower().strip()
    n = n.replace("&", "and")
    if n.startswith("the "):
        n = n[4:]
    return normalize_text(n)


def metadata_artist_matches(filepath, artist):
    try:
        raw = MutagenFile(filepath)
        easy = MutagenFile(filepath, easy=True)

        target = clean_artist_name(artist)
        candidates = []

        # --- ID3v2.3 raw frames (MP3) ---
        if raw and hasattr(raw, "tags") and raw.tags:
            for key, value in raw.tags.items():
                k = key.lower()

                # Standard artist frames
                if k.startswith("tpe1") and hasattr(value, "text"):
                    candidates.extend(value.text)
                if k.startswith("tpe2") and hasattr(value, "text"):
                    candidates.extend(value.text)

                # MusicBrainz multi-artist frames stored as TXXX
                if k.startswith("txxx") and hasattr(value, "desc"):
                    desc = value.desc.lower()
                    if desc in ("artists", "artist", "artistsort", "artist sort order"):
                        if hasattr(value, "text"):
                            candidates.extend(value.text)

        # --- FLAC/M4A/Vorbis easy tags ---
        if easy:
            for key in (
                "artist",
                "artists",
                "albumartist",
                "performer",
                "composer",
                "artistsort",
                "artistsortorder",
                "artist sort order",
            ):
                if key in easy:
                    candidates.extend(easy[key])

        # --- CRITICAL: split BEFORE normalization ---
        split_candidates = []
        for c in candidates:
            parts = re.split(
                r"[,&/]|feat\.?|featuring|with",
                c,
                flags=re.IGNORECASE
            )
            split_candidates.extend(parts)

        # Clean + normalize each candidate
        cleaned = [clean_artist_name(c) for c in split_candidates if c.strip()]

        # Exact match OR prefix match OR primary-artist match
        for c in cleaned:
            if not c:  # ignore empty strings
                continue

            if c == target:
                return True
            if c.startswith(target):
                return True
            if target.startswith(c):
                return True

        return False

    except Exception:
        return False

def find_real_track_path(MUSIC_ROOT, artist, album, title, HYBRID_MODE):
    clean_title = clean_track_title(title)
    norm_title = clean_title

    def title_matches(fname):
        base = os.path.splitext(fname)[0]
        norm_file = normalize_text(base)

        if norm_file == norm_title:
            return True

        stripped = norm_file.lstrip("0123456789")
        if stripped == norm_title:
            return True

        if f"-{norm_title}-" in f"-{norm_file}-":
            return True

        return False

    def search_album(artist_path, album_folder):
        album_path = os.path.join(artist_path, album_folder)
        if not os.path.isdir(album_path):
            return None

        for fname in sorted(os.listdir(album_path)):
            if not fname.lower().endswith((".flac", ".mp3", ".m4a", ".ogg", ".wav", ".opus")):
                continue

            full_path = os.path.join(album_path, fname)

            if title_matches(fname) and metadata_artist_matches(full_path, artist):
                return f"{os.path.basename(artist_path)}/{album_folder}/{fname}"

        return None

    # 1. Search the specified album
    artist_folder = find_best_match(MUSIC_ROOT, artist)
    if artist_folder:
        artist_path = os.path.join(MUSIC_ROOT, artist_folder)
        album_folder = find_best_match(artist_path, album)

        if album_folder:
            result = search_album(artist_path, album_folder)
            if result:
                return result

    # 2. Search all albums by the artist
    if artist_folder:
        artist_path = os.path.join(MUSIC_ROOT, artist_folder)
        for album_folder in sorted(os.listdir(artist_path)):
            result = search_album(artist_path, album_folder)
            if result:
                return result

    # 3. Search all Various Artists albums
    va_folder = find_best_match(MUSIC_ROOT, "Various Artists")
    if va_folder:
        va_path = os.path.join(MUSIC_ROOT, va_folder)
        for album_folder in sorted(os.listdir(va_path)):
            result = search_album(va_path, album_folder)
            if result:
                return result

    return None

# ===== M3U Parsing =====

def parse_m3u(filepath):
    """Parse an m3u file and extract track information.
    
    Returns list of dicts with keys: artist, album, title, path, extinf
    """
    tracks = []
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"ERROR: Could not read {filepath}: {e}")
        return tracks

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        
        # Look for EXTINF lines
        if line.startswith("#EXTINF:"):
            # Parse metadata: #EXTINF:0,artist - title
            metadata = line.replace("#EXTINF:0,", "").strip()
            
            # Try to extract artist and title from "artist - title"
            if " - " in metadata:
                artist, title = metadata.split(" - ", 1)
                artist = artist.strip()
                title = title.strip()
            else:
                artist = "Unknown Artist"
                title = metadata
            
            # Next line should be the path
            if i + 1 < len(lines):
                path = lines[i + 1].rstrip()
                
                # Try to extract album from the file path
                # Paths often look like: ./artist/album/track.mp3
                album = extract_album_from_path(path)
                
                tracks.append({
                    "artist": artist,
                    "album": album,
                    "title": title,
                    "path": path,
                    "extinf": metadata,
                    "original_line": i
                })
                i += 2
            else:
                i += 1
        else:
            i += 1
    
    return tracks


def extract_album_from_path(path):
    """Extract album name from file path.
    
    Paths look like: ./artist/album/track.mp3
    Returns the second directory component if available.
    """
    # Normalize slashes
    path = path.replace("\\", "/")
    
    # Remove leading ./
    path = path.lstrip("./")
    
    # Split by /
    parts = path.split("/")
    
    # Try to get album (second component if 3+ parts, or Unknown)
    if len(parts) >= 3:
        # Second component is likely the album
        return parts[1]
    elif len(parts) == 2:
        # Only artist/track - use part of filename as album guess
        return parts[0]
    else:
        return "Unknown Album"


# ===== Resolution & Reporting =====

def resolve_playlist(playlist_path):
    """Resolve tracks and write updated playlist back to disk."""

    print(f"\n{'='*70}")
    print(f"Processing: {os.path.basename(playlist_path)}")
    print(f"{'='*70}")

    # Read original lines
    with open(playlist_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tracks = parse_m3u(playlist_path)

    if not tracks:
        print("No tracks found in this playlist.")
        return

    print(f"Found {len(tracks)} tracks.\n")

    resolved_count = 0
    unresolved = []

    # We will build a new list of lines to write back
    new_lines = lines.copy()

    for idx, track in enumerate(tracks, 1):
        artist = track["artist"]
        title = track["title"]
        album = track["album"]
        original_path = track["path"]
        line_index = track["original_line"] + 1  # path is always next line

        real_path = find_real_track_path(MUSIC_ROOT, artist, album, title, HYBRID_MODE)

        if real_path:
            status = "RESOLVED"
            resolved_count += 1

            # Replace the path line in the playlist
            new_lines[line_index] = f"./{real_path}\n"

            debug(f"  {original_path} → {real_path}")
        else:
            status = "NOT FOUND"
            unresolved.append((artist, title, original_path))
            debug(f"  Could not resolve: {artist}/{album}/{title}")

        print(f"[{idx:3d}/{len(tracks)}] {status} - {artist} - {title}")

    # Write updated playlist back to disk
    with open(playlist_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"\n{'-'*70}")
    print(f"Summary: {resolved_count}/{len(tracks)} tracks resolved")

    if unresolved:
        print(f"\nUnresolved tracks ({len(unresolved)}):")
        for artist, title, path in unresolved:
            print(f"  - {artist} - {title} ({path})")


def main():
    # Declare globals at the start
    global MUSIC_ROOT, DEBUG, HYBRID_MODE
    
    parser = argparse.ArgumentParser(
        description="Resolve tracks in Spotify m3u playlists to local music files."
    )
    parser.add_argument(
        "playlist",
        nargs="?",
        help="Specific playlist file to process (without .m3u extension). "
             "If omitted, all playlists in playlists/ folder will be processed."
    )
    parser.add_argument(
        "--music-root",
        type=str,
        default=MUSIC_ROOT,
        help=f"Path to music library root (default: {MUSIC_ROOT})"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output"
    )
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="Enable hybrid mode (second-pass search)"
    )
    
    args = parser.parse_args()
    
    # Update globals if provided
    MUSIC_ROOT = args.music_root
    DEBUG = args.debug
    
    # Verify music root exists
    if not os.path.isdir(MUSIC_ROOT):
        print(f"ERROR: Music library root not found: {MUSIC_ROOT}")
        sys.exit(1)
    
    # Process playlist(s)
    playlists_dir = "playlists"
    
    if not os.path.isdir(playlists_dir):
        print(f"ERROR: playlists/ directory not found")
        sys.exit(1)
    
    if args.playlist:
        # Process single playlist
        playlist_name = args.playlist
        if not playlist_name.endswith(".m3u"):
            playlist_name += ".m3u"
        
        playlist_path = os.path.join(playlists_dir, playlist_name)
        
        if not os.path.isfile(playlist_path):
            print(f"ERROR: Playlist not found: {playlist_path}")
            sys.exit(1)
        
        resolve_playlist(playlist_path)
    else:
        # Process all playlists
        m3u_files = sorted([f for f in os.listdir(playlists_dir) if f.endswith(".m3u")])
        
        if not m3u_files:
            print("No .m3u files found in playlists/ folder")
            return
        
        print(f"Found {len(m3u_files)} playlists to process\n")
        
        for playlist_file in m3u_files:
            playlist_path = os.path.join(playlists_dir, playlist_file)
            resolve_playlist(playlist_path)
        
        print(f"\n{'='*70}")
        print(f"Completed processing {len(m3u_files)} playlists")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
