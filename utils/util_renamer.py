import os
import re
import shutil
import requests
import urllib.parse
from pathlib import Path

try:
    import pyperclip
except ImportError:  # Clipboard support is a convenience only
    pyperclip = None

from difflib import SequenceMatcher
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.panel import Panel
from rich.align import Align
from rich.rule import Rule
from rich import box
from rich.markup import escape
from utils.util_config import load_config
from utils.util_resolution import *

console = Console()

# Load config
VIDEO_EXTENSIONS = set(load_config()['renamer']['video_extensions'])
SIDECAR_EXTENSIONS = set(load_config()['renamer']['sidecar_extensions'])

# Cache for show IDs to avoid re-asking for the same show
# Key: (show_title, season) -> Value: imdb_id
SHOW_CACHE = {}

def get_movie_info(imdb_id):
    """
    Fetch movie title and release year by IMDb ID using Wikidata (P345).
    Returns (title, year_str). Either can be None on failure.
    """
    try:
        tt_id = imdb_id if imdb_id.startswith('tt') else f"tt{imdb_id}"

        # P577 (publication date) covers films; P580 (start time) covers TV series
        query = (
            "SELECT ?itemLabel (MIN(?y) AS ?minYear) WHERE { "
            f'?item wdt:P345 "{tt_id}". '
            "OPTIONAL { ?item wdt:P577 ?date. BIND(YEAR(?date) AS ?year) } "
            "OPTIONAL { ?item wdt:P580 ?startDate. BIND(YEAR(?startDate) AS ?startYear) } "
            "BIND(COALESCE(?year, ?startYear) AS ?y) "
            'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } '
            "} "
            "GROUP BY ?item ?itemLabel "
            "LIMIT 1"
        )

        response = requests.get(
            'https://query.wikidata.org/sparql',
            params={'query': query, 'format': 'json'},
            headers={'User-Agent': 'MassMediaConverter/1.0 (personal media tool)'},
            timeout=15,
        )
        response.raise_for_status()

        bindings = response.json().get('results', {}).get('bindings', [])
        if not bindings:
            return None, None

        row = bindings[0]
        title = row.get('itemLabel', {}).get('value')
        year = row.get('minYear', {}).get('value')

        if title:
            # Strip Wikidata disambiguators like "Inception (2010 film)"
            title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
            title = title.replace(":", "-")

        return title, year

    except Exception as e:
        console.print(f"[bold red]Error fetching movie info: {e}[/bold red]")
        return None, None


def get_episode_info(imdb_series_id, season_num, episode_num):
    """
    Fetch episode information using TVMaze API.
    Returns (series_title, episode_title, show_year, episode_year)
    """
    try:
        # Ensure ID has 'tt' prefix for TVMaze lookup
        if not imdb_series_id.startswith('tt'):
             imdb_series_id = f"tt{imdb_series_id}"

        # Step 1: Lookup Show by IMDb ID
        lookup_url = f"https://api.tvmaze.com/lookup/shows?imdb={imdb_series_id}"
        response = requests.get(lookup_url, timeout=10)
        
        if response.status_code != 200:
            console.print(f"[bold red]TVMaze lookup failed for {imdb_series_id}[/bold red]")
            return None, None, None, None
            
        show_data = response.json()
        tvmaze_id = show_data.get('id')
        series_title = show_data.get('name')
        
        # Get show premiere year
        premiered = show_data.get('premiered')
        show_year = premiered.split('-')[0] if premiered else None
        
        # Step 2: Fetch specific episode
        episode_url = f"https://api.tvmaze.com/shows/{tvmaze_id}/episodebynumber?season={season_num}&number={episode_num}"
        ep_response = requests.get(episode_url, timeout=10)
        
        if ep_response.status_code == 200:
            ep_data = ep_response.json()
            title = ep_data.get('name')
            
            # Get episode air year
            airdate = ep_data.get('airdate')
            episode_year = airdate.split('-')[0] if airdate else None
            
            # Clean title
            if title:
                title = title.replace(":", "-")
                
            return series_title, title, show_year, episode_year
        else:
            console.print(f"[bold yellow]TVMaze: Episode S{season_num}E{episode_num} not found.[/bold yellow]")
            return series_title, None, show_year, None

    except Exception as e:
        console.print(f"[bold red]Error fetching episode info from TVMaze: {e}[/bold red]")
        return None, None, None, None


def parse_filename(filename):
    """
    Extract title and year from filename.
    Returns (clean_title, year_str). year_str can be None.
    """
    # Try to find a year (1900-2099)
    match = re.search(r'\b(19|20)\d{2}\b', filename)
    year = None
    clean_title = filename

    if match:
        year = match.group(0)
        # Take everything before the year
        clean_title = filename[:match.start()]

    # Replace dots, underscores, parentheses, brackets with spaces
    clean_title = re.sub(r'[._\(\)\[\]]', ' ', clean_title)

    # Remove extra whitespace
    clean_title = " ".join(clean_title.split())

    return clean_title, year


def parse_tv_filename(filename):
    """
    Extract show title, season, and episode from filename.
    Returns (show_title, season, episode) or (None, None, None).
    """
    # Regex for SxxExx pattern (case insensitive)
    match = re.search(r'(.*?)[\._\s-]*[sS](\d{1,2})[eE](\d{1,2})', filename)
    
    if match:
        show_title_raw = match.group(1)
        season = int(match.group(2))
        episode = int(match.group(3))
        
        # Clean up show title
        clean_title = re.sub(r'[._\(\)\[\]]', ' ', show_title_raw)
        clean_title = " ".join(clean_title.split())
        
        return clean_title, season, episode
    
    return None, None, None


def search_movie_suggestion(title, year=None, kind_priority='feature'):
    """
    Search IMDb using the suggestion API (v2).
    Returns a list of results (up to 5) or None.
    """
    try:
        clean_query = title.lower()
        if not clean_query:
            return None

        first_char = clean_query[0]
        encoded_query = urllib.parse.quote(clean_query)

        url = f"https://v2.sg.media-imdb.com/suggestion/{first_char}/{encoded_query}.json"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None

        data = response.json()
        if 'd' not in data:
            return None

        results = []
        for item in data['d']:
            # We only care about items with a valid IMDb ID (starts with 'tt')
            if 'id' not in item or not item['id'].startswith('tt'):
                continue

            # Map to a structure compatible with our usage
            movie_data = {
                'title': item.get('l'),
                'year': str(item.get('y')) if item.get('y') else None,
                'movieID': item.get('id', '').replace('tt', ''),
                'kind': item.get('q')
            }
            results.append(movie_data)

        if not results:
            return None
        
        # Sort results: prioritize exact year match or kind match
        sorted_results = []
        
        # 1. Exact year match AND kind match
        if year:
            for movie in results:
                kind = movie.get('kind', '').lower()
                is_kind_match = False
                if kind_priority == 'tv series' and ('tv' in kind or 'series' in kind):
                    is_kind_match = True
                elif kind_priority == 'feature' and (kind == 'feature' or kind == 'tv movie'):
                    is_kind_match = True
                
                if movie['year'] == year and is_kind_match:
                    sorted_results.append(movie)
        
        # 2. Kind match (if not already added)
        for movie in results:
            if movie in sorted_results: continue
            
            kind = movie.get('kind', '').lower()
            if kind_priority == 'tv series' and ('tv' in kind or 'series' in kind):
                sorted_results.append(movie)
            elif kind_priority == 'feature' and (kind == 'feature' or kind == 'tv movie'):
                sorted_results.append(movie)
                
        # 3. Remaining results
        for movie in results:
            if movie not in sorted_results:
                sorted_results.append(movie)

        return sorted_results[:5]

    except Exception as e:
        console.print(f"[bold red]Error searching IMDb API: {e}[/bold red]")
        return None


def sanitize_component(name):
    """Remove characters that are invalid in file/folder names and tidy whitespace."""
    if not name:
        return ""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = " ".join(name.split())
    # Windows does not allow names to end with a space or a dot
    return name.strip(" .")


def display_path(path):
    """Return a path relative to the working directory when possible."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def unique_path(dest_dir, filename, collision_format):
    """Return a path inside dest_dir that does not exist yet.

    The configured collision suffix (e.g. " ({n})") is inserted before the
    extension when the same name is already taken.
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    number = 2
    while True:
        if "{n}" in collision_format:
            addition = collision_format.replace("{n}", str(number))
        else:
            addition = f"{collision_format}{number}"
        candidate = dest_dir / f"{stem}{addition}{suffix}"
        if not candidate.exists():
            return candidate
        number += 1


def move_file(src, dest_dir, new_name, log_file, current_config, announce=True):
    """Move `src` into `dest_dir` using `new_name`, never overwriting anything.

    Returns the final destination path, or None if the move failed.
    """
    new_name = sanitize_component(new_name)
    if not new_name:
        console.print(f"[bold red]Cannot move {escape(src.name)}: resulting name is empty.[/bold red]")
        return None

    dest_dir = Path(dest_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        collision_format = current_config['renamer'].get('collision_suffix', ' ({n})')
        dest = unique_path(dest_dir, new_name, collision_format)
        shutil.move(str(src), str(dest))
    except OSError as e:
        console.print(f"[bold red]Error moving file: {e}[/bold red]")
        return None

    shown = display_path(dest)
    log_file.write(f"{shown}\n")
    log_file.flush()  # Ensure it's written to disk immediately

    if announce:
        console.print(Panel(f"[bold green]{escape(shown)}[/bold green]", title="Renamed Success", border_style="green"))
    else:
        console.print(f"  [green]+ {escape(shown)}[/green]")

    return dest


def find_sidecars(video_path, sidecar_extensions):
    """Find subtitle/metadata files that belong to a video.

    Matches "Movie.srt" and language-tagged files such as "Movie.en.srt".
    Returns a list of (path, remainder) tuples, where remainder is the
    ".en" style part that must be preserved in the new filename.
    """
    stem = video_path.stem
    matches = []

    try:
        entries = list(video_path.parent.iterdir())
    except OSError:
        return matches

    for entry in entries:
        if not entry.is_file() or entry == video_path:
            continue
        if entry.suffix.lower() not in sidecar_extensions:
            continue

        entry_stem_lower = entry.stem.lower()
        stem_lower = stem.lower()
        if entry_stem_lower == stem_lower:
            remainder = ""
        elif entry_stem_lower.startswith(stem_lower + "."):
            remainder = entry.stem[len(stem):]
        else:
            continue

        matches.append((entry, remainder))

    return matches


def move_sidecars(source_video, moved_video, log_file, current_config):
    """Move sidecars next to the renamed video, keeping their extra suffixes."""
    moved = []
    for sidecar, remainder in find_sidecars(source_video, SIDECAR_EXTENSIONS):
        new_name = f"{moved_video.stem}{remainder}{sidecar.suffix}"
        dest = move_file(sidecar, moved_video.parent, new_name, log_file,
                         current_config, announce=False)
        if dest:
            moved.append(dest)
    return moved


def build_show_folder(output_root, show_name, imdb_id, season, episode, resolution, current_config):
    """Build (but do not create) the show/season folder for a TV episode."""
    renamer = current_config['renamer']
    folder_fmt = renamer.get('show_folder_format', "[SHOW_NAME] [[IMDB_ID]] - [RESOLUTION]")
    season_fmt = renamer.get('season_folder_format', "Staffel [SEASON]")

    def fill(fmt):
        return fmt.replace("[SHOW_NAME]", show_name)\
                  .replace("[SEASON]", f"{season:02d}")\
                  .replace("[EPISODE]", f"{episode:02d}")\
                  .replace("[IMDB_ID]", imdb_id or "")\
                  .replace("[RESOLUTION]", resolution)

    folder_name = fill(folder_fmt)
    season_name = fill(season_fmt)

    # Drop an empty [IMDB_ID] placeholder when no ID was found
    folder_name = folder_name.replace("[]", "")
    if resolution == "Unknown":
        folder_name = re.sub(r'\s*-\s*Unknown\s*$', '', folder_name)

    folder_name = sanitize_component(" ".join(folder_name.split()))
    season_name = sanitize_component(" ".join(season_name.split()))

    if not folder_name:
        folder_name = "Unknown Show"
    if not season_name:
        season_name = f"Staffel {season:02d}"

    return Path(output_root) / folder_name / season_name


def format_movie_name(format_str, movie_name, year, imdb_id, resolution):
    """
    Replaces placeholders in the format string with actual movie data.
    """
    return format_str.replace("[MOVIE_NAME]", movie_name)\
                     .replace("[YEAR]", year)\
                     .replace("[IMDB_ID]", imdb_id)\
                     .replace("[RESOLUTION]", resolution)

def format_show_name(format_str, show_name, season, episode, episode_name, resolution, imdb_id, show_year, episode_year):
    """
    Replaces placeholders in the format string with actual show data.
    """
    # Default [YEAR] to show_year for backward compatibility
    year_val = show_year if show_year else "Unknown"
    ep_year_val = episode_year if episode_year else "Unknown"
    
    return format_str.replace("[SHOW_NAME]", show_name)\
                     .replace("[SEASON]", f"{season:02d}")\
                     .replace("[EPISODE]", f"{episode:02d}")\
                     .replace("[EPISODE_NAME]", episode_name)\
                     .replace("[RESOLUTION]", resolution)\
                     .replace("[IMDB_ID]", imdb_id)\
                     .replace("[YEAR]", year_val)\
                     .replace("[SHOW_YEAR]", year_val)\
                     .replace("[EPISODE_YEAR]", ep_year_val)

def check_similarity(name1, name2):
    """
    Check if two names are similar.
    """
    if not name1 or not name2:
        return False
    # Normalize: remove non-alphanumeric, lowercase
    n1 = re.sub(r'[\W_]+', '', name1).lower()
    n2 = re.sub(r'[\W_]+', '', name2).lower()
    
    if n1 == n2:
        return True
    
    # If one is contained in another (and length is reasonable)
    if len(n1) > 3 and len(n2) > 3:
        if n1 in n2 or n2 in n1:
            return True

    ratio = SequenceMatcher(None, n1, n2).ratio()
    return ratio >= 0.8

def process_file(file_path, output_root, log_file, current_config):
    """
    Process a single file: search/ask for ID, move it into output_root, and log.
    """
    # Skip if not a video file
    if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return

    filename = file_path.name

    console.print()
    console.print(Rule(style="cyan"))
    console.print(Panel(Align.center(f"[bold white]{escape(filename)}[/bold white]"), title="[bold cyan]Processing File[/bold cyan]", border_style="cyan", expand=False))

    # Check for TV Show pattern first
    tv_title, season, episode = parse_tv_filename(file_path.stem)
    
    is_tv_show = False
    if tv_title:
        is_tv_show = True
        search_title = tv_title
        search_year = None 
        console.print(f"[bold blue]📺 Detected TV Show:[/bold blue] {tv_title} S{season:02d}E{episode:02d}")
    else:
        # Parse as Movie
        search_title, search_year = parse_filename(file_path.stem)
        console.print(f"[bold magenta]🎬 Detected Movie:[/bold magenta] {search_title} ({search_year or 'Unknown Year'})")

    # Check cache for TV shows
    imdb_id = None
    selected_suggestion = None
    if is_tv_show:
        cache_key = (tv_title.lower(), season)
        if cache_key in SHOW_CACHE:
            imdb_id = SHOW_CACHE[cache_key]
            console.print(f"[bold green]Using cached ID for '{tv_title} S{season:02d}': {imdb_id}[/bold green]")

    # Check for existing IMDb ID in filename if not cached
    if not imdb_id:
        id_match = re.search(r'tt\d+', filename)
        if id_match:
            existing_imdb_id = id_match.group(0)
            console.print(f"[bold blue]Found IMDb ID in filename: {existing_imdb_id}[/bold blue]")
            
            with console.status("[bold yellow]Verifying ID...[/bold yellow]", spinner="dots"):
                fetched_title, fetched_year = get_movie_info(existing_imdb_id)
            
            if fetched_title:
                # Compare
                name_to_compare = tv_title if is_tv_show else search_title
                is_match = check_similarity(name_to_compare, fetched_title)
                
                # If it's a movie and we have years, check year too
                if is_match and not is_tv_show and search_year and fetched_year and fetched_year != 'Unknown':
                    try:
                        sy = int(search_year)
                        fy = int(fetched_year)
                        if abs(sy - fy) > 1:
                            is_match = False
                    except ValueError:
                        pass

                if is_match:
                    console.print(f"[bold green]ID verified: {fetched_title} ({fetched_year}) matches filename.[/bold green]")
                    imdb_id = existing_imdb_id
                else:
                    console.print(f"[bold red]Mismatch detected![/bold red]")
                    console.print(f"Filename suggests: {name_to_compare}")
                    console.print(f"ID {existing_imdb_id} is: {fetched_title} ({fetched_year})")
                    
                    choice = Prompt.ask("[bold yellow]Use ID from filename (1) or Search by Name (2)?[/bold yellow]", choices=["1", "2"], default="1")
                    if choice == "1":
                        imdb_id = existing_imdb_id
                    else:
                        imdb_id = None
            else:
                console.print("[bold red]Could not fetch info for existing ID. Proceeding to search.[/bold red]")

    if not imdb_id:
        # Copy the cleaned name to clipboard (only if a clipboard is available)
        if pyperclip is not None:
            try:
                pyperclip.copy(search_title)
            except Exception:
                pass

        # Efficiency Boost: Auto-search for the movie/show
        kind = 'tv series' if is_tv_show else 'feature'
        
        with console.status(f"[bold yellow]Searching IMDb for '{search_title}' (Year: {search_year or 'Any'})...[/bold yellow]", spinner="dots"):
            suggestions = search_movie_suggestion(search_title, search_year, kind_priority=kind)

        top_suggestion = None
        if suggestions:
            top_suggestion = suggestions[0]
            
            top_panel = Panel(
                 f"[bold green]{escape(top_suggestion['title'])}[/bold green] ({top_suggestion.get('year')})\n[blue]ID: tt{top_suggestion['movieID']}[/blue]",
                 title="[bold green]Top Suggestion[/bold green]",
                 border_style="green"
             )
            console.print(top_panel)
            
            table = Table(title="Other suggestions", box=box.ROUNDED, header_style="bold magenta")
            table.add_column("No.", style="cyan", no_wrap=True)
            table.add_column("Title", style="magenta")
            table.add_column("Year", style="green")
            table.add_column("Kind", style="yellow")
            table.add_column("ID", style="blue")

            for idx, s in enumerate(suggestions):
                table.add_row(str(idx+1), escape(s['title']), str(s.get('year')), s.get('kind'), f"tt{s['movieID']}")
            
            console.print(table)
            
            prompt_text = "[bold yellow]Enter IMDb ID, select number (1-5), or 'skip' [Press Enter to use #1][/bold yellow]"
        else:
            console.print("[bold yellow]No automatic match found.[/bold yellow]")
            prompt_text = "[bold yellow]Enter IMDb ID or 'skip'[/bold yellow]"

        user_input = Prompt.ask(prompt_text).strip()

        if user_input.lower() == "skip":
            console.print("[bold yellow]Skipping file as requested.[/bold yellow]")
            return

        # Determine which ID to use
        if not user_input and top_suggestion:
            selected_suggestion = top_suggestion
            imdb_id = f"tt{top_suggestion['movieID']}"
        elif user_input.isdigit():
            idx = int(user_input) - 1
            if suggestions and 0 <= idx < len(suggestions):
                selected_suggestion = suggestions[idx]
                imdb_id = f"tt{suggestions[idx]['movieID']}"
            else:
                console.print("[bold red]Invalid selection.[/bold red]")
                return
        elif user_input.startswith("tt"):
            imdb_id = user_input
        else:
            # Maybe user typed title? Assume they meant ID if it starts with tt, otherwise invalid
            console.print("[bold red]Invalid input. Please enter an ID or select a number.[/bold red]")
            return

    if imdb_id:
        # Cache the ID if it's a TV show
        if is_tv_show:
            cache_key = (tv_title.lower(), season)
            SHOW_CACHE[cache_key] = imdb_id

        resolution = get_resolution(str(file_path))
        if not resolution:
            # Fallback if resolution detection fails
            res_category = "Unknown"
        else:
            width, height = resolution
            res_category = get_category_label(width, height)

        if is_tv_show:
             # Fetch Episode Info
             with console.status("[bold yellow]Fetching Episode Info...[/bold yellow]", spinner="dots"):
                 series_title, ep_title, show_year, episode_year = get_episode_info(imdb_id, season, episode)

             # FIX: Ensure we never lose the show name
             if not series_title:
                 series_title = tv_title  # keep parsed filename title instead of None

             # Clean series title to avoid invalid characters
             if series_title:
                 series_title = sanitize_component(series_title.replace(":", "-"))
             if not series_title:
                 series_title = "Unknown Show"

             if current_config['renamer'].get('folder_mode', 'show_folders') == 'show_folders':
                 dest_dir = build_show_folder(output_root, series_title, imdb_id, season,
                                              episode, res_category, current_config)
             else:
                 dest_dir = Path(output_root)

             if ep_title:
                 # Construct TV Show filename
                 show_fmt = current_config['renamer'].get('show_format', "[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME]")
                 new_stem = format_show_name(show_fmt, series_title, season, episode, ep_title, res_category, imdb_id, show_year, episode_year)
             else:
                 console.print("[bold yellow]Could not fetch episode details.[/bold yellow]")
                 # Fallback: Rename without episode title if episode info is missing
                 # User might prefer this over skipping
                 new_stem = f"{series_title} - S{season:02d}E{episode:02d} - Unknown Episode"
                 console.print(f"Renaming with fallback title: {escape(new_stem + file_path.suffix)}")

             moved = move_file(file_path, dest_dir, new_stem + file_path.suffix,
                               log_file, current_config)
             if moved:
                 move_sidecars(file_path, moved, log_file, current_config)

        else:
            # Movie: reuse title/year from the selected suggestion when available,
            # otherwise look the ID up via Wikidata.
            original_title = release_year = None
            if selected_suggestion:
                original_title = selected_suggestion.get('title')
                release_year = selected_suggestion.get('year')

            if not (original_title and release_year):
                with console.status("[bold yellow]Fetching Movie Info...[/bold yellow]", spinner="dots"):
                    original_title, release_year = get_movie_info(imdb_id)

            if original_title:
                original_title = sanitize_component(original_title.replace(":", "-"))

            if original_title and release_year:
                # Construct the new filename
                movie_fmt = current_config['renamer'].get('movie_format', "[MOVIE_NAME] ([YEAR]) [[IMDB_ID]] - [RESOLUTION]")
                new_stem = format_movie_name(movie_fmt, original_title, release_year, imdb_id, res_category)
                moved = move_file(file_path, Path(output_root), new_stem + file_path.suffix,
                                  log_file, current_config)
                if moved:
                    move_sidecars(file_path, moved, log_file, current_config)
            else:
                console.print("[bold red]Movie not found or error occurred.[/bold red]")
    else:
        console.print("[bold yellow]No ID provided. Skipping.[/bold yellow]")


def _resolve_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def run_renamer():
    """
    Main function to rename video files based on IMDb metadata.

    Renamed files (and their sidecars) are moved into the configured
    renamer output folder, e.g. "out_renamer".
    """
    # Reload config in case it changed
    global VIDEO_EXTENSIONS, SIDECAR_EXTENSIONS
    config = load_config()
    VIDEO_EXTENSIONS = set(config['renamer']['video_extensions'])
    SIDECAR_EXTENSIONS = set(config['renamer']['sidecar_extensions'])

    input_dir = _resolve_path(config['paths']['renamer_input'])
    output_dir = _resolve_path(config['paths']['renamer_output'])

    console.print(Rule(title="[bold green]Media Renamer[/bold green]"))
    console.print(f"[bold]Input folder:[/bold]  {escape(str(input_dir))}")
    console.print(f"[bold]Output folder:[/bold] {escape(str(output_dir))}")

    if not input_dir.exists() or not input_dir.is_dir():
        console.print("[bold red]Error: Input folder does not exist.[/bold red]")
        return

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(f"[bold red]Error creating output folder: {e}[/bold red]")
        return

    # Ensure settings directory exists for the changelog
    os.makedirs('settings', exist_ok=True)
    changelog_path = Path('settings/renamer_changelog.txt')

    # Only top-level video files are processed; the output folder is never re-scanned.
    files = sorted(
        [f for f in input_dir.iterdir()
         if f.is_file() and not f.name.startswith('.') and f.suffix.lower() in VIDEO_EXTENSIONS],
        key=lambda x: x.name.lower()
    )

    if not files:
        console.print("[bold yellow]No video files found to rename.[/bold yellow]")
        return

    # Open log file using 'with' to ensure it closes
    with open(changelog_path, "a", encoding="utf-8") as log_out:
        for file_path in files:
            process_file(file_path, output_dir, log_out, config)

    console.print(f"\n[bold green]All files processed. Changelog saved to {changelog_path}.[/bold green]")
