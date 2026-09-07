import json
import os
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.align import Align
from utils.util_colors import clear_screen

console = Console()

CONFIG_FILE = 'settings/config.json'

DEFAULT_CONFIG = {
    "converter": {
        "convert_to": "mp4",
        "threads": 5,
        "resolution": "original",  # Options: original, 1080p, 720p, 480p
        "scaling_mode": "both"     # Options: both, downscale_only, upscale_only
    },
    "renamer": {
        "video_extensions": [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"],
        "movie_format": "[MOVIE_NAME] ([YEAR]) [[IMDB_ID]] - [RESOLUTION]",
        "show_format": "[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME]",
        "show_folder_format": "[SHOW_NAME] [[IMDB_ID]] - [RESOLUTION]",
        "season_folder_format": "Staffel [SEASON]"
    }
}

def load_config():
    if not os.path.exists('settings'):
        os.makedirs('settings')
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            
            # Ensure new keys exist in loaded config
            if "renamer" not in config:
                config["renamer"] = DEFAULT_CONFIG["renamer"]
            else:
                if "movie_format" not in config["renamer"]:
                    config["renamer"]["movie_format"] = DEFAULT_CONFIG["renamer"]["movie_format"]
                if "show_format" not in config["renamer"]:
                    config["renamer"]["show_format"] = DEFAULT_CONFIG["renamer"]["show_format"]
                if "show_folder_format" not in config["renamer"]:
                    config["renamer"]["show_folder_format"] = DEFAULT_CONFIG["renamer"]["show_folder_format"]
                if "season_folder_format" not in config["renamer"]:
                    config["renamer"]["season_folder_format"] = DEFAULT_CONFIG["renamer"]["season_folder_format"]
                if "video_extensions" not in config["renamer"]:
                    config["renamer"]["video_extensions"] = DEFAULT_CONFIG["renamer"]["video_extensions"]
            
            if "converter" not in config:
                config["converter"] = DEFAULT_CONFIG["converter"]
            else:
                if "resolution" not in config["converter"]:
                    config["converter"]["resolution"] = DEFAULT_CONFIG["converter"]["resolution"]
                if "scaling_mode" not in config["converter"]:
                    config["converter"]["scaling_mode"] = DEFAULT_CONFIG["converter"]["scaling_mode"]
                if "convert_to" not in config["converter"]:
                    config["converter"]["convert_to"] = DEFAULT_CONFIG["converter"]["convert_to"]
                if "threads" not in config["converter"]:
                    config["converter"]["threads"] = DEFAULT_CONFIG["converter"]["threads"]
                    
            return config
    except Exception as e:
        console.print(f"[bold red]Error loading config: {e}[/bold red]")
        return DEFAULT_CONFIG

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        console.print(f"[bold red]Error saving config: {e}[/bold red]")

def edit_config_menu():
    config = load_config()
    while True:
        clear_screen()
        
        menu_text = (
            f"\n[bold cyan]1.[/bold cyan] Converter: Output Format ([bold green]{config['converter']['convert_to']}[/bold green])\n"
            f"[bold cyan]2.[/bold cyan] Converter: Threads ([bold green]{config['converter']['threads']}[/bold green])\n"
            f"[bold cyan]3.[/bold cyan] Converter: Resolution ([bold green]{config['converter']['resolution']}[/bold green])\n"
            f"[bold cyan]4.[/bold cyan] Converter: Scaling Mode ([bold green]{config['converter']['scaling_mode']}[/bold green])\n"
            f"[bold cyan]5.[/bold cyan] Renamer: Extensions\n"
            f"[bold cyan]6.[/bold cyan] Renamer: Movie Format\n"
            f"[bold cyan]7.[/bold cyan] Renamer: TV Show Format\n"
            f"[bold yellow]8.[/bold yellow] Save & Back\n"
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
        # Estimate panel height: 2 (borders) + 2 (padding) + 10 (content) = 14 lines
        panel_height = 14
        top_padding = (console.height - panel_height) // 2
        # Ensure we don't have negative padding
        if top_padding < 0:
            top_padding = 0
            
        console.print("\n" * top_padding)
        console.print(Align.center(panel))
        
        console.print() # Add some spacing
        
        console.print(Align.center("[bold yellow]Select an option[/bold yellow]"))
        
        # Center the prompt input cursor roughly
        prompt_padding = " " * max(0, (console.width // 2 - 2))
        choice = Prompt.ask(f"{prompt_padding}[bold green]>>>[/bold green]", choices=["1", "2", "3", "4", "5", "6", "7", "8"], show_choices=False)
        
        if choice == '1':
            new_format = Prompt.ask("Enter new output format (e.g., mp4, mkv)")
            if new_format:
                config['converter']['convert_to'] = new_format
        elif choice == '2':
            new_threads = Prompt.ask("Enter number of threads")
            if new_threads.isdigit():
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
            console.print(f"Current extensions: [bold green]{', '.join(config['renamer']['video_extensions'])}[/bold green]")
            new_exts = Prompt.ask("Enter new extensions (comma separated, e.g., .mp4, .mkv)")
            if new_exts:
                ext_list = [e.strip() for e in new_exts.split(',')]
                # Ensure they start with dot
                ext_list = [e if e.startswith('.') else f'.{e}' for e in ext_list]
                config['renamer']['video_extensions'] = ext_list
        elif choice == '6':
            console.print(f"Current Movie Format: [bold green]{config['renamer']['movie_format']}[/bold green]")
            console.print("Placeholders: [MOVIE_NAME], [YEAR], [IMDB_ID], [RESOLUTION]")
            new_fmt = Prompt.ask("Enter new movie format")
            if new_fmt:
                config['renamer']['movie_format'] = new_fmt
        elif choice == '7':
            console.print(f"Current TV Show Format: [bold green]{config['renamer']['show_format']}[/bold green]")
            console.print("Placeholders: [SHOW_NAME], [SEASON], [EPISODE], [EPISODE_NAME], [RESOLUTION], [SHOW_YEAR], [EPISODE_YEAR]")
            new_fmt = Prompt.ask("Enter new TV show format")
            if new_fmt:
                config['renamer']['show_format'] = new_fmt
        elif choice == '8':
            save_config(config)
            break
