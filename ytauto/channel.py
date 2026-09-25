"""채널 꾸미기: 프로필·배너 이미지 생성, 설명·키워드·배너를 API로 설정."""
from PIL import ImageDraw, ImageFont
from googleapiclient.http import MediaFileUpload

from . import media, youtube
from .common import ROOT, log

ASSETS = ROOT / "assets" / "channel"


def make_images(config):
    ASSETS.mkdir(parents=True, exist_ok=True)
    name = config["channel"]["name"]
    top, bottom = config["channel"].get("colors", ["#f59e0b", "#b45309"])
    font_path = media.find_font(config)
    profile = media._gradient(800, 800, top, bottom)
    draw = ImageDraw.Draw(profile)
    short = config["channel"].get("short_name") or name[:4]
    font = ImageFont.truetype(font_path, 200 if len(short) <= 3 else 150)
    draw.text((400, 400), short, font=font, fill=(255, 255, 255), anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0))
    profile.save(ASSETS / "profile.png")

    # 배너: 모든 기기에서 보이는 가운데 1546x423 영역 안에만 글자를 둔다
    banner = media._gradient(2560, 1440, top, bottom)
    draw = ImageDraw.Draw(banner)
    draw.text((1280, 680), name, font=ImageFont.truetype(font_path, 150), fill=(255, 255, 255), anchor="mm",
              stroke_width=6, stroke_fill=(0, 0, 0))
    tagline = config["channel"].get("tagline", "")
    if tagline:
        draw.text((1280, 820), tagline, font=ImageFont.truetype(font_path, 64), fill=(255, 255, 255), anchor="mm")
    banner.save(ASSETS / "banner.png")
    return ASSETS / "profile.png", ASSETS / "banner.png"


def apply(config):
    profile, banner = make_images(config)
    yt = youtube.service()
    channel = youtube.my_channel(yt)
    banner_url = yt.channelBanners().insert(
        media_body=MediaFileUpload(str(banner), mimetype="image/png")).execute()["url"]
    ch = config["channel"]
    yt.channels().update(part="brandingSettings", body={
        "id": channel["id"],
        "brandingSettings": {
            "channel": {
                "title": ch["name"],
                "description": ch.get("about", ""),
                "keywords": " ".join(f'"{k}"' if " " in k else k for k in ch.get("keywords", [])),
                "defaultLanguage": ch["language"],
                "country": "KR",
            },
            "image": {"bannerExternalUrl": banner_url},
        },
    }).execute()
    log.info("채널 설명·키워드·배너를 설정했어요. 프로필 사진(%s)은 YouTube Studio에서 직접 올려 주세요.", profile)
