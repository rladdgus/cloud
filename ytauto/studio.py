"""자체 스튜디오: 제품 사진 → (선택) Kling 모션 클립 → 단어 단위 자막 + 음성 → 9:16 쇼츠.

화면 구성 (1080x1920)
  ┌ 광고 표시 (작게)
  ├ 훅 제목 (영상 내내 고정)
  ├ 제품 카드 960x960 (모션 클립 또는 천천히 확대되는 사진)
  ├ 단어 단위 자막 (말하는 부분만 표시)
  └ 진행 막대
"""
import asyncio
import base64
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import media
from .common import log

W, H = 1080, 1920
CARD, CARD_X, CARD_Y = 960, 60, 330
CAPTION_Y = 1480
MOTION_SUFFIX = (" Keep the product's exact shape, color, logo and text unchanged. "
                 "Smooth, subtle camera motion, clean commercial lighting.")
MOTION_NEGATIVE = "deformed product, changed logo, altered text, extra products, hands with extra fingers, blur"


# ---------- 음성 + 단어 타이밍 ----------

def _edge_tts(text, out_path, config):
    import edge_tts

    words = []

    async def run():
        comm = edge_tts.Communicate(text, config["tts"]["voice"], rate=config["tts"].get("rate", "+0%"),
                                    boundary="WordBoundary")
        with open(out_path, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append((chunk["text"], start, start + chunk["duration"] / 1e7))

    asyncio.run(run())
    return words


def _elevenlabs(text, out_path, config):
    voice = config["tts"]["elevenlabs_voice_id"]
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
        json={"text": text, "model_id": config["tts"].get("elevenlabs_model", "eleven_multilingual_v2")},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    Path(out_path).write_bytes(base64.b64decode(data["audio_base64"]))
    a = data["alignment"]
    words, cur, start, end = [], "", None, 0.0
    for ch, s, e in zip(a["characters"], a["character_start_times_seconds"], a["character_end_times_seconds"]):
        if ch.isspace():
            if cur:
                words.append((cur, start, end))
            cur, start = "", None
            continue
        if start is None:
            start = s
        cur, end = cur + ch, e
    if cur:
        words.append((cur, start, end))
    return words


def narrate(text, out_path, config):
    """음성 파일을 만들고 [(단어, 시작초, 끝초)]를 돌려준다."""
    if config["tts"].get("engine") == "elevenlabs" and os.environ.get("ELEVENLABS_API_KEY"):
        return _elevenlabs(text, out_path, config)
    return _edge_tts(text, out_path, config)


def caption_chunks(words, max_chars=10):
    """단어를 화면에 띄울 짧은 덩어리로 묶는다."""
    chunks, cur = [], []
    for w in words:
        if cur and len(" ".join(x[0] for x in cur + [w])) > max_chars:
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    out = []
    for i, c in enumerate(chunks):
        end = chunks[i + 1][0][1] if i + 1 < len(chunks) else c[-1][2] + 0.3
        out.append((" ".join(x[0] for x in c), c[0][1], end))
    return out


# ---------- Kling 모션 클립 (fal.ai) ----------

def _fal_video(image_url, prompt, out_path, config):
    model = config["studio"]["motion"]["model"]
    headers = {"Authorization": f"Key {os.environ['FAL_KEY']}"}
    r = requests.post(f"https://queue.fal.run/{model}", headers=headers, timeout=60, json={
        "prompt": prompt + MOTION_SUFFIX, "image_url": image_url,
        "duration": "5", "negative_prompt": MOTION_NEGATIVE,
    })
    r.raise_for_status()
    job = r.json()
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(8)
        status = requests.get(job["status_url"], headers=headers, timeout=60).json()
        if status.get("status") == "COMPLETED":
            result = requests.get(job["response_url"], headers=headers, timeout=60).json()
            url = result["video"]["url"]
            Path(out_path).write_bytes(requests.get(url, timeout=300).content)
            return out_path
        if status.get("status") not in ("IN_QUEUE", "IN_PROGRESS"):
            raise RuntimeError(f"fal 작업 실패: {status}")
    raise TimeoutError("fal 작업 시간 초과")


def motion_clips(image_url, scenes, workdir, config):
    """앞쪽 장면부터 max_clips개까지 모션 클립을 동시에 만든다. 실패한 장면은 None (사진으로 대체)."""
    motion = config["studio"]["motion"]
    clips = [None] * len(scenes)
    if not (motion.get("enabled") and os.environ.get("FAL_KEY") and image_url):
        return clips
    targets = list(range(min(motion.get("max_clips", 2), len(scenes))))

    def job(i):
        try:
            return _fal_video(image_url, scenes[i].get("motion_prompt", "slow push-in on the product"),
                              workdir / f"m{i}.mp4", config)
        except Exception as e:
            log.warning("모션 클립 %d 실패 (사진으로 대체): %s", i, e)
            return None

    with ThreadPoolExecutor(max_workers=len(targets) or 1) as pool:
        for i, path in zip(targets, pool.map(job, targets)):
            clips[i] = path
    return clips


# ---------- 그림 ----------

def _font(config, size):
    return ImageFont.truetype(media.find_font(config), size)


def base_frame(product_image, hook, notice, index, total, config, item_no=None):
    """자막을 뺀 고정 화면 (배경, 광고 표시, 훅 제목, 진행 막대)."""
    img = ImageEnhance.Brightness(
        media._cover(product_image, W, H).filter(ImageFilter.GaussianBlur(40))).enhance(0.35)
    draw = ImageDraw.Draw(img)
    f_notice = _font(config, 32)
    draw.text((W / 2, 70), notice, font=f_notice, fill=(220, 220, 220), anchor="mm")

    f_hook = _font(config, 84)
    lines = media._wrap(draw, hook, f_hook, W - 120)[:2]
    y = 200 - (len(lines) - 1) * 50
    for line in lines:
        draw.text((W / 2, y), line, font=f_hook, fill=(255, 225, 60), anchor="mm",
                  stroke_width=7, stroke_fill=(0, 0, 0))
        y += 100

    draw.rounded_rectangle([CARD_X - 6, CARD_Y - 6, CARD_X + CARD + 6, CARD_Y + CARD + 6], radius=36,
                           fill=(255, 255, 255))
    if item_no:
        # 카드 아래 고정 배지: 시청자가 번호를 기억하도록
        f_chip = _font(config, 46)
        chip = f"프로필 링크 {item_no}번"
        cw = draw.textlength(chip, font=f_chip)
        draw.rounded_rectangle([(W - cw) / 2 - 28, 1672, (W + cw) / 2 + 28, 1744], radius=36,
                               fill=(255, 225, 60))
        draw.text((W / 2, 1708), chip, font=f_chip, fill=(20, 20, 20), anchor="mm")
    bar_w = (W - 200) / total
    for i in range(total):
        color = (255, 225, 60) if i <= index else (80, 80, 80)
        draw.rounded_rectangle([100 + i * bar_w + 6, H - 150, 100 + (i + 1) * bar_w - 6, H - 138],
                               radius=6, fill=color)
    return img


def caption_png(text, path, config):
    """한 줄에 들어가도록 글자 크기를 줄이고, 그래도 넘치면 단어 단위로 두 줄로 나눈다."""
    img = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for size in (104, 96, 88, 80):
        font = _font(config, size)
        if draw.textlength(text, font=font) <= W - 100:
            lines = [text]
            break
    else:
        words = text.split()
        half = max(1, len(words) // 2)
        lines = [" ".join(words[:half]), " ".join(words[half:])] if len(words) > 1 else [text]
    step = size + 20
    y = 130 - (len(lines) - 1) * step / 2
    for line in lines:
        draw.text((W / 2, y), line, font=font, fill=(255, 255, 255), anchor="mm",
                  stroke_width=10, stroke_fill=(0, 0, 0))
        y += step
    img.save(path)


# ---------- 합성 ----------

def build(script, product, product_image, workdir, config, item_no=None):
    workdir = Path(workdir)
    scenes = script["scenes"]
    fps = config["video"]["fps"]
    notice = config["shopping"]["disclosure_short"]
    card_png = workdir / "card.png"
    card = product_image.copy()
    card.thumbnail((CARD, CARD))
    canvas = Image.new("RGB", (CARD, CARD), (255, 255, 255))
    canvas.paste(card, ((CARD - card.width) // 2, (CARD - card.height) // 2))
    canvas.save(card_png)

    clips = motion_clips(product.get("image_hd") or product.get("image"), scenes, workdir, config)
    segments = []
    for i, scene in enumerate(scenes):
        audio = workdir / f"a{i}.mp3"
        words = narrate(scene["narration"], audio, config)
        dur = media.duration(audio) + 0.2
        base = workdir / f"b{i}.png"
        base_frame(product_image, script["hook_title"], notice, i, len(scenes), config, item_no).save(base)

        caps = caption_chunks(words) or [(scene["caption"], 0, dur)]
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", str(base)]
        if clips[i]:
            inputs += ["-stream_loop", "-1", "-i", str(clips[i])]
            card_filter = (f"[1:v]scale={CARD}:{CARD}:force_original_aspect_ratio=increase,"
                           f"crop={CARD}:{CARD},setsar=1,fps={fps}[card]")
        else:
            frames = int(dur * fps) + 1
            inputs += ["-loop", "1", "-framerate", str(fps), "-i", str(card_png)]
            card_filter = (f"[1:v]zoompan=z='min(zoom+0.0009,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                           f":d={frames}:s={CARD}x{CARD}:fps={fps}[card]")
        inputs += ["-i", str(audio)]
        filters = [card_filter, f"[0:v][card]overlay={CARD_X}:{CARD_Y}[v0]"]
        last = "v0"
        for j, (text, start, end) in enumerate(caps):
            png = workdir / f"c{i}_{j}.png"
            caption_png(text, png, config)
            inputs += ["-i", str(png)]
            filters.append(f"[{last}][{3 + j}:v]overlay=0:{CAPTION_Y - 130}"
                           f":enable='between(t,{start:.2f},{min(end, dur):.2f})'[v{j + 1}]")
            last = f"v{j + 1}"
        filters.append(f"[{last}]format=yuv420p[out]")
        seg = workdir / f"s{i}.mp4"
        media._run([
            *inputs, "-filter_complex", ";".join(filters), "-map", "[out]", "-map", "2:a",
            "-af", "apad", "-t", f"{dur:.2f}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(seg),
        ])
        segments.append(seg)

    concat = workdir / "list.txt"
    concat.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    joined = workdir / "joined.mp4"
    media._run(["-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)])
    return media.mix_music(joined, workdir / "studio.mp4", config)
