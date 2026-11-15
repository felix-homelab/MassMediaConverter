import os
import shutil
import sys
import time
import threading
import logging
from pathlib import Path
from rich.progress import Progress, BarColumn, TextColumn
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.align import Align
from rich.markup import escape

from utils.util_ffmpeg import *
from utils.util_config import load_config
from utils.util_colors import clear_screen

console = Console()

# --- Configuration ---
config = load_config()
SETTING_THREADS = config['converter']['threads']

if not os.path.exists('settings'):
    os.makedirs('settings')

# --- Logging Setup ---
logging.basicConfig(
    filename='settings/conversion_errors.log',
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


class ConversionWorker:
    def __init__(self, task_id, input_file, output_file):
        self.task_id = task_id
        self.input_file = str(input_file)
        self.output_file = str(output_file)
        self.name = os.path.basename(self.input_file)

        # Pre-fetch media info to avoid calling ffprobe twice (once for frames, once for build_cmd)
        self.media_info = ffprobe_media_info(self.input_file)

        output_dir = os.path.dirname(self.output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        self.total_frames = get_total_frames(self.input_file, self.media_info)
        if self.total_frames == 0: self.total_frames = 1

        self.current_frame = 0
        self.progress_percent = 0
        self.process = None
        self.is_finished = False
        self.end_timestamp = None
        self.has_error = False
        self.warnings = []
        self._monitor_thread = None
        self.rich_task_id = None  # Will be assigned by Rich Progress

    def start(self):
        cmd, warnings = ffmpeg_build_cmd(self.input_file, self.output_file, self.media_info)
        self.warnings = warnings
        
        if not cmd:
            # Failed to build command (e.g. file not found or probe failed)
            self.has_error = True
            logging.error(f"FILE: {self.input_file}\nERROR: Could not build ffmpeg command. {warnings}")
            self.is_finished = True
            return

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace'
        )
        self._monitor_thread = threading.Thread(target=self._monitor_output, daemon=True)
        self._monitor_thread.start()

    def _monitor_output(self):
        while self.process.poll() is None:
            line = self.process.stdout.readline()
            if not line: break

            line = line.strip()
            if line.startswith('frame='):
                try:
                    frame_str = line.split('=')[1].strip()
                    if frame_str.isdigit():
                        self.current_frame = int(frame_str)
                        self.progress_percent = int((self.current_frame / self.total_frames) * 100)
                        self.progress_percent = min(self.progress_percent, 99)
                except:
                    pass

        self.process.wait()

        if self.process.returncode != 0:
            # --- FAILURE ---
            self.has_error = True
            error_log = self.process.stderr.read()
            error_msg = (
                f"FILE: {self.input_file}\n"
                f"EXIT CODE: {self.process.returncode}\n"
                f"FFMPEG ERROR:\n{error_log}\n{'-' * 40}"
            )
            logging.error(error_msg)
            self.progress_percent = 0
        else:
            # --- SUCCESS ---
            self.progress_percent = 100
            if self.warnings:
                warn_msg = (
                        f"FILE: {self.input_file}\n"
                        f"STATUS: Converted but missing streams\n"
                        f"DETAILS:\n" + "\n".join(self.warnings) + f"\n{'-' * 40}"
                )
                logging.warning(warn_msg)

        self.is_finished = True


def truncate_filename(filename, max_length=40, offset=0):
    max_length -= offset
    if len(filename) > max_length:
        return filename[:max_length-3] + '...'
    else:
        return filename + ' ' * (max_length - len(filename))

def _resolve_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def _collect_input_files(input_root, output_root, extensions):
    """Recursively collect convertible files below input_root.

    The output folder is never scanned, even when it is nested inside the
    input folder.
    """
    input_root = Path(input_root)
    output_root = Path(output_root).resolve()
    found = []

    for root, dirs, filenames in os.walk(input_root):
        current = Path(root)
        dirs[:] = sorted(
            d for d in dirs
            if (current / d).resolve() != output_root
        )
        for filename in filenames:
            if filename.startswith('.'):
                continue
            path = current / filename
            if path.suffix.lower() in extensions and path.is_file():
                found.append(path)

    return sorted(found, key=lambda p: str(p).lower())


def _reserve_destination(destination, reserved, collision_format, avoid_existing):
    """Make sure two planned jobs never write to the same output file."""
    candidate = destination
    number = 2
    while candidate in reserved or (avoid_existing and candidate.exists()):
        if "{n}" in collision_format:
            addition = collision_format.replace("{n}", str(number))
        else:
            addition = f"{collision_format}{number}"
        candidate = destination.with_name(f"{destination.stem}{addition}{destination.suffix}")
        number += 1
    reserved.add(candidate)
    return candidate


def _sidecar_files(source, sidecar_extensions):
    """Find subtitle/metadata files that belong to `source`.

    Matches "Movie.srt" and language-tagged files such as "Movie.en.srt".
    Returns a list of (path, remainder) tuples.
    """
    stem = source.stem
    matches = []

    try:
        entries = list(source.parent.iterdir())
    except OSError:
        return matches

    for entry in entries:
        if not entry.is_file() or entry == source:
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


def collect_conversion_jobs(config=None, input_root=None, output_root=None):
    """Plan the conversions.

    Returns (jobs, skipped_existing, sidecar_copies):
      * jobs             - (source_path, destination_path) video conversions
      * skipped_existing - sources whose output file already exists
      * sidecar_copies   - (source_path, destination_path) sidecar copies
    """
    if config is None:
        config = load_config()

    converter = config['converter']
    renamer = config['renamer']

    extension = f".{converter['convert_to'].lstrip('.').lower()}"
    mirror = converter.get('mirror_input_structure', True)
    skip_existing = converter.get('skip_existing', True)
    collision_format = converter.get('collision_suffix', ' ({n})')

    if input_root is None:
        input_root = _resolve_path(config['paths']['converter_input'])
    if output_root is None:
        output_root = _resolve_path(config['paths']['converter_output'])
    input_root = Path(input_root)
    output_root = Path(output_root)

    extensions = {e.lower() for e in renamer.get('video_extensions', [])}
    sidecar_extensions = {e.lower() for e in renamer.get('sidecar_extensions', [])}
    sources = _collect_input_files(input_root, output_root, extensions)

    jobs = []
    skipped_existing = []
    sidecar_copies = []
    reserved = set()

    for source in sources:
        relative = source.relative_to(input_root)
        if mirror:
            destination = output_root / relative.with_suffix(extension)
        else:
            destination = output_root / (source.stem + extension)

        if skip_existing and destination.exists():
            final_destination = destination
            skipped_existing.append(source)
        else:
            final_destination = _reserve_destination(
                destination, reserved, collision_format, skip_existing)
            jobs.append((source, final_destination))

        # Sidecars follow the video name and live in the same output folder.
        for sidecar, remainder in _sidecar_files(source, sidecar_extensions):
            target = final_destination.with_name(
                f"{final_destination.stem}{remainder}{sidecar.suffix}")
            if skip_existing and target.exists():
                continue
            target = _reserve_destination(
                target, reserved, collision_format, skip_existing)
            sidecar_copies.append((sidecar, target))

    return jobs, skipped_existing, sidecar_copies


def copy_sidecars(sidecar_copies):
    """Copy planned sidecars. Returns (copied_count, failures)."""
    copied = 0
    failures = []
    for source, target in sidecar_copies:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        except OSError as e:
            failures.append((source, str(e)))
            logging.warning(f"FILE: {source}\nSTATUS: Could not copy sidecar\nERROR: {e}\n{'-' * 40}")
    return copied, failures


def _pause():
    console.print()
    try:
        console.input("[bold yellow]Press Enter to continue...[/bold yellow]")
    except (EOFError, KeyboardInterrupt):
        pass


def _report_sidecars(copied, failures):
    if copied:
        console.print(f"[bold green]Copied {copied} sidecar file(s) to the output folder.[/bold green]")
    if failures:
        console.print(
            f"[bold yellow]Could not copy {len(failures)} sidecar file(s); see 'conversion_errors.log'.[/bold yellow]")


def run_converter():
    # Reload config in case it changed
    global SETTING_THREADS
    config = load_config()
    SETTING_THREADS = max(1, int(config['converter']['threads']))

    clear_screen()

    input_root = _resolve_path(config['paths']['converter_input'])
    output_root = _resolve_path(config['paths']['converter_output'])

    console.print(f"[bold]Input folder:[/bold]  {escape(str(input_root))}")
    console.print(f"[bold]Output folder:[/bold] {escape(str(output_root))}")

    if not input_root.exists() or not input_root.is_dir():
        console.print(f"[bold red]Input folder not found: {escape(str(input_root))}[/bold red]")
        console.print("[bold yellow]Run the Media Renamer first so this folder is created.[/bold yellow]")
        _pause()
        return

    if input_root.resolve() == output_root.resolve():
        console.print("[bold red]Input and output folders must be different.[/bold red]")
        _pause()
        return

    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(f"[bold red]Error creating output folder: {e}[/bold red]")
        _pause()
        return

    files_to_convert, skipped_existing, sidecar_copies = collect_conversion_jobs(
        config, input_root, output_root)

    # External subtitles/metadata are copied next to their converted video.
    copied_sidecars, failed_sidecars = copy_sidecars(sidecar_copies)

    total_files = len(files_to_convert)
    current_file_idx = 0
    active_workers = []
    completed_tasks = 0
    files_processed_count = 0

    failed_files = []
    warning_files = []

    if total_files == 0:
        if skipped_existing:
            console.print(
                f"[bold yellow]All {len(skipped_existing)} file(s) are already converted.[/bold yellow]")
        else:
            console.print(
                f"[bold yellow]No files found in {escape(str(input_root))}.[/bold yellow]")
        _report_sidecars(copied_sidecars, failed_sidecars)
        _pause()
        return

    # Create a Progress instance for the overall progress
    overall_progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        expand=True
    )
    
    overall_task_id = overall_progress.add_task(f"Converting Files... 0/{total_files}", total=total_files)

    # Initialize thread slots state
    thread_slots = [{'index': i, 'text': truncate_filename('Pending...'), 'completed': 0} for i in range(SETTING_THREADS)]

    # Initial table creation
    dummy_thread_progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        expand=True
    )
    for slot in thread_slots:
        dummy_thread_progress.add_task(f"Thread {slot['index'] + 1}: {slot['text']}", completed=slot['completed'])

    progress_table = Table.grid(expand=True)
    progress_table.add_row(Panel(overall_progress, title="[bold magenta]Total Progress[/bold magenta]", border_style="magenta"))
    progress_table.add_row(Panel(dummy_thread_progress, title="[bold green]Individual Threads[/bold green]", border_style="green"))

    try:
        with Live(progress_table, auto_refresh=False) as live:
            while completed_tasks < total_files:

                # 1. Manage Completed Workers
                for worker in active_workers[:]:
                    if worker.is_finished:
                        if worker.end_timestamp is None:
                            worker.end_timestamp = time.time()
                            
                            # Record stats
                            if worker.has_error:
                                failed_files.append(worker.name)
                            elif worker.warnings:
                                warning_files.append(worker.name)
                            
                            overall_progress.advance(overall_task_id)
                            files_processed_count += 1
                            overall_progress.update(overall_task_id, description=f"Converting Files... {files_processed_count}/{total_files}")

                        # Remove from active list after cooldown
                        if time.time() - worker.end_timestamp > 1.0:
                            # Reset the thread slot to "Done" state
                            thread_idx = worker.thread_index
                            thread_slots[thread_idx]['text'] = truncate_filename('Done. No current task.')
                            thread_slots[thread_idx]['completed'] = 100
                            
                            active_workers.remove(worker)
                            completed_tasks += 1

                # 2. Add New Workers
                while len(active_workers) < SETTING_THREADS and current_file_idx < total_files:
                    # Find first available thread slot
                    used_slots = [w.thread_index for w in active_workers]
                    available_slot = -1
                    for i in range(SETTING_THREADS):
                        if i not in used_slots:
                            available_slot = i
                            break
                    
                    if available_slot != -1:
                        source_file, output_file = files_to_convert[current_file_idx]
                        worker = ConversionWorker(current_file_idx + 1, source_file, output_file)
                        worker.thread_index = available_slot # Assign slot
                        
                        # Update slot state
                        thread_slots[available_slot]['text'] = truncate_filename('Pending...')
                        thread_slots[available_slot]['completed'] = 0
                        
                        worker.start()
                        active_workers.append(worker)
                        current_file_idx += 1
                    else:
                        break

                # 3. Update Progress & Descriptions
                for worker in active_workers:
                    display_name = escape(truncate_filename(worker.name))
                    
                    if worker.is_finished:
                        if worker.has_error:
                            desc = f"[red]FAILED: {escape(truncate_filename(worker.name, offset=8))}[/red]"
                        elif worker.warnings:
                            desc = f"[yellow]NO SUBS: {escape(truncate_filename(worker.name, offset=9))}[/yellow]"
                        else:
                            desc = f"[green]DONE: {escape(truncate_filename(worker.name, offset=6))}[/green]"
                        
                        thread_slots[worker.thread_index]['text'] = desc
                        thread_slots[worker.thread_index]['completed'] = 100
                    else:
                        desc = f"{display_name}"
                        thread_slots[worker.thread_index]['text'] = desc
                        thread_slots[worker.thread_index]['completed'] = worker.progress_percent

                # 4. Rebuild Display
                # Sort slots: Active/Pending first, Done last. Stable sort by index.
                display_slots = sorted(thread_slots, key=lambda s: (1 if 'Done. No current task.' in s['text'] else 0, s['index']))

                thread_progress = Progress(
                    TextColumn("[bold cyan]{task.description}"),
                    BarColumn(bar_width=None),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    expand=True
                )
                
                for i, slot in enumerate(display_slots):
                    thread_progress.add_task(f"Thread {i+1}: {slot['text']}", completed=slot['completed'], total=100)

                progress_table = Table.grid(expand=True)
                progress_table.add_row(Panel(overall_progress, title="[bold magenta]Total Progress[/bold magenta]", border_style="magenta"))
                progress_table.add_row(Panel(thread_progress, title="[bold green]Individual Threads[/bold green]", border_style="green"))

                live.update(Align.center(progress_table))
                live.refresh()
                time.sleep(0.1)

            # --- Final Update to show all threads as Done ---
            for i in range(SETTING_THREADS):
                thread_slots[i]['text'] = truncate_filename("Done. No current task.")
                thread_slots[i]['completed'] = 100
            
            # Rebuild display one last time
            display_slots = sorted(thread_slots, key=lambda s: s['index'])
            thread_progress = Progress(
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=None),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                expand=True
            )
            for i, slot in enumerate(display_slots):
                thread_progress.add_task(f"Thread {i+1}: {slot['text']}", completed=slot['completed'], total=100)
            
            progress_table = Table.grid(expand=True)
            progress_table.add_row(Panel(overall_progress, title="[bold magenta]Total Progress[/bold magenta]", border_style="magenta"))
            progress_table.add_row(Panel(thread_progress, title="[bold green]Individual Threads[/bold green]", border_style="green"))
            
            live.update(Align.center(progress_table))
            live.refresh()

    except KeyboardInterrupt:
        for w in active_workers: 
            if w.process:
                w.process.kill()
        sys.exit()

    # --- Final Report ---
    console.print()
    # console.print(Panel(f"[bold green]Total Progress: {total_files}/{total_files} | 100%[/bold green]", title="[bold magenta]Conversion Complete[/bold magenta]"))

    if failed_files or warning_files:
        console.print("[bold yellow]Done. Check 'conversion_errors.log' for details.[/bold yellow]")
        if failed_files:
            console.print(f"[bold red]FAILED ({len(failed_files)}):[/bold red]")
            for f in failed_files: console.print(f" - {escape(f)}")
        if warning_files:
            console.print(f"[bold yellow]WARNINGS / NO SUBS ({len(warning_files)}):[/bold yellow]")
            for f in warning_files: console.print(f" - {escape(f)}")
    else:
        console.print('[bold green]Done! All files converted perfectly.[/bold green]')

    if skipped_existing:
        console.print(
            f"[bold yellow]Skipped {len(skipped_existing)} already-converted file(s).[/bold yellow]")

    _report_sidecars(copied_sidecars, failed_sidecars)

    console.print(f"[bold green]Output written to:[/bold green] {escape(str(output_root))}")

    _pause()
