import subprocess
import json


def get_resolution(video_path):

    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'json',
        video_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)

        # Check if stream data exists
        if 'streams' in data and len(data['streams']) > 0:
            width = int(data['streams'][0]['width'])
            height = int(data['streams'][0]['height'])
            return width, height

    except Exception as e:
        return None

    return None


def get_category_label(width, height):
    # 8K UHD (7680 x 4320)
    # Check: Width ~7680 OR Height ~4320
    if width >= 7000 or height >= 3900:
        return "4320p"

    # 5K (5120 x 2880)
    elif width >= 4800 or height >= 2700:
        return "2880p"

    # 4K UHD (3840 x 2160)
    # Covers standard 4K and "Scope 4K" (3840 x 1600)
    elif width >= 3600 or height >= 2000:
        return "2160p"

    # QHD (2560 x 1440) vs Ultrawide 1080p (2560 x 1080)
    # This is a special case: 2560px width is used by both.
    elif width >= 2500:
        # If height is 1440-ish, it's 1440p. If smaller (like 1080), it's 1080p.
        if height >= 1300:
            return "1440p"
        else:
            return "1080p"  # Likely 21:9 Ultrawide monitor resolution

    # Full HD (1920 x 1080)
    # Covers Standard (1920x1080), Scope (1920x800), and 4:3 (1440x1080)
    elif width >= 1800 or height >= 1000:
        return "1080p"

    # HD+ (1600 x 900)
    elif width >= 1500 or height >= 850:
        return "900p"

    # HD (1280 x 720)
    # Covers Standard (1280x720) and Scope (1280x536)
    elif width >= 1200 or height >= 650:
        return "720p"

    # qHD (960 x 540)
    elif width >= 900 or height >= 500:
        return "540p"

    # SD / VGA (640 x 480)
    # 640x480 is 480p, but 640x360 is 360p. We need a height check here.
    elif width >= 640:
        if height >= 400:
            return "480p"
        else:
            return "360p"

    # Low Res
    elif width >= 480 or height >= 360:
        return "360p"

    else:
        return "SD"