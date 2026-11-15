import copy
import json
import os
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.align import Align
from rich.markup import escape
from utils.util_colors import clear_screen
from utils.util_editions import normalize_edition_key

console = Console()

CONFIG_FILE = 'settings/config.json'
CONFIG_VERSION = 2

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "paths": {
        # All paths are relative to the folder the app is started from (the project root).
        "renamer_input": ".",
        "renamer_output": "out_renamer",
        "converter_input": "out_renamer",
        "converter_output": "out_converter"
    },
    "converter": {
        "convert_to": "mp4",
        "threads": 5,
        "resolution": "original",  # Options: original, 1080p, 720p, 480p
        "scaling_mode": "both",    # Options: both, downscale_only, upscale_only
        "mirror_input_structure": True,
        "skip_existing": True,
        "collision_suffix": " ({n})"
    },
    "renamer": {
        "video_extensions": [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"],
        "sidecar_extensions": [".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".nfo", ".jpg", ".jpeg", ".png", ".webp"],
        "movie_format": "[MOVIE_NAME] ([YEAR]) [[IMDB_ID]] - [EDITION] - [RESOLUTION]",
        "show_format": "[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME] - [EDITION]",
        "show_folder_format": "[SHOW_NAME] [[IMDB_ID]] - [RESOLUTION]",
        "season_folder_format": "Staffel [SEASON]",
        "folder_mode": "show_folders",  # Options: show_folders, flat
        "collision_suffix": " ({n})",
        "edition_tags": ["Director's Cut", "Extended", "Remastered", "Uncut"]
    }
}


def _deep_merge(defaults, loaded):
    """Return `loaded` with every key that is missing filled in from `defaults`."""
    if not isinstance(loaded, dict):
        return copy.deepcopy(defaults)

    result = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _insert_edition_placeholder(format_str):
    """Add [EDITION] to a legacy format string exactly once."""
    if '[EDITION]' in format_str:
        return format_str
    if not format_str.strip():
        return '[EDITION]'
    if '[RESOLUTION]' in format_str:
        return format_str.replace('[RESOLUTION]', '[EDITION] - [RESOLUTION]', 1)
    return format_str.rstrip(' -') + ' - [EDITION]'


def _migrate_config(config):
    """Move keys from older config layouts to their current location."""
    converter = config.get('converter')
    renamer = config.get('renamer')

    # Older builds stored the show/season folder formats under "converter".
    if isinstance(converter, dict) and isinstance(renamer, dict):
        for key in ('show_folder_format', 'season_folder_format'):
            if key in converter:
                if key not in renamer:
                    renamer[key] = converter[key]
                del converter[key]

    # v2 introduced the [EDITION] placeholder. Insert it once into formats
    # from older configs; removing it later is respected because the version
    # is already at CONFIG_VERSION.
    version = config.get('config_version', 1)
    if not isinstance(version, int) or version < CONFIG_VERSION:
        if isinstance(renamer, dict):
            for key in ('movie_format', 'show_format'):
                fmt = renamer.get(key)
                if isinstance(fmt, str):
                    renamer[key] = _insert_edition_placeholder(fmt)
        config['config_version'] = CONFIG_VERSION


def _normalize_edition_tags(values):
    if not isinstance(values, list):
        return list(DEFAULT_CONFIG['renamer']['edition_tags'])

    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = " ".join(value.split())
        key = normalize_edition_key(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _normalize_extensions(values):
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip().lower()
        if not value:
            continue
        if not value.startswith('.'):
            value = f'.{value}'
        result.append(value)
    return result


def _validate_config(config):
    """Clamp/normalize values that would otherwise break the pipeline."""
    version = config.get('config_version')
    if not isinstance(version, int) or version < CONFIG_VERSION:
        config['config_version'] = CONFIG_VERSION
    renamer = config.setdefault('renamer', {})
    converter = config.setdefault('converter', {})
    paths = config.setdefault('paths', {})

    renamer['video_extensions'] = _normalize_extensions(renamer.get('video_extensions', []))
    renamer['sidecar_extensions'] = _normalize_extensions(renamer.get('sidecar_extensions', []))

    if 'edition_tags' not in renamer:
        renamer['edition_tags'] = list(DEFAULT_CONFIG['renamer']['edition_tags'])
    else:
        renamer['edition_tags'] = _normalize_edition_tags(renamer['edition_tags'])

    for key in ('movie_format', 'show_format', 'show_folder_format', 'season_folder_format'):
        if not isinstance(renamer.get(key), str) or not renamer[key].strip():
            renamer[key] = DEFAULT_CONFIG['renamer'][key]

    if renamer.get('folder_mode') not in ('show_folders', 'flat'):
        renamer['folder_mode'] = 'show_folders'

    for key in ('collision_suffix',):
        if not isinstance(renamer.get(key), str) or not renamer[key]:
            renamer[key] = DEFAULT_CONFIG['renamer'][key]
        # A suffix without the {n} placeholder would produce the same name forever.
        if '{n}' not in renamer[key]:
            renamer[key] += '{n}'

    if not isinstance(converter.get('collision_suffix'), str) or not converter['collision_suffix']:
        converter['collision_suffix'] = DEFAULT_CONFIG['converter']['collision_suffix']
    if '{n}' not in converter['collision_suffix']:
        converter['collision_suffix'] += '{n}'

    try:
        converter['threads'] = max(1, int(converter.get('threads', 5)))
    except (TypeError, ValueError):
        converter['threads'] = 5

    if not isinstance(converter.get('convert_to'), str) or not converter['convert_to'].strip():
        converter['convert_to'] = DEFAULT_CONFIG['converter']['convert_to']
    else:
        converter['convert_to'] = converter['convert_to'].strip().lstrip('.').lower()

    for key, default in (
        ('mirror_input_structure', True),
        ('skip_existing', True),
    ):
        if not isinstance(converter.get(key), bool):
            converter[key] = default

    for key, default in DEFAULT_CONFIG['paths'].items():
        if not isinstance(paths.get(key), str) or not paths[key].strip():
            paths[key] = default

    return config


def load_config():
    os.makedirs('settings', exist_ok=True)

    if not os.path.exists(CONFIG_FILE):
        config = copy.deepcopy(DEFAULT_CONFIG)
        save_config(config)
        return config

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception as e:
        console.print(f"[bold red]Error loading config: {e}[/bold red]")
        return copy.deepcopy(DEFAULT_CONFIG)

    if not isinstance(raw, dict):
        console.print("[bold red]Error loading config: expected a JSON object.[/bold red]")
        return copy.deepcopy(DEFAULT_CONFIG)

    _migrate_config(raw)
    return _validate_config(_deep_merge(DEFAULT_CONFIG, raw))


def save_config(config):
    try:
        os.makedirs('settings', exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        console.print(f"[bold red]Error saving config: {e}[/bold red]")


def _ask_bool(label, current):
    answer = Prompt.ask(label, choices=["y", "n"], default="y" if current else "n")
    return answer.lower() == "y"


def _ask_text(label, current, preserve_space=False):
    if preserve_space:
        # console.input keeps leading/trailing spaces, Prompt.ask does not.
        console.print(f"{label} [dim](current:[/dim] {escape(str(current))}[dim])[/dim]")
        value = console.input("[bold green]>>> [/bold green]")
        return str(current) if value == "" else value
    value = Prompt.ask(label, default=str(current)).strip()
    return value or str(current)


def edit_config_menu():
    config = load_config()
    while True:
        clear_screen()

        paths = config['paths']
        renamer = config['renamer']
        converter = config['converter']

        menu_text = (
            f"\n[bold cyan]1.[/bold cyan] Converter: Output Format ([bold green]{escape(str(converter['convert_to']))}[/bold green])\n"
            f"[bold cyan]2.[/bold cyan] Converter: Threads ([bold green]{converter['threads']}[/bold green])\n"
            f"[bold cyan]3.[/bold cyan] Converter: Resolution ([bold green]{converter['resolution']}[/bold green])\n"
            f"[bold cyan]4.[/bold cyan] Converter: Scaling Mode ([bold green]{converter['scaling_mode']}[/bold green])\n"
            f"[bold cyan]5.[/bold cyan] Converter: Mirror Input Structure ([bold green]{converter['mirror_input_structure']}[/bold green])\n"
            f"[bold cyan]6.[/bold cyan] Converter: Skip Existing Outputs ([bold green]{converter['skip_existing']}[/bold green])\n"
            f"[bold cyan]7.[/bold cyan] Converter: Collision Suffix ([bold green]{escape(str(converter['collision_suffix']))}[/bold green])\n"
            f"[bold cyan]8.[/bold cyan] Renamer: Extensions\n"
            f"[bold cyan]9.[/bold cyan] Renamer: Sidecar Extensions\n"
            f"[bold cyan]10.[/bold cyan] Renamer: Movie Format\n"
            f"[bold cyan]11.[/bold cyan] Renamer: TV Show Format\n"
            f"[bold cyan]12.[/bold cyan] Renamer: Show Folder Format\n"
            f"[bold cyan]13.[/bold cyan] Renamer: Season Folder Format\n"
            f"[bold cyan]14.[/bold cyan] Renamer: Edition Tags ([bold green]{escape(', '.join(renamer['edition_tags'])) or 'disabled'}[/bold green])\n"
            f"[bold cyan]15.[/bold cyan] Renamer: Folder Mode ([bold green]{renamer['folder_mode']}[/bold green])\n"
            f"[bold cyan]16.[/bold cyan] Renamer: Collision Suffix ([bold green]{escape(str(renamer['collision_suffix']))}[/bold green])\n"
            f"[bold cyan]17.[/bold cyan] Paths: Renamer Input ([bold green]{escape(str(paths['renamer_input']))}[/bold green])\n"
            f"[bold cyan]18.[/bold cyan] Paths: Renamer Output ([bold green]{escape(str(paths['renamer_output']))}[/bold green])\n"
            f"[bold cyan]19.[/bold cyan] Paths: Converter Input ([bold green]{escape(str(paths['converter_input']))}[/bold green])\n"
            f"[bold cyan]20.[/bold cyan] Paths: Converter Output ([bold green]{escape(str(paths['converter_output']))}[/bold green])\n"
            f"[bold yellow]21.[/bold yellow] Save & Back\n"
        )

        panel = Panel(
            Align.center(menu_text),
            title="[bold magenta]Configuration Menu[/bold magenta]",
            subtitle="[italic dim]Customize your settings[/italic dim]",
            border_style="bright_blue",
            padding=(1, 4),
            expand=False
        )

        # Vertical centering
        panel_height = menu_text.count("\n") + 4
        top_padding = max(0, (console.height - panel_height) // 2)

        console.print("\n" * top_padding)
        console.print(Align.center(panel))

        console.print() # Add some spacing

        console.print(Align.center("[bold yellow]Select an option[/bold yellow]"))

        # Center the prompt input cursor roughly
        prompt_padding = " " * max(0, (console.width // 2 - 2))
        choices = [str(i) for i in range(1, 22)]
        choice = Prompt.ask(f"{prompt_padding}[bold green]>>>[/bold green]", choices=choices, show_choices=False)

        if choice == '1':
            new_format = Prompt.ask("Enter new output format (e.g., mp4, mkv)")
            if new_format:
                config['converter']['convert_to'] = new_format.strip().lstrip('.').lower()
        elif choice == '2':
            new_threads = Prompt.ask("Enter number of threads")
            if new_threads.isdigit() and int(new_threads) > 0:
                config['converter']['threads'] = int(new_threads)
        elif choice == '3':
            console.print("Options: [green]original[/green], [green]1080p[/green], [green]720p[/green], [green]480p[/green]")
            new_res = Prompt.ask("Enter target resolution", choices=["original", "1080p", "720p", "480p"], default="original")
            config['converter']['resolution'] = new_res
        elif choice == '4':
            console.print("Options: [green]both[/green] (scale up/down), [green]downscale_only[/green] (never upscale), [green]upscale_only[/green] (never downscale)")
            new_mode = Prompt.ask("Enter scaling mode", choices=["both", "downscale_only", "upscale_only"], default="both")
            config['converter']['scaling_mode'] = new_mode
        elif choice == '5':
            config['converter']['mirror_input_structure'] = _ask_bool(
                "Mirror out_renamer's folder structure in converter output? (y/n)",
                config['converter']['mirror_input_structure'])
        elif choice == '6':
            config['converter']['skip_existing'] = _ask_bool(
                "Skip files that already exist in the output folder? (y/n)",
                config['converter']['skip_existing'])
        elif choice == '7':
            console.print("Placeholder {n} is replaced by the duplicate number, e.g. ' (2)'.")
            config['converter']['collision_suffix'] = _ask_text(
                "Enter converter collision suffix", config['converter']['collision_suffix'],
                preserve_space=True)
        elif choice == '8':
            console.print(f"Current extensions: [bold green]{', '.join(renamer['video_extensions'])}[/bold green]")
            new_exts = Prompt.ask("Enter new extensions (comma separated, e.g., .mp4, .mkv)")
            if new_exts:
                config['renamer']['video_extensions'] = _normalize_extensions(new_exts.split(','))
        elif choice == '9':
            console.print(f"Current sidecar extensions: [bold green]{', '.join(renamer['sidecar_extensions'])}[/bold green]")
            new_exts = Prompt.ask("Enter new sidecar extensions (comma separated, e.g., .srt, .nfo)")
            if new_exts:
                config['renamer']['sidecar_extensions'] = _normalize_extensions(new_exts.split(','))
        elif choice == '10':
            console.print(f"Current Movie Format: [bold green]{renamer['movie_format']}[/bold green]")
            console.print("Placeholders: [MOVIE_NAME], [YEAR], [IMDB_ID], [EDITION], [RESOLUTION]")
            new_fmt = Prompt.ask("Enter new movie format")
            if new_fmt:
                config['renamer']['movie_format'] = new_fmt
        elif choice == '11':
            console.print(f"Current TV Show Format: [bold green]{renamer['show_format']}[/bold green]")
            console.print("Placeholders: [SHOW_NAME], [SEASON], [EPISODE], [EPISODE_NAME], [EDITION], [RESOLUTION], [SHOW_YEAR], [EPISODE_YEAR]")
            new_fmt = Prompt.ask("Enter new TV show format")
            if new_fmt:
                config['renamer']['show_format'] = new_fmt
        elif choice == '12':
            console.print(f"Current Show Folder Format: [bold green]{renamer['show_folder_format']}[/bold green]")
            console.print("Placeholders: [SHOW_NAME], [SEASON], [EPISODE], [RESOLUTION], [IMDB_ID]")
            new_fmt = Prompt.ask("Enter new show folder format")
            if new_fmt:
                config['renamer']['show_folder_format'] = new_fmt
        elif choice == '13':
            console.print(f"Current Season Folder Format: [bold green]{renamer['season_folder_format']}[/bold green]")
            console.print("Placeholders: [SHOW_NAME], [SEASON], [EPISODE], [RESOLUTION], [IMDB_ID]")
            new_fmt = Prompt.ask("Enter new season folder format")
            if new_fmt:
                config['renamer']['season_folder_format'] = new_fmt
        elif choice == '14':
            current_tags = ', '.join(renamer['edition_tags']) or 'disabled'
            console.print(f"Current edition tags: [bold green]{escape(current_tags)}[/bold green]")
            console.print("Enter comma-separated editions, or 'none' to disable.")
            console.print("Example: Director's Cut, Extended, Remastered, Uncut")
            new_tags = Prompt.ask("Edition tags")
            if new_tags.strip().lower() == 'none':
                config['renamer']['edition_tags'] = []
            elif new_tags.strip():
                config['renamer']['edition_tags'] = [t.strip() for t in new_tags.split(',') if t.strip()]
        elif choice == '15':
            console.print("Options: [green]show_folders[/green] (Show/Staffel NN/), [green]flat[/green] (everything directly in the output folder)")
            new_mode = Prompt.ask("Enter folder mode", choices=["show_folders", "flat"], default=renamer['folder_mode'])
            config['renamer']['folder_mode'] = new_mode
        elif choice == '16':
            console.print("Placeholder {n} is replaced by the duplicate number, e.g. ' (2)'.")
            config['renamer']['collision_suffix'] = _ask_text(
                "Enter renamer collision suffix", renamer['collision_suffix'],
                preserve_space=True)
        elif choice == '17':
            config['paths']['renamer_input'] = _ask_text(
                "Enter renamer input folder", paths['renamer_input'])
        elif choice == '18':
            config['paths']['renamer_output'] = _ask_text(
                "Enter renamer output folder", paths['renamer_output'])
        elif choice == '19':
            config['paths']['converter_input'] = _ask_text(
                "Enter converter input folder", paths['converter_input'])
        elif choice == '20':
            config['paths']['converter_output'] = _ask_text(
                "Enter converter output folder", paths['converter_output'])
        elif choice == '21':
            save_config(_validate_config(config))
            break
