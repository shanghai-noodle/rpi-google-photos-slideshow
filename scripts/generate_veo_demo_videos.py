#!/usr/bin/env python3
"""Generate lively cats & dogs demo MP4 videos using the Gemini Veo API.

Usage:
    GEMINI_API_KEY="your-api-key" python3 scripts/generate_veo_demo_videos.py
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_DIR = os.path.join(BASE_DIR, "demo_media")

VEO_CLIPS = [
    {
        "filename": "vid_p01_golden_puppy.mp4",
        "aspect_ratio": "9:16",
        "prompt": (
            "A photorealistic vertical video of a fluffy Golden Retriever puppy "
            "happily bounding toward the camera through a sunlit meadow of wildflowers, "
            "wagging its tail energetically, ears flopping, golden hour sunlight, 4k DSLR quality."
        ),
    },
    {
        "filename": "vid_p02_orange_kitten.mp4",
        "aspect_ratio": "9:16",
        "prompt": (
            "A photorealistic vertical video of an adorable ginger tabby kitten "
            "playfully batting at a dangling feather toy on a cozy knitted blanket by a sunny window, "
            "pouncing and tilting its head curiously, natural morning sunlight."
        ),
    },
    {
        "filename": "vid_p03_husky_snow_play.mp4",
        "aspect_ratio": "9:16",
        "prompt": (
            "A photorealistic vertical video of a joyful Siberian Husky dog "
            "leaping and tossing fresh powdery snow into the air with its nose in a sunlit winter forest, "
            "wagging its tail playfully, crisp slow-motion snow sparkles."
        ),
    },
    {
        "filename": "vid_l01_corgi_beach.mp4",
        "aspect_ratio": "16:9",
        "prompt": (
            "A photorealistic widescreen video of a happy Pembroke Welsh Corgi dog "
            "running joyfully along wet ocean sand at sunset, splashing through shallow golden waves, "
            "ears perked and big smile, cinematic tracking shot."
        ),
    },
    {
        "filename": "vid_l02_kittens_playing.mp4",
        "aspect_ratio": "16:9",
        "prompt": (
            "A photorealistic widescreen video of two fluffy kittens playfully tumbling "
            "and chasing a yarn ball across a sunlit wooden floor in a warm living room, "
            "lively natural feline motion, shallow depth of field."
        ),
    },
    {
        "filename": "vid_l03_aussie_frisbee_leap.mp4",
        "aspect_ratio": "16:9",
        "prompt": (
            "A photorealistic widescreen video of an energetic Australian Shepherd dog "
            "sprinting across a lush green park at golden hour and leaping high into the air "
            "to catch a flying frisbee, dynamic action shot."
        ),
    },
]


def generate_veo_video(api_key: str, model: str, prompt: str, aspect_ratio: str, out_path: str) -> bool:
    """Call Gemini Veo predictLongRunning endpoint and save the resulting MP4."""
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    url = f"{base_url}/models/{model}:predictLongRunning?key={api_key}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "aspectRatio": aspect_ratio,
            "sampleCount": 1,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"Starting Veo generation ({model}, {aspect_ratio}) for {os.path.basename(out_path)}...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        op = json.loads(resp.read().decode("utf-8"))

    op_name = op.get("name")
    if not op_name:
        raise RuntimeError(f"Unexpected Veo response: {op}")

    poll_url = f"{base_url}/{op_name}?key={api_key}"
    while True:
        time.sleep(8)
        with urllib.request.urlopen(poll_url, timeout=60) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        if status.get("done"):
            break
        print(f"  ...waiting on {op_name}...")

    if "error" in status:
        raise RuntimeError(f"Veo generation failed: {status['error']}")

    response_data = status.get("response", {})
    samples = (
        response_data.get("generateVideoResponse", {}).get("generatedSamples")
        or response_data.get("predictions")
        or []
    )
    if not samples:
        raise RuntimeError(f"No video samples found in response: {status}")

    sample = samples[0]
    video_obj = sample.get("video", sample)
    if "bytesBase64Encoded" in video_obj:
        raw_bytes = base64.b64decode(video_obj["bytesBase64Encoded"])
    elif "uri" in video_obj:
        dl_url = video_obj["uri"]
        sep = "&" if "?" in dl_url else "?"
        with urllib.request.urlopen(f"{dl_url}{sep}key={api_key}", timeout=120) as dl_resp:
            raw_bytes = dl_resp.read()
    else:
        raise RuntimeError(f"Unrecognized video payload: {sample}")

    with open(out_path, "wb") as f:
        f.write(raw_bytes)

    # Extract poster frame (.mp4.jpg) for multi-tile collage fallback & fast preview
    poster_path = f"{out_path}.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-i", out_path, "-frames:v", "1", "-q:v", "2", poster_path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Saved {out_path} ({len(raw_bytes) // 1024} KB) and poster {poster_path}")
    return True


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Error: Please set GEMINI_API_KEY in your environment.", file=sys.stderr)
        return 1

    model = os.environ.get("VEO_MODEL", "veo-2.0-generate-001")
    os.makedirs(DEMO_DIR, exist_ok=True)

    for clip in VEO_CLIPS:
        out_path = os.path.join(DEMO_DIR, clip["filename"])
        generate_veo_video(api_key, model, clip["prompt"], clip["aspect_ratio"], out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
