from playwright.sync_api import sync_playwright
import time
import random
import re
import os
from resolve_tracks import find_real_track_path

USER_PROFILE_URL = "" #"https://open.spotify.com/user/XXXXX/playlists" OR "https://open.spotify.com/playlist/XXXXXXXXXXXXXXXX" # <-- change this to your Spotify playlists page or a specific playlist URL
MUSIC_ROOT = r""  # <-- change this to your music library root
HYBRID_MODE = True  # Enable second-pass search if normal album match fails. This will search all albums by the artist for the track if the album match fails ##currently not in use all seaches are hybrid mode
DEBUG = True

def debug(*args):
    if DEBUG:
        print("[DEBUG]", *args)

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

def human_delay(min_s=0.3, max_s=0.8):
    time.sleep(random.uniform(min_s, max_s))

def scroll_with_keys(page):
    debug("Attempting keyboard-based scrolling.")

    # Ensure the page is focused
    page.bring_to_front()
    page.keyboard.press("Home")  # Go to top first
    time.sleep(0.5)

    last_count = -1
    stable_rounds = 0

    while stable_rounds < 3:
        # Press PageDown multiple times with delays
        for _ in range(4):
            page.keyboard.press("PageDown")
            time.sleep(random.uniform(0.5, 1.0))

        # Wait a bit for content to load
        time.sleep(random.uniform(1.0, 2.0))

        # Check if more tracks appeared
        links = page.query_selector_all("a[href*='/track/']")
        count = len(links)
        debug(f"Visible track links after keyboard scroll: {count}")

        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0

        last_count = count

        # Safety check
        if stable_rounds >= 3:
            break

def scroll_slowly(page):
    debug("Using mouse wheel scrolling.")
    last_height = 0
    stable_rounds = 0
    max_scrolls = 20  # Prevent infinite scrolling
    scroll_count = 0

    while stable_rounds < 3 and scroll_count < max_scrolls:
        # Scroll down with mouse wheel
        page.mouse.wheel(0, random.randint(1000, 1500))
        time.sleep(random.uniform(0.5, 1.0))

        new_height = page.evaluate("document.body.scrollHeight")
        scroll_count += 1

        links = page.query_selector_all("a[href*='/track/']")
        count = len(links)
        debug(f"Mouse scroll {scroll_count}: visible tracks = {count}, page height = {new_height}")

        if new_height == last_height:
            stable_rounds += 1
        else:
            stable_rounds = 0

        last_height = new_height

def extract_playlist_urls(page):
    print("Loading user profile…")
    page.goto(USER_PROFILE_URL, timeout=60000)
    human_delay()

    scroll_slowly(page)

    print("Extracting playlist URLs…")
    links = page.query_selector_all("a[href*='/playlist/']")
    raw_urls = {link.get_attribute("href") for link in links}

    BASE = "https://open.spotify.com"
    urls = [
        url if url.startswith("http") else BASE + url
        for url in raw_urls
    ]

    print(f"Found {len(urls)} playlists")
    return urls

def extract_playlist_name(page):
    # 1. Try og:title
    og = page.query_selector("meta[property='og:title']")
    if og:
        name = og.get_attribute("content")
        if name and name.strip():
            return name.strip()

    # 2. Try <title>
    title_el = page.query_selector("title")
    if title_el:
        name = title_el.inner_text().strip()
        if name:
            # Strip " | Spotify" suffix
            name = name.replace("| Spotify", "").strip()
            return name

    # 3. Try h1
    h1 = page.query_selector("h1[data-encore-id='text']")
    if h1:
        name = h1.inner_text().strip()
        if name:
            return name

    # 4. Try role="heading"
    heading = page.query_selector("[role='heading']")
    if heading:
        name = heading.inner_text().strip()
        if name:
            return name

    # 5. Try first large text block above tracklist
    candidates = page.query_selector_all("div[data-encore-id='text']")
    for c in candidates:
        txt = c.inner_text().strip()
        if len(txt) > 3 and "spotify" not in txt.lower():
            return txt

    return "Unknown Playlist"


def safe_goto(page, url, retries=3):
    debug(f"Navigating to: {url}")

    for attempt in range(1, retries + 1):
        try:
            page.goto(url, wait_until='networkidle')
            human_delay()
            debug(f"Final URL after navigation: {page.url}")

            # Try to detect playlist page by selector, not by HTML text
            try:
                page.wait_for_selector("div[data-testid='playlist-tracklist']", timeout=15000)
                debug("Detected desktop playlist layout.")
                
                return True
            except:
                try:
                    page.wait_for_selector("div[class*='standalone-ellipsis-one-line']", timeout=15000)
                    debug("Detected mobile playlist layout.")
                    return True
                except:
                    debug("No playlist layout detected.")

            # Only print HTML preview AFTER selector check
            html = page.content()
            debug(f"HTML length: {len(html)}")
            debug("HTML preview:", html[:500].replace("\n", " "))

            # Soft checks (informational only)
            if "login" in page.url:
                debug("Possible login redirect.")
            if "consent" in html.lower():
                debug("Possible cookie/consent banner.")
            if "429" in html:
                debug("Possible rate limit page.")

        except Exception as e:
            debug(f"Navigation error: {e}")

        debug(f"Retrying ({attempt}/{retries})…")
        time.sleep(random.uniform(1.0, 2.0))

    debug("Failed to load playlist page after retries.")
    return False

def get_track_count(page):
    """
    Extracts the total number of songs from the playlist header.
    Looks for spans like: <span data-encore-id="text">77 songs</span>
    """
    spans = page.query_selector_all("span[data-encore-id='text']")
    for s in spans:
        txt = s.inner_text().strip().lower()
        if "song" in txt:  # matches "77 songs", "1 song"
            m = re.search(r"(\d+)", txt)
            if m:
                return int(m.group(1))
    return 0


def get_visible_track_hrefs(page):
    hrefs = set()
    for link in page.query_selector_all("a[href*='/track/']"):
        href = link.get_attribute("href")
        if href:
            hrefs.add(href.split("?")[0])
    return hrefs


def collect_visible_tracks(page, seen, tracks):
    """Collect unique track metadata from all currently visible track links."""
    for link in page.query_selector_all("a[href*='/track/']"):
        href = link.get_attribute("href")
        if not href:
            continue

        normalized = href.split("?")[0]
        if normalized in seen:
            continue

        seen.add(normalized)

        title_el = link.query_selector("div[data-encore-id='text']")
        title = title_el.inner_text().strip() if title_el else "Unknown Title"

        artist_el = link.evaluate_handle("""
            (node) => {
                const parent = node.closest('div');
                if (!parent) return null;
                return parent.querySelector("a[href*='/artist/']");
            }
        """)
        artist = artist_el.inner_text().strip() if artist_el else "Unknown Artist"

        row_el = link.evaluate_handle("node => node.closest('[role=row]')").as_element()
        album = "Unknown Album"
        if row_el:
            album_el = row_el.query_selector("a[href*='/album/']")
            album = album_el.inner_text().strip() if album_el else album

        tracks.append((artist, album, title))

    return len(seen)


def get_playlist_scroll_container(page):
    """
    Returns the actual scrollable container for a Spotify playlist page.
    """
    debug("Searching for scrollable container...")

    # Find potentially scrollable elements
    scrollable_elements = page.evaluate("""
        () => {
            const elements = [];
            const allElements = document.querySelectorAll('*');

            for (let el of allElements) {
                const style = window.getComputedStyle(el);
                const isScrollable = (style.overflow === 'auto' || style.overflow === 'scroll' ||
                                    style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                                   (el.scrollHeight > el.clientHeight);

                if (isScrollable) {
                    elements.push({
                        tag: el.tagName,
                        id: el.id,
                        className: el.className,
                        scrollHeight: el.scrollHeight,
                        clientHeight: el.clientHeight,
                        dataset: Object.keys(el.dataset),
                        rect: el.getBoundingClientRect()
                    });
                }
            }

            return elements.slice(0, 10); // Limit to first 10 to avoid too much output
        }
    """)

    debug(f"Found {len(scrollable_elements)} scrollable elements:")
    for i, el in enumerate(scrollable_elements):
        debug(f"  {i}: {el['tag']} .{el['className']} #{el['id']} (scrollHeight: {el['scrollHeight']}, clientHeight: {el['clientHeight']})")

    # Try the specific tracklist parent container first
    tracklist = page.query_selector("div[data-testid='playlist-tracklist']")
    if tracklist:
        container = tracklist.evaluate_handle("(el) => el.parentElement.parentElement")
        if container:
            debug("Using tracklist parent container")
            return container

    # Look for main content area
    main_content = page.query_selector("main") or page.query_selector("[data-testid='main']")
    if main_content:
        debug("Using main content area")
        return main_content

    # Fallback to main view container
    container = page.query_selector("div.main-view-container")
    if container:
        debug("Using main-view-container")
        return container

    # Last resort: the body element
    debug("Using body as last resort")
    return page.query_selector("body")


def scroll_playlist(page, track_count):
    """
    Scrolls the playlist until no new tracks load, trying different scrollable elements.
    Returns a set of unique track hrefs currently visible.
    """
    debug("Finding scrollable elements and testing which one loads tracks.")

    seen_links = set()
    collected_tracks = []

    def _collect_current():
        nonlocal seen_links, collected_tracks
        num = collect_visible_tracks(page, seen_links, collected_tracks)
        return num

    active_links = get_visible_track_hrefs(page)
    last_count = len(active_links)
    debug(f"Initial unique track links: {last_count}")

    # Capture initial view metadata
    _collect_current()

    def _scroll_until_stable(container_handle, max_scrolls=20, stable_limit=4):
        nonlocal active_links, last_count, seen_links
        stable_rounds = 0
        scroll_attempts = 0

        while scroll_attempts < max_scrolls and stable_rounds < stable_limit and len(active_links) < track_count:
            container_handle.evaluate("el => { el.scrollTop += el.clientHeight * 0.8; }")
            time.sleep(random.uniform(1.2, 2.0))

            current_links = get_visible_track_hrefs(page)
            current_count = len(current_links)
            debug(f"Scroll {scroll_attempts + 1}: unique tracks = {current_count}")

            # Union existing and currently visible links (avoid lost-virtualized rows)
            active_links |= current_links

            collect_visible_tracks(page, seen_links, collected_tracks)
            active_count = len(active_links)

            if active_count > last_count:
                last_count = active_count
                stable_rounds = 0
            else:
                stable_rounds += 1

            scroll_attempts += 1

            if active_count >= track_count:
                debug(f"Reached expected unique track count: {active_count}/{track_count}")
                break

        return len(active_links)

    container = get_playlist_scroll_container(page)
    if container:
        debug("Using selected playlist scroll container")
        _scroll_until_stable(container, max_scrolls=25, stable_limit=4)

    if len(active_links) < track_count:
        debug("Primary container did not load all tracks; scanning candidates.")

        scrollable_elements = page.evaluate("""
            () => {
                const elements = [];
                const allElements = document.querySelectorAll('*');

                for (let el of allElements) {
                    const style = window.getComputedStyle(el);
                    const isScrollable = (style.overflow === 'auto' || style.overflow === 'scroll' ||
                                        style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                                       (el.scrollHeight > el.clientHeight) &&
                                       el.scrollHeight > 1000;

                    if (isScrollable) {
                        let priority = 0;
                        if (el.tagName === 'MAIN') priority += 10;
                        if (el.matches('[data-testid*="main"]')) priority += 8;
                        if (el.matches('[data-testid*="playlist"]')) priority += 6;
                        if (el.classList.contains('main-view-container')) priority += 5;
                        if (el.scrollHeight > el.clientHeight * 3) priority += 3;

                        const identifier = `${el.tagName}.${Array.from(el.classList).join('.')}.${el.id}`;
                        elements.push({
                            identifier, tag: el.tagName, id: el.id,
                            className: Array.from(el.classList).join('.'),
                            scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
                            priority
                        });
                    }
                }

                const unique = [];
                const seen = new Set();
                for (let candidate of elements.sort((a, b) => b.priority - a.priority)) {
                    if (!seen.has(candidate.identifier)) {
                        unique.push(candidate);
                        seen.add(candidate.identifier);
                    }
                }

                return unique.slice(0, 5);
            }
        """)

        debug(f"Candidate scroll elements: {len(scrollable_elements)}")

        for candidate in scrollable_elements:
            if len(active_links) >= track_count:
                break

            element_handle = page.evaluate_handle(f"""
                () => {{
                    return Array.from(document.querySelectorAll('*')).find(el => {{
                        const style = window.getComputedStyle(el);
                        const isScrollable = (style.overflow === 'auto' || style.overflow === 'scroll' ||
                                            style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                                           (el.scrollHeight > el.clientHeight) &&
                                           el.scrollHeight > 1000;

                        if (!isScrollable) return false;
                        const identifier = `${{el.tagName}}.${{Array.from(el.classList).join('.')}}.${{el.id}}`;
                        return identifier === "{candidate['identifier']}";
                    }});
                }}
            """)

            if not element_handle:
                continue

            _scroll_until_stable(element_handle, max_scrolls=15, stable_limit=3) #set max_scrolls for playlist

    debug(f"Scroll done, unique tracks = {len(active_links)}/{track_count}")
    return active_links, collected_tracks


def scrape_playlist(page, url):
    print(f"\nLoading playlist: {url}")

    if not safe_goto(page, url):
        print("Failed to load playlist after retries.")
        return None, []

    # Wait for playlist container
    try:
        page.wait_for_selector("div[data-testid='playlist-tracklist']", timeout=60000)
    except:
        print("Playlist page did not load correctly.")
        return None, []

    # Extract playlist name
    playlist_name = extract_playlist_name(page)
    debug(f"Playlist name detected: {playlist_name}")

    # Extract track count
    track_count = get_track_count(page)
    debug(f"Playlist reports {track_count} tracks.")

    # Scroll only if needed (will happen below in the capture phase)
    # ---- INCREMENTAL CAPTURE LOOP (UNIQUE TRACKS ONLY) ----
    if track_count > 30:
        visible_uris, tracks = scroll_playlist(page, track_count)

        if len(visible_uris) >= track_count:
            debug(f"All playlist links loaded: {len(visible_uris)}/{track_count}")
        else:
            debug(f"Partial load: {len(visible_uris)}/{track_count} (will use what is available)")

        # If no metadata was captured during scroll (edge case), collect now
        if len(tracks) == 0:
            seen = set()
            tracks = []
            collect_visible_tracks(page, seen, tracks)

    else:
        seen = set()
        tracks = []
        collect_visible_tracks(page, seen, tracks)

    debug(f"Extracted {len(tracks)} total tracks (seen {len(tracks)} unique hrefs).")
    return playlist_name, tracks

def write_m3u(playlist_name, tracks, MUSIC_ROOT):
    OUTPUT_DIR = "playlists"
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    safe_name = sanitize_filename(playlist_name)
    filepath = os.path.join(OUTPUT_DIR, safe_name + ".m3u")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for artist, album, title in tracks:
            safe_artist = sanitize_filename(artist)
            safe_album = sanitize_filename(album)
            safe_title = sanitize_filename(title)

            # Remove album name from title if it appears at the end (Spotify sometimes includes it)
            if safe_album and safe_title.lower().endswith(safe_album.lower()):
                safe_title = safe_title[:-len(safe_album)].rstrip(' -').rstrip()

            # Try to find the real file (with track number + correct extension)
            real_path = find_real_track_path(
                MUSIC_ROOT,
                safe_artist,
                safe_album,
                safe_title,
                HYBRID_MODE
            )

            # Write EXTINF
            f.write(f"#EXTINF:0,{artist} - {title}\n")

            if real_path:
                # Found the real file — use it
                f.write(f"./{real_path}\n")
            else:
                # Not found — write fallback and warn
                fallback = f"./{safe_artist}/{safe_album}/{safe_title}.flac"
                f.write(fallback + "\n")
                # Print unresolved track info so the user can see fail-to-map entries
                debug(f"UNRESOLVED: {artist} - {title} (album: {album})")

    print(f"Saved playlist: {filepath}")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
        )

        if DEBUG:
            def maybe_debug_response(response):
                if response.status != 200:
                    debug(f"HTTP {response.status} → {response.url}")

            page.on("response", maybe_debug_response)

        playlist_urls = extract_playlist_urls(page)

        for url in playlist_urls:
            playlist_name, tracks = scrape_playlist(page, url)
            if playlist_name:
                print(f"Found {len(tracks)} tracks in '{playlist_name}'")
                write_m3u(playlist_name, tracks, MUSIC_ROOT)

        browser.close()
