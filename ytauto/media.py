"""음성(TTS), 장면 이미지, 영상 합성."""
import asyncio
import io
import os
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .common import ROOT, log

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT_CANDIDATES = [
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/blackhansans/BlackHanSans-Regular.ttf"


def find_font(config):
    custom = config["video"].get("font_path")
    if custom and Path(custom).exists():
        return custom
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    local = ROOT / "assets" / "fonts" / "BlackHanSans-Regular.ttf"
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(requests.get(FONT_URL, timeout=60).content)
    return str(local)


# ---------- 음성 ----------

def synthesize(text, out_path, config):
    import edge_tts

    async def run():
        await edge_tts.Communicate(
            text, config["tts"]["voice"], rate=config["tts"].get("rate", "+0%")
        ).save(str(out_path))

    asyncio.run(run())


def duration(path):
    proc = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    if not m:
        raise RuntimeError(f"길이를 읽을 수 없음: {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


# ---------- 이미지 ----------

def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    t, b = _hex(top), _hex(bottom)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        r = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * r) for i in range(3)))
    return img


def fetch_background(keyword, w, h):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": keyword, "orientation": "portrait", "per_page": 5},
            headers={"Authorization": key},
            timeout=20,
        )
        photos = r.json().get("photos", [])
        if not photos:
            return None
        img_bytes = requests.get(photos[0]["src"]["large2x"], timeout=30).content
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except (requests.RequestException, ValueError, OSError) as e:
        log.warning("배경 사진 실패(%s): %s", keyword, e)
        return None
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    return ImageEnhance.Brightness(img.filter(ImageFilter.GaussianBlur(1))).enhance(0.55)


def _wrap(draw, text, font, max_width):
    lines, line = [], ""
    for ch in text:
        if draw.textlength(line + ch, font=font) > max_width and line:
            lines.append(line.strip())
            line = ch
        else:
            line += ch
    if line.strip():
        lines.append(line.strip())
    return lines


def render_scene(caption, keyword, series, index, total, out_path, config, font_path):
    w, h = config["video"]["width"], config["video"]["height"]
    img = fetch_background(keyword, w, h) or _gradient(w, h, *series["color"])
    draw = ImageDraw.Draw(img)

    # 상단 시리즈 라벨
    label_font = ImageFont.truetype(font_path, 52)
    label = f"  {series['name']}  "
    lw = draw.textlength(label, font=label_font)
    draw.rounded_rectangle([(w - lw) / 2 - 10, 170, (w + lw) / 2 + 10, 260], radius=40, fill=(0, 0, 0))
    draw.text((w / 2, 215), label, font=label_font, fill=(255, 221, 87), anchor="mm")

    # 가운데 큰 자막
    font = ImageFont.truetype(font_path, 96)
    lines = _wrap(draw, caption, font, w - 160)[:4]
    line_h = 130
    y = h / 2 - line_h * (len(lines) - 1) / 2
    for line in lines:
        draw.text((w / 2, y), line, font=font, fill=(255, 255, 255), anchor="mm",
                  stroke_width=8, stroke_fill=(0, 0, 0))
        y += line_h

    # 하단 진행 표시
    bar_w = (w - 200) / total
    for i in range(total):
        color = (255, 255, 255) if i <= index else (90, 90, 90)
        draw.rounded_rectangle([100 + i * bar_w + 6, h - 260, 100 + (i + 1) * bar_w - 6, h - 248],
                               radius=6, fill=color)
    img.save(out_path, "PNG")


# ---------- 영상 ----------

def _run(args):
    proc = subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr[-800:]}")


def build_video(script, series, workdir, config):
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    font_path = find_font(config)
    fps = config["video"]["fps"]
    w, h = config["video"]["width"], config["video"]["height"]
    scenes = script["scenes"]
    segments = []
    for i, scene in enumerate(scenes):
        audio = workdir / f"s{i}.mp3"
        image = workdir / f"s{i}.png"
        segment = workdir / f"s{i}.mp4"
        synthesize(scene["narration"], audio, config)
        render_scene(scene["caption"], scene["image_keyword"], series, i, len(scenes), image, config, font_path)
        dur = duration(audio) + 0.25
        frames = int(dur * fps) + 1
        zoom = f"zoompan=z='min(zoom+0.0007,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}"
        _run([
            "-loop", "1", "-framerate", str(fps), "-i", str(image), "-i", str(audio),
            "-vf", f"{zoom},format=yuv420p", "-af", "apad", "-t", f"{dur:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(segment),
        ])
        segments.append(segment)

    concat_list = workdir / "list.txt"
    concat_list.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    joined = workdir / "joined.mp4"
    _run(["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(joined)])

    final = workdir / "final.mp4"
    music = config["video"].get("background_music")
    if music and Path(music).exists():
        _run([
            "-i", str(joined), "-stream_loop", "-1", "-i", music,
            "-filter_complex", "[1:a]volume=0.08[m];[0:a][m]amix=inputs=2:duration=first[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", str(final),
        ])
    else:
        joined.replace(final)
    total = duration(final)
    if total > 180:
        log.warning("영상 길이 %.1f초 - 쇼츠 한도(3분)를 넘어 일반 영상으로 올라가요", total)
    return final, total
