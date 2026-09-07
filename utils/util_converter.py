import os
import sys
import time
import threading
import logging
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
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
SETTING_CONVERT_TO = config['converter']['convert_to']
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
        self.input_file = input_file
        self.name = os.path.basename(input_file)
        self.output_file = output_file
        
        # Pre-fetch media info to avoid calling ffprobe twice (once for frames, once for build_cmd)
        self.media_info = ffprobe_media_info(input_file)

        self.total_frames = get_total_frames(input_file, self.media_info)
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

def run_converter():
    # Reload config in case it changed
    global SETTING_CONVERT_TO, SETTING_THREADS
    config = load_config()
    SETTING_CONVERT_TO = config['converter']['convert_to']
    SETTING_THREADS = config['converter']['threads']

    clear_screen()

    if not os.path.exists('output'):
        os.makedirs('output')

    files_to_convert = []
    extensions = ('.mkv', '.avi', '.mp4', '.mov', '.flv')
    
    # Sort files to ensure consistent order
    all_files = sorted(os.listdir())
    
    for f in all_files:
        if f.lower().endswith(extensions):
            files_to_convert.append((f, os.path.join('output', os.path.splitext(f)[0] + f'.{SETTING_CONVERT_TO}')))

    total_files = len(files_to_convert)
    current_file_idx = 0
    active_workers = []
    completed_tasks = 0
    files_processed_count = 0

    failed_files = []
    warning_files = []

    if total_files == 0:
        console.print("[bold yellow]No files found.[/bold yellow]")
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
                        file_info = files_to_convert[current_file_idx]
                        worker = ConversionWorker(current_file_idx + 1, file_info[0], file_info[1])
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

    os.system('pause')
