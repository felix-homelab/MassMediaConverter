import os
import sys
import time
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.align import Align

from utils.util_renamer import run_renamer
from utils.util_converter import run_converter
from utils.util_config import edit_config_menu
from utils.util_colors import clear_screen

console = Console()

def main_menu():
    while True:
        clear_screen()
        
        menu_text = (
            "\n[bold cyan]1.[/bold cyan] Media Renamer (IMDb)\n"
            "[bold cyan]2.[/bold cyan] Media Converter (FFmpeg)\n"
            "[bold cyan]3.[/bold cyan] Configuration\n"
            "[bold red]4.[/bold red] Exit\n"
        )
        
        panel = Panel(
            Align.center(menu_text),
            title="[bold magenta]Mass Media Converter & Renamer[/bold magenta]",
            subtitle="[italic dim]Automate your media workflow[/italic dim]",
            border_style="bright_blue",
            padding=(1, 4),
            expand=False
        )
        
        # Vertical centering
        # Estimate panel height: 2 (borders) + 2 (padding) + 6 (content) = 10 lines
        panel_height = 10
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
        choice = Prompt.ask(f"{prompt_padding}[bold green]>>>[/bold green]", choices=["1", "2", "3", "4"], show_choices=False)

        if choice == '1':
            run_renamer()
            try:
                console.input("[bold yellow]Press Enter to continue...[/bold yellow]")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == '2':
            run_converter()
        elif choice == '3':
            edit_config_menu()
        elif choice == '4':
            console.print("[bold red]Exiting...[/bold red]")
            sys.exit()

if __name__ == '__main__':
    # Enable ANSI escape sequences on Windows
    os.system('')
    main_menu()
