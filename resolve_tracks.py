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
from pathlib import Path

# ===== Configuration =====
MUSIC_ROOT = r""  # <-- change this to your music library root
DEBUG = True

def debug(*args):
    if DEBUG:
        print("[DEBUG]", *args)


# ===== Helper Functions (from sple.py) =====

def normalize_path(path):
    """Normalize slashes and lowercase for consistent matching."""
    path = path.replace("\\", "/")
    path = re.sub(r"/+", "/", path)
    return path.lower()


def normalize_text(s):
    """Normalize text for fuzzy matching (remove punctuation, lowercase)."""
    return re.sub(r"[^a-z0-9]", "", normalize_path(s))


def find_best_match(root, target):
    """Return the best matching folder inside root."""
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

        score = 0
        if norm_target in norm_folder:
            score += 2
        if norm_folder in norm_target:
            score += 1

        if score > best_score:
            best_score = score
            best = folder

    return best


def find_real_track_path(MUSIC_ROOT, artist, album, title):
    """Find a real track path in the music library based on metadata."""
    # Normalize MUSIC_ROOT
    MUSIC_ROOT = normalize_path(MUSIC_ROOT)

    # Candidate artist folders:
    candidates = []

    # 1. Try the actual artist
    artist_folder = find_best_match(MUSIC_ROOT, artist)
    if artist_folder:
        candidates.append(os.path.join(MUSIC_ROOT, artist_folder))

    # 2. Try "Various Artists" for compilations
    va_folder = find_best_match(MUSIC_ROOT, "Various Artists")
    if va_folder:
        candidates.append(os.path.join(MUSIC_ROOT, va_folder))

    # Normalize title for matching
    norm_title = normalize_text(title)

    # Search each candidate artist folder
    for artist_path in candidates:
        album_folder = find_best_match(artist_path, album)
        if not album_folder:
            continue

        album_path = os.path.join(artist_path, album_folder)

        # Look for matching track
        for fname in os.listdir(album_path):
            if not fname.lower().endswith((".flac", ".mp3", ".m4a", ".ogg", ".wav", ".opus")):
                continue

            norm_fname = normalize_text(fname)

            if norm_title in norm_fname:
                # Build relative path using actual folder names
                rel_artist = os.path.basename(artist_path)
                rel_album = album_folder
                rel_path = os.path.join(rel_artist, rel_album, fname)

                return normalize_path(rel_path)

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
                tracks.append({
                    "artist": artist,
                    "album": "Unknown Album",  # We don't extract this from m3u
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


# ===== Resolution & Reporting =====

def resolve_playlist(playlist_path):
    """Attempt to resolve all tracks in a playlist."""
    print(f"\n{'='*70}")
    print(f"Processing: {os.path.basename(playlist_path)}")
    print(f"{'='*70}")
    
    tracks = parse_m3u(playlist_path)
    
    if not tracks:
        print("No tracks found in this playlist.")
        return
    
    print(f"Found {len(tracks)} tracks.\n")
    
    resolved_count = 0
    unresolved = []
    
    for idx, track in enumerate(tracks, 1):
        artist = track["artist"]
        title = track["title"]
        album = track["album"]
        original_path = track["path"]
        
        # Try to resolve
        real_path = find_real_track_path(MUSIC_ROOT, artist, album, title)
        
        if real_path:
            status = "✓ RESOLVED"
            resolved_count += 1
            debug(f"  {original_path} → {real_path}")
        else:
            status = "✗ NOT FOUND"
            unresolved.append((artist, title, original_path))
            debug(f"  Could not resolve: {artist}/{album}/{title}")
        
        print(f"[{idx:3d}/{len(tracks)}] {status} - {artist} - {title}")
    
    # Summary
    print(f"\n{'-'*70}")
    print(f"Summary: {resolved_count}/{len(tracks)} tracks resolved")
    
    if unresolved:
        print(f"\nUnresolved tracks ({len(unresolved)}):")
        for artist, title, path in unresolved:
            print(f"  - {artist} - {title} ({path})")


def main():
    # Declare globals at the start
    global MUSIC_ROOT, DEBUG
    
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
