# Mass Media Converter & Renamer

An interactive command-line tool for turning a folder of messy media files into a clean, consistently named library, then batch-converting that library to another video container.

The workflow is split into two stages:

```text
  source folder              out_renamer/                   out_converter/
  ─────────────              ────────────                   ─────────────
  Movie.2020.1080p.mkv  ──►  Movie (2020) [tt…] - 1080p.mkv  ──►  Movie (2020) [tt…] - 1080p.mp4
  Show.S01E02.mkv       ──►  Show [tt…] - 1080p/Staffel 01/  ──►  Show [tt…] - 1080p/Staffel 01/
                             Show - S01E02 - Episode.mkv          Show - S01E02 - Episode.mp4
```

1. **Media Renamer** looks up each video on IMDb/TVMaze, builds a clean filename and folder structure, moves the file (plus its subtitles/metadata) into `out_renamer/`.
2. **Media Converter** takes everything from `out_renamer/`, converts it with FFmpeg (stream copy when possible), and writes the result to `out_converter/`, preserving the folder structure.

---

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Output folder structure](#output-folder-structure)
- [Configuration](#configuration)
  - [Paths](#paths)
  - [Converter settings](#converter-settings)
  - [Renamer settings](#renamer-settings)
- [Filename placeholders](#filename-placeholders)
- [Supported formats and codecs](#supported-formats-and-codecs)
- [Logs](#logs)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Notes and limitations](#notes-and-limitations)

---

## Features

- **IMDb-assisted renaming** with an interactive top-suggestion prompt and a numbered suggestion list.
- **Automatic movie/TV detection**:
  - `SxxExx` in the filename → TV episode (metadata from TVMaze).
  - Anything else → movie (metadata from the IMDb suggestion API and Wikidata).
- **IMDb ID reuse and verification**: an existing `tt…` ID in the filename is checked against the parsed title/year; a per-season show cache avoids asking for the same show twice.
- **Resolution detection** via `ffprobe`; the resolution label (e.g. `1080p`) can be embedded in file and folder names.
- **Automatic folder organisation** for TV shows: `<Show> [ttID] - <resolution>/Staffel <NN>/`.
- **Sidecar handling**: subtitles and metadata (`.srt`, `.ass`, `.nfo`, images, …) are moved and renamed with their video, preserving language tags such as `.en.srt`.
- **Collision-safe**: nothing is silently overwritten; duplicates receive a configurable suffix such as ` (2)`.
- **Batch conversion** with multiple concurrent workers and live per-thread progress.
- **Smart FFmpeg command building**: copies streams when the output container supports the codec, re-encodes only when necessary, and can scale to a target resolution.
- **NVIDIA NVENC auto-detection** with CPU fallback.
- **Subtitle-aware**: text subtitles are copied or converted to the output container's format; bitmap subtitles that cannot be represented are skipped and logged.
- **Resumable**: already-converted files in `out_converter/` are skipped on re-runs.
- **Fully configurable** paths, folder modes, naming formats, collision suffixes, extensions and conversion settings via `settings/config.json` or the built-in menu.

---

## Requirements

- **Python 3.9+** (developed against Python 3.13/3.14).
- **FFmpeg and FFprobe** available on your `PATH` — required for resolution detection and all conversions.
- Python packages from [`requirements.txt`](requirements.txt):
  - [`rich`](https://pypi.org/project/rich/) — terminal UI, progress bars, panels.
  - [`requests`](https://pypi.org/project/requests/) — IMDb/TVMaze/Wikidata API calls.
  - [`pyperclip`](https://pypi.org/project/pyperclip/) — optional convenience: copies the detected title to the clipboard before searching. The app runs without it.

Optional but recommended for faster conversions:

- A CUDA-capable NVIDIA GPU. The converter probes for `h264_nvenc` once and uses it automatically when available.

---

## Installation

```bash
git clone <your-repository-url> MassMediaConverter
cd MassMediaConverter

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Install FFmpeg:

| Platform | Suggested install |
| --- | --- |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` or download from <https://ffmpeg.org/download.html> |
| Arch | `sudo pacman -S ffmpeg` |

Verify the installation:

```bash
ffmpeg -version
ffprobe -version
python3 -c "import rich, requests; print('dependencies ok')"
```

---

## Quick start

1. Put your media files into the project folder (or point `paths.renamer_input` at another folder — see [Configuration](#configuration)).
2. Run the app:

   ```bash
   python main.py
   ```

3. Choose **1 — Media Renamer (IMDb)** and work through the prompts. Renamed files and their sidecars are moved into `out_renamer/`.
4. Choose **2 — Media Converter (FFmpeg)**. Converted files are written to `out_converter/`.
5. Your finished library is in `out_converter/`.

> **Important:** the renamer **moves** files out of the source folder. If you want to keep the originals, make a backup or copy them elsewhere first.

---

## Usage

The main menu:

| Option | Action |
| --- | --- |
| **1** | Media Renamer (IMDb) |
| **2** | Media Converter (FFmpeg) |
| **3** | Configuration |
| **4** | Exit |

### Renamer prompts

For each video file the renamer:

1. Detects whether it is a TV episode or a movie.
2. Reuses a verified `tt…` ID already present in the filename when it matches the parsed title/year.
3. Otherwise searches IMDb and shows:
   - the **top suggestion**,
   - a numbered table of up to 5 suggestions.

You can then:

- press **Enter** to accept suggestion #1,
- type a **number (1–5)** to pick another suggestion,
- type a full **IMDb ID** (e.g. `tt1375666`),
- type **`skip`** to leave the file untouched in the source folder.

If an ID found in the filename looks wrong, you are asked whether to use it or search by name.

### Converter prompts

The converter is non-interactive: choose option **2** and it processes everything currently in `out_renamer/`.

- Files whose output already exists in `out_converter/` are skipped (configurable).
- A live progress view shows the overall total and one line per worker thread.
- Failed conversions and "converted but missing streams" warnings are written to `settings/conversion_errors.log`.
- At the end you get a summary of failures, warnings, skipped files, copied sidecars, and the output path.

---

## Output folder structure

Both output folders live next to the app by default and are created automatically.

```text
MassMediaConverter/
├── out_renamer/                                  # renamer output
│   ├── Movie (2020) [tt1234567] - 1080p.mkv
│   ├── Movie (2020) [tt1234567] - 1080p.en.srt
│   └── Show [tt7654321] - 1080p/
│       └── Staffel 01/
│           ├── Show - S01E01 - Pilot.mkv
│           └── Show - S01E01 - Pilot.en.srt
└── out_converter/                                # converter output (mirrors out_renamer)
    ├── Movie (2020) [tt1234567] - 1080p.mp4
    ├── Movie (2020) [tt1234567] - 1080p.en.srt
    └── Show [tt7654321] - 1080p/
        └── Staffel 01/
            ├── Show - S01E01 - Pilot.mp4
            └── Show - S01E01 - Pilot.en.srt
```

Folder rules:

- **Movies** go directly into the renamer output folder.
- **TV episodes** go into `<Show> [ttID] - <resolution>/<season folder>/…`.
- Set `renamer.folder_mode` to `"flat"` to disable show folders entirely.
- Set `converter.mirror_input_structure` to `false` to flatten everything in `out_converter/`.
- The converter never scans its own output folder, even if it is nested inside the input folder.

---

## Configuration

The config file is created automatically on first run:

```text
settings/config.json
```

You can edit it directly or use **Configuration** (menu option 3), which saves the file for you. Unknown/missing keys are filled in automatically, and older config layouts are migrated on load.

### Paths

All paths are resolved relative to the directory from which the app is started (the project root), unless an absolute path is given. `~` is expanded.

| Key | Default | Description |
| --- | --- | --- |
| `paths.renamer_input` | `"."` | Folder scanned by the renamer. Only top-level video files are processed; subfolders are ignored. |
| `paths.renamer_output` | `"out_renamer"` | Where renamed videos and their sidecars are moved. |
| `paths.converter_input` | `"out_renamer"` | Folder scanned recursively by the converter. |
| `paths.converter_output` | `"out_converter"` | Where converted videos and copied sidecars are written. |

### Converter settings

| Key | Default | Description |
| --- | --- | --- |
| `converter.convert_to` | `"mp4"` | Output container/extension. Supported containers: `mp4`, `mkv`, `avi`, `mov`, `webm`, `flv`, `wmv`, `ogv`, `3gp`, `ts`, `m2ts`. |
| `converter.threads` | `5` | Number of simultaneous conversions. Values below 1 are clamped to 1. |
| `converter.resolution` | `"original"` | Target height: `original`, `1080p`, `720p`, `480p`. |
| `converter.scaling_mode` | `"both"` | `both` (scale up or down), `downscale_only` (never upscale), `upscale_only` (never downscale). |
| `converter.mirror_input_structure` | `true` | `true` mirrors the relative paths from the input folder; `false` writes all files flat into the output folder. |
| `converter.skip_existing` | `true` | Skip a conversion when the output file already exists. Set to `false` to re-encode and overwrite. |
| `converter.collision_suffix` | `" ({n})"` | Suffix used when two planned outputs collide. `{n}` is replaced by `2`, `3`, … |

### Renamer settings

| Key | Default | Description |
| --- | --- | --- |
| `renamer.video_extensions` | `.mkv, .mp4, .avi, .mov, .wmv, .flv, .webm, .m4v, .ts` | Files treated as videos by both stages. The converter also uses this list as its input extensions. |
| `renamer.sidecar_extensions` | `.srt, .ass, .ssa, .vtt, .sub, .idx, .nfo, .jpg, .jpeg, .png, .webp` | Files moved/renamed with a video (and copied by the converter). |
| `renamer.movie_format` | `"[MOVIE_NAME] ([YEAR]) [[IMDB_ID]] - [RESOLUTION]"` | Movie filename template. |
| `renamer.show_format` | `"[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME]"` | TV episode filename template. |
| `renamer.show_folder_format` | `"[SHOW_NAME] [[IMDB_ID]] - [RESOLUTION]"` | Show folder template. |
| `renamer.season_folder_format` | `"Staffel [SEASON]"` | Season folder template. |
| `renamer.folder_mode` | `"show_folders"` | `show_folders` creates show/season folders for TV; `flat` puts everything directly in the renamer output. |
| `renamer.collision_suffix` | `" ({n})"` | Suffix used when a destination name already exists. `{n}` is replaced by `2`, `3`, … |

If a collision suffix does not contain `{n}`, `{n}` is appended automatically so repeated collisions cannot loop.

A minimal config:

```json
{
  "paths": {
    "renamer_input": ".",
    "renamer_output": "out_renamer",
    "converter_input": "out_renamer",
    "converter_output": "out_converter"
  },
  "converter": {
    "convert_to": "mp4",
    "threads": 5,
    "resolution": "original",
    "scaling_mode": "both",
    "mirror_input_structure": true,
    "skip_existing": true,
    "collision_suffix": " ({n})"
  },
  "renamer": {
    "video_extensions": [".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"],
    "sidecar_extensions": [".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".nfo", ".jpg", ".jpeg", ".png", ".webp"],
    "movie_format": "[MOVIE_NAME] ([YEAR]) [[IMDB_ID]] - [RESOLUTION]",
    "show_format": "[SHOW_NAME] - S[SEASON]E[EPISODE] - [EPISODE_NAME]",
    "show_folder_format": "[SHOW_NAME] [[IMDB_ID]] - [RESOLUTION]",
    "season_folder_format": "Staffel [SEASON]",
    "folder_mode": "show_folders",
    "collision_suffix": " ({n})"
  }
}
```

---

## Filename placeholders

### `renamer.movie_format`

| Placeholder | Example |
| --- | --- |
| `[MOVIE_NAME]` | `Inception` |
| `[YEAR]` | `2010` |
| `[IMDB_ID]` | `tt1375666` |
| `[RESOLUTION]` | `1080p` |

### `renamer.show_format`

| Placeholder | Example |
| --- | --- |
| `[SHOW_NAME]` | `Breaking Bad` |
| `[SEASON]` | `01` |
| `[EPISODE]` | `02` |
| `[EPISODE_NAME]` | `Cat's in the Bag...` |
| `[RESOLUTION]` | `1080p` |
| `[IMDB_ID]` | `tt0903747` |
| `[YEAR]` | show premiere year (e.g. `2008`) |
| `[SHOW_YEAR]` | same as `[YEAR]` |
| `[EPISODE_YEAR]` | episode air year, or `Unknown` |

### `renamer.show_folder_format` and `renamer.season_folder_format`

| Placeholder | Example |
| --- | --- |
| `[SHOW_NAME]` | `Breaking Bad` |
| `[SEASON]` | `01` |
| `[EPISODE]` | `02` |
| `[IMDB_ID]` | `tt0903747` |
| `[RESOLUTION]` | `1080p` |

Notes:

- Double brackets produce a literal bracketed value: `[[IMDB_ID]]` → `[tt0903747]`.
- `:` is replaced with `-` in titles; other illegal filename characters (`<>"/\|?*`) are removed.
- If the resolution cannot be detected, `[RESOLUTION]` becomes `Unknown`; the `- Unknown` suffix is dropped from show folder names.
- If no IMDb ID is available, an empty `[]` in a folder name is removed.
- Missing episode titles fall back to `Unknown Episode`.

---

## Supported formats and codecs

The converter decides per stream whether the input codec can be **copied** into the target container. Otherwise it re-encodes.

- **Video**: only the first non-cover-art video stream is kept. 10-bit input is encoded as HEVC when the container supports it, otherwise H.264 with a warning.
- **Audio**: every audio stream is mapped; unsupported codecs are converted to the first supported codec for that container.
- **Subtitles**: every text subtitle is mapped; unsupported text subtitles are converted to the container's default text format. Bitmap subtitles (PGS, VobSub, DVD) are skipped for MP4/MOV with a warning.

Copy-capable codecs by container:

| Container | Video | Audio | Subtitles |
| --- | --- | --- | --- |
| `mp4` | h264, hevc, mpeg4, vp9, av1, mpeg2video, mjpeg | aac, mp3, ac3, eac3, alac, opus, flac, vorbis, dts | mov_text, srt, webvtt, ttml |
| `mkv` | h264, hevc, vp9, vp8, av1, mpeg2video, mpeg4, theora, prores, dnxhd, ffv1, mjpeg | aac, mp3, ac3, eac3, flac, opus, vorbis, dts, truehd, pcm_s16le, pcm_s24le, alac | srt, ass, ssa, subrip, webvtt, pgs, dvd_subtitle, vobsub, text, dvb_subtitle, hdmv_pgs_subtitle |
| `avi` | h264, mpeg4, mpeg2video, mjpeg, divx, xvid, h263, rawvideo | mp3, ac3, pcm_s16le, pcm_s24le, u8, aac, dts | srt, subrip, xsub |
| `mov` | h264, hevc, mpeg4, mpeg2video, prores, dnxhd, dvvideo, mjpeg, qtrle, svq1, svq3, av1 | aac, alac, mp3, ac3, pcm_s16le, pcm_s24le, pcm_s32le, pcm_f32le, adpcm_ima_qt, flac | mov_text, srt, eia_608 |
| `webm` | vp8, vp9, av1 | opus, vorbis | webvtt |
| `flv` | h264, vp6, sorenson, h263, flashsv | aac, mp3, nellymoser, adpcm_swf, speex | — |
| `wmv` | wmv1, wmv2, wmv3, vc1, mpeg4, msmpeg4v3 | wmav1, wmav2, wmapro, pcm_s16le | wmv_subtitle |
| `ogv` | theora, dirac, daala | vorbis, flac, opus, speex | — |
| `3gp` | h263, h264, mpeg4 | aac, amr_nb, amr_wb | mov_text, 3gpp_timed_text |
| `ts` | h264, hevc, mpeg2video | aac, mp3, ac3, eac3, mp2, dts, truehd | dvb_subtitle, dvb_teletext, arib_caption |
| `m2ts` | h264, hevc, mpeg2video, vc1 | aac, ac3, eac3, dts, truehd, pcm_bluray | pgs, hdmv_pgs_subtitle |

Resolution labels produced by the resolver: `4320p`, `2880p`, `2160p`, `1440p`, `1080p`, `900p`, `720p`, `540p`, `480p`, `360p`, `SD`.

---

## Logs

All logs live in `settings/`:

| File | Content |
| --- | --- |
| `settings/renamer_changelog.txt` | One destination path per moved video/sidecar, appended across runs. |
| `settings/conversion_errors.log` | FFmpeg failures (exit code + stderr) and warnings about skipped streams / missing subtitles. |

The renamer also appends to its changelog only after a successful move; failed moves are printed to the console and not logged as successful.

---

## How it works

### Renamer

1. Reads `paths.renamer_input` and collects top-level, non-hidden files whose extension is in `renamer.video_extensions`.
2. Detects TV episodes with an `SxxExx` pattern (case-insensitive); everything else is treated as a movie and parsed for a year (`1900–2099`).
3. Looks for an existing `tt…` ID in the filename and verifies it:
   - movies: title similarity plus a year check (±1 year),
   - TV: title similarity.
4. If needed, searches the IMDb suggestion API and prompts for a choice.
5. Resolves resolution with `ffprobe` and maps it to a category label.
6. Fetches metadata:
   - movies → IMDb suggestion data or Wikidata (title/year by IMDb ID),
   - TV → TVMaze (show title, episode title, show year, air year).
7. Builds the destination path:
   - `folder_mode = show_folders` → `<show folder>/<season folder>/`,
   - `folder_mode = flat` → directly in the output folder.
8. Moves the video with a collision-safe destination name, then moves matching sidecars next to it.
9. Appends the relative destination path(s) to the changelog.

Skipped or failed files stay in the source folder so you can retry them later.

### Converter

1. Reads `paths.converter_input` and recursively collects files matching `renamer.video_extensions`, skipping hidden files and the output folder itself.
2. Plans one output path per source:
   - mirror mode → same relative subpath with the new extension,
   - flat mode → basename with the new extension.
3. Skips outputs that already exist when `skip_existing` is enabled; otherwise reserves unique names for collisions.
4. Copies matching sidecars into the output folder next to the video.
5. Probes each file with `ffprobe`, then builds an FFmpeg command:
   - maps the first main video stream,
   - maps all audio and subtitle streams,
   - copies codecs the target container supports, re-encodes the rest,
   - optionally applies `scale=-2:<height>` for the configured resolution,
   - writes progress to a pipe for the live UI.
6. Runs up to `converter.threads` conversions concurrently and reports failures/warnings at the end.

---

## Troubleshooting

### `Input folder not found: …/out_renamer`

Run the renamer first, or point `paths.converter_input` at an existing folder.

### `No files found in …`

- The input folder is empty.
- Files are in subfolders — the renamer only scans the top level; move them up or set `paths.renamer_input` to that folder.
- The extension is not in `renamer.video_extensions`.

### `ffmpeg` / `ffprobe` not found

Install FFmpeg and make sure both binaries are on `PATH`. Restart the terminal afterwards.

### Conversion fails

Check `settings/conversion_errors.log`. Common causes:

- Corrupt or incomplete source files.
- Encoder unavailable in your FFmpeg build (e.g. `libx265`).
- Disk full.
- Unsupported combination of streams for the target container.

### `pyperclip` errors

`pyperclip` is optional; the app already handles it being absent. Install it if you want the detected title copied to the clipboard before each search:

```bash
pip install pyperclip
```

### A file was renamed incorrectly

The renamer uses the top IMDb suggestion when you press Enter. Re-run it with the correct ID, or fix the file manually. Once a file has moved to `out_renamer/`, re-running the renamer will not touch it unless you point `paths.renamer_input` at `out_renamer` and rename it again.

### Re-convert a file that was skipped

Delete the corresponding file from `out_converter/` and run the converter again, or set `converter.skip_existing` to `false`.

### Keep original files

The renamer **moves** files out of the source folder. Back up your source folder first, or work on a copy of your media. The renamed original remains in `out_renamer/` after conversion — the converter only writes to `out_converter/` and never deletes from `out_renamer/`.

---

## Project layout

```text
MassMediaConverter/
├── main.py                  # Main menu and entry point
├── requirements.txt         # Python dependencies
├── README.md                # This file
├── .gitignore               # Ignores runtime folders and caches
├── settings/                # Created on first run
│   ├── config.json
│   ├── renamer_changelog.txt
│   └── conversion_errors.log
├── out_renamer/             # Created by the renamer
├── out_converter/           # Created by the converter
└── utils/
    ├── util_colors.py       # Clear-screen helper
    ├── util_config.py       # Config schema, loading, migration and menu
    ├── util_converter.py    # Conversion planning, workers and progress UI
    ├── util_ffmpeg.py       # FFmpeg probe/command building and codec tables
    ├── util_renamer.py      # Rename flow, sidecars, folders and changelog
    └── util_resolution.py   # Resolution detection and category labels
```

Runtime folders (`settings/`, `out_renamer/`, `out_converter/`, `output/`) and Python caches are ignored by Git.

---

## Notes and limitations

- **Internet required** for the renamer: IMDb suggestion API, TVMaze, and Wikidata are queried live. Offline runs will show search errors; files are then skipped.
- **Interactive**: the renamer asks for a choice per file that has no verified ID.
- **TV detection is pattern-based**: only `SxxExx` is recognised. Other schemes (e.g. `1x02`) are treated as movies.
- **The renamer only scans one folder level**; put files in the renamer input folder itself.
- **Sidecar matching** is by matching basename or basename plus a dotted tag: `Movie.srt`, `Movie.en.srt`, `Movie.forced.srt`. Files with unrelated names are left alone.
- **Bitmap subtitles** cannot be stored as text in MP4/MOV; they are skipped with a warning. Use MKV if you need to keep them.
- **Only the first main video stream** is converted; secondary video streams (e.g. multiple angles) are skipped with a warning.
- **Cover art / attached pictures** are skipped.
- **`-movflags +faststart`** is applied to every output; modern FFmpeg ignores it for non-MP4 containers.
- The repository historically tracked `__pycache__` files. They are now ignored; remove them from the index with `git rm -r --cached utils/__pycache__` if you want a clean tree.
