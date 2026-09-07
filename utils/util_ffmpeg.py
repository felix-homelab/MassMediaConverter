import json
import subprocess
from rich.console import Console
from utils.util_config import load_config

console = Console()

# --- Configuration & Constants ---

supported_video_codecs = {
    'mp4': ['h264', 'hevc', 'mpeg4', 'vp9', 'av1', 'mpeg2video', 'mjpeg'],
    'mkv': ['h264', 'hevc', 'vp9', 'vp8', 'av1', 'mpeg2video', 'mpeg4', 'theora', 'prores', 'dnxhd', 'ffv1', 'mjpeg'],
    'avi': ['h264', 'mpeg4', 'mpeg2video', 'mjpeg', 'divx', 'xvid', 'h263', 'rawvideo'],
    'mov': ['h264', 'hevc', 'mpeg4', 'mpeg2video', 'prores', 'dnxhd', 'dvvideo', 'mjpeg', 'qtrle', 'svq1', 'svq3', 'av1'],
    'webm': ['vp8', 'vp9', 'av1'],
    'flv': ['h264', 'vp6', 'sorenson', 'h263', 'flashsv'],
    'wmv': ['wmv1', 'wmv2', 'wmv3', 'vc1', 'mpeg4', 'msmpeg4v3'],
    'ogv': ['theora', 'dirac', 'daala'],
    '3gp': ['h263', 'h264', 'mpeg4'],
    'ts':  ['h264', 'hevc', 'mpeg2video'],
    'm2ts': ['h264', 'hevc', 'mpeg2video', 'vc1']
}

supported_audio_codecs = {
    'mp4': ['aac', 'mp3', 'ac3', 'eac3', 'alac', 'opus', 'flac', 'vorbis', 'dts'],
    'mkv': ['aac', 'mp3', 'ac3', 'eac3', 'flac', 'opus', 'vorbis', 'dts', 'truehd', 'pcm_s16le', 'pcm_s24le', 'alac'],
    'avi': ['mp3', 'ac3', 'pcm_s16le', 'pcm_s24le', 'u8', 'aac', 'dts'],
    'mov': ['aac', 'alac', 'mp3', 'ac3', 'pcm_s16le', 'pcm_s24le', 'pcm_s32le', 'pcm_f32le', 'adpcm_ima_qt', 'flac'],
    'webm': ['opus', 'vorbis'],
    'flv': ['aac', 'mp3', 'nellymoser', 'adpcm_swf', 'speex'],
    'wmv': ['wmav1', 'wmav2', 'wmapro', 'pcm_s16le'],
    'ogg': ['vorbis', 'flac', 'opus', 'speex'],
    '3gp': ['aac', 'amr_nb', 'amr_wb'],
    'ts':  ['aac', 'mp3', 'ac3', 'eac3', 'mp2', 'dts', 'truehd'],
    'm2ts': ['aac', 'ac3', 'eac3', 'dts', 'truehd', 'pcm_bluray']
}

supported_subtitle_codecs = {
    'mp4': ['mov_text', 'srt', 'webvtt', 'ttml'],  # 'srt' is technically non-standard in MP4 but widely supported as private stream
    'mkv': ['srt', 'ass', 'ssa', 'subrip', 'webvtt', 'pgs', 'dvd_subtitle', 'vobsub', 'text', 'dvb_subtitle', 'hdmv_pgs_subtitle'],
    'avi': ['srt', 'subrip', 'xsub'],
    'mov': ['mov_text', 'srt', 'eia_608'],
    'webm': ['webvtt'],
    'flv': [], # FLV does not natively support subtitles in the traditional sense (often burned in or sidecar)
    'wmv': ['wmv_subtitle'], # Very rare, usually hardcoded or sidecar
    'ogg': [], # Ogg usually uses sidecar, though OggKate exists it is rare
    '3gp': ['mov_text', '3gpp_timed_text'],
    'ts':  ['dvb_subtitle', 'dvb_teletext', 'arib_caption'],
    'm2ts': ['pgs', 'hdmv_pgs_subtitle']
}

# Map the software encoder name to the resulting format
encoder_to_codec_map = {
    'libx264': 'h264',
    'h264_nvenc': 'h264',
    'libx265': 'hevc',
    'hevc_nvenc': 'hevc',
    'libvpx-vp9': 'vp9',
    'libopus': 'opus',
    'libfdk_aac': 'aac'
}

# Codecs that cannot be converted to text (must be burned in or skipped for MP4)
BITMAP_SUBTITLE_CODECS = [
    'hdmv_pgs_subtitle', 'pgs', 'dvd_subtitle', 'dvdsub', 'vobsub', 'xsub'
]

# Resolution map
RESOLUTION_MAP = {
    "2160p": 2160,
    "1080p": 1080,
    "720p": 720,
    "480p": 480
}


# --- Helper Functions ---

_NVIDIA_SUPPORT_CACHE = None

def check_nvidia_gpu_support():
    """Checks if ffmpeg has NVENC (Nvidia GPU) support enabled. Caches the result."""
    global _NVIDIA_SUPPORT_CACHE
    if _NVIDIA_SUPPORT_CACHE is not None:
        return _NVIDIA_SUPPORT_CACHE

    try:
        subprocess.run(
            ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=64x64',
             '-c:v', 'h264_nvenc', '-t', '0.1', '-f', 'null', '-'], ## or hevc_nvenc
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        _NVIDIA_SUPPORT_CACHE = True
    except (subprocess.CalledProcessError, FileNotFoundError):
        _NVIDIA_SUPPORT_CACHE = False
    
    return _NVIDIA_SUPPORT_CACHE


def get_total_frames(file_path, media_info=None):
    if media_info:
        info = media_info
    else:
        info = ffprobe_media_info(file_path)
    
    if not info: return 0

    video_stream = next((s for s in info.get('streams', []) if s['codec_type'] == 'video'), None)
    if not video_stream: return 0

    if 'nb_frames' in video_stream and video_stream['nb_frames'] != 'N/A':
        return int(video_stream['nb_frames'])

    try:
        duration = float(info['format']['duration'])
        fps_str = video_stream.get('r_frame_rate', '30/1')
        num, den = map(int, fps_str.split('/'))
        fps = num / den if den != 0 else 30
        return int(duration * fps)
    except (KeyError, ValueError, ZeroDivisionError):
        return 0


def ffprobe_media_info(file_path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding='utf-8'
        )
        if result.returncode != 0: return None
        return json.loads(result.stdout)
    except Exception as e:
        console.print(f"[bold red]Error analyzing {file_path}: {e}[/bold red]")
        return None


def ffmpeg_build_cmd(input_file: str, output_file: str, media_info=None):
    """
    Returns a tuple: (command_list, warnings_list)
    """
    config = load_config()
    target_res_label = config['converter'].get('resolution', 'original')
    scaling_mode = config['converter'].get('scaling_mode', 'both')

    output_type = output_file.split('.')[-1].lower()
    
    if media_info:
        info = media_info
    else:
        info = ffprobe_media_info(input_file)
        
    if not info:
        return [], [f"Could not probe file: {input_file}"]

    has_nvidia = check_nvidia_gpu_support()
    warnings = []

    ffmpeg_cmd = ['ffmpeg']

    if has_nvidia:
        ffmpeg_cmd.extend(['-hwaccel', 'cuda'])

    ffmpeg_cmd.extend(['-i', input_file])

    # Counters for output streams
    out_v_idx = 0
    out_a_idx = 0
    out_s_idx = 0

    # --- Video ---
    # Only map the FIRST valid video stream to avoid multi-stream issues in containers like MP4
    video_streams = [s for s in info['streams'] if s['codec_type'] == 'video']
    video_mapped = False
    
    for stream in video_streams:
        idx = stream['index']
        codec = stream.get('codec_name', 'unknown')
        pix_fmt = stream.get('pix_fmt', '')
        disposition = stream.get('disposition', {})
        width = stream.get('width')
        height = stream.get('height')
        
        # Skip attached pictures (cover art)
        if disposition.get('attached_pic', 0) == 1:
            warnings.append(f"Skipped Stream #{idx}: Attached picture (cover art) skipped.")
            continue

        if video_mapped:
            warnings.append(f"Skipped Stream #{idx}: Secondary video stream skipped (only main video is kept).")
            continue

        # Check for HDR/10-bit
        is_10bit = '10' in pix_fmt or '12' in pix_fmt or pix_fmt == 'p010le'

        # Determine if scaling is needed
        should_scale = False
        scale_filter = None
        
        if target_res_label != 'original' and height:
            target_height = RESOLUTION_MAP.get(target_res_label)
            if target_height:
                if scaling_mode == 'both':
                    should_scale = height != target_height
                elif scaling_mode == 'downscale_only':
                    should_scale = height > target_height
                elif scaling_mode == 'upscale_only':
                    should_scale = height < target_height
                
                if should_scale:
                    # -2 preserves aspect ratio and ensures width is divisible by 2 (required by some codecs)
                    scale_filter = f"scale=-2:{target_height}"

        # Determine codec strategy
        # If we are scaling, we MUST re-encode, so we can't use 'copy'
        can_copy = (codec in supported_video_codecs.get(output_type, [])) and not should_scale

        if can_copy:
            ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:v:{out_v_idx}', 'copy'])
        else:
            # Re-encoding needed
            output_supports_hevc = 'hevc' in supported_video_codecs.get(output_type, [])
            
            if is_10bit and output_supports_hevc:
                if has_nvidia:
                    # Nvidia HEVC 10-bit
                    ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:v:{out_v_idx}', 'hevc_nvenc', '-pix_fmt', 'p010le'])
                else:
                    # CPU HEVC 10-bit
                    ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:v:{out_v_idx}', 'libx265', '-pix_fmt', 'yuv420p10le', '-x265-params', 'hdr-opt=1'])
            else:
                # Standard SDR encoding (or fallback if container doesn't support HEVC)
                encoder = 'h264_nvenc' if has_nvidia else 'libx264'
                ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:v:{out_v_idx}', encoder])
                
                if is_10bit and not output_supports_hevc:
                     warnings.append(f"Stream #{idx}: Input is 10-bit/HDR but output container '{output_type}' does not support HEVC. HDR metadata may be lost.")
            
            # Apply scaling filter if needed
            if should_scale and scale_filter:
                ffmpeg_cmd.extend(['-vf', scale_filter])

        out_v_idx += 1
        video_mapped = True

    # --- Audio ---
    audio_streams = [s for s in info['streams'] if s['codec_type'] == 'audio']
    for stream in audio_streams:
        idx = stream['index']
        codec = stream.get('codec_name', 'unknown')

        if codec in supported_audio_codecs.get(output_type, []):
            ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:a:{out_a_idx}', 'copy'])
        else:
            best_audio = supported_audio_codecs.get(output_type, ['aac'])[0]
            ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:a:{out_a_idx}', best_audio])
        
        out_a_idx += 1

    # --- Subtitles (Logic modified to catch bitmap subs) ---
    subtitle_streams = [s for s in info['streams'] if s['codec_type'] == 'subtitle']
    for stream in subtitle_streams:
        idx = stream['index']
        codec = stream.get('codec_name', 'unknown').lower()

        # Check for bitmap subtitles in containers that don't support them (like MP4)
        if output_type in ['mp4', 'mov', 'm4v']:
            if codec in BITMAP_SUBTITLE_CODECS:
                # SKIP and Log Warning
                warnings.append(
                    f"Skipped Stream #{idx}: Subtitle codec '{codec}' is image-based and cannot be converted to {output_type} text.")
                continue

        if codec in supported_subtitle_codecs.get(output_type, []):
            ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:s:{out_s_idx}', 'copy'])
        else:
            #if codec not in BITMAP_SUBTITLE_CODECS:
            best_sub = supported_subtitle_codecs.get(output_type, ['mov_text'])[0]
            ffmpeg_cmd.extend(['-map', f'0:{idx}', f'-c:s:{out_s_idx}', best_sub])
        
        out_s_idx += 1

    ffmpeg_cmd.append(output_file)

    ffmpeg_cmd.extend(['-movflags', '+faststart'])
    ffmpeg_cmd.extend(['-progress', 'pipe:1'])
    ffmpeg_cmd.extend(['-nostats', '-loglevel', 'error'])
    ffmpeg_cmd.extend(['-y'])

    return ffmpeg_cmd, warnings
