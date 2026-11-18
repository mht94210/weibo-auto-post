"""配图(占位版).

红线: 不爬热搜配图、不去水印、不盗用他人图片.
v1: PIL 生成原创话题标题卡(话题词 + 排名 + 热度 + 底部小字),
纯原创, 无版权问题, 可直接配文发.
以后可接:
- 免费图库: Pexels / Unsplash API(需申请 API key, 只用明确授权的图),
  实现 fetch_free_stock_image(keyword) 即可;
- 大模型生图: 实现 generate_ai_image(prompt) 即可.
两个口子都保持"输入话题词, 输出本地图片路径"的约定.
"""
from __future__ import annotations

import random
from pathlib import Path

from hotsearch import HotTopic

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - 服务器上 pip install pillow 即可
    Image = None  # type: ignore[assignment]

PALETTES = [
    ((255, 243, 224), (33, 33, 33), (191, 54, 12)),
    ((232, 245, 233), (27, 94, 32), (46, 125, 50)),
    ((227, 242, 253), (13, 71, 161), (21, 101, 192)),
    ((252, 228, 236), (136, 14, 79), (173, 20, 87)),
]

CARD_W, CARD_H = 1080, 720


def _font(size: int):
    for path in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap(text: str, width: int = 14) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if len(cur) >= width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_title_card(topic: HotTopic, out_dir: Path,
                    rng: random.Random | None = None) -> Path:
    """生成一张话题标题卡, 返回本地路径. 无 Pillow 时抛错, 由调用方降级为纯文字."""
    if Image is None:
        raise RuntimeError("Pillow 未安装: pip install pillow 后再配图")
    rng = rng or random
    bg, fg, accent = rng.choice(PALETTES)
    img = Image.new("RGB", (CARD_W, CARD_H), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, CARD_W, 16], fill=accent)
    draw.text((60, 60), f"微博热搜 · 第 {topic.rank} 名",
              font=_font(44), fill=accent)
    lines = _wrap(topic.word, 12)
    y = 180
    for line in lines[:3]:
        draw.text((60, y), line, font=_font(84), fill=fg)
        y += 110
    draw.text((60, CARD_H - 90), f"热度 {topic.heat}  · 话题讨论",
              font=_font(40), fill=accent)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in topic.word if c.isalnum() or c in ("-", "_"))[:20] or "topic"
    path = out_dir / f"card_{topic.rank}_{safe}.png"
    img.save(path)
    return path


def fetch_free_stock_image(keyword: str, out_dir: Path) -> Path:
    """【占位, 未实现】以后接 Pexels/Unsplash 免费图库时在这里实现.
    要求: 只用 API 返回的、明确可免费商用的图, 保留作者信息, 不去水印."""
    raise NotImplementedError(
        "免费图库未接入: 需申请 Pexels/Unsplash API key 后再实现; 当前用 make_title_card() 兜底.")


def generate_ai_image(topic: HotTopic, out_dir: Path) -> Path:
    """【占位, 未实现】以后接大模型生图时在这里实现.
    要求: 提示词只描述话题氛围, 不复刻他人图片, 不生成带水印/带他人肖像的内容."""
    raise NotImplementedError(
        "AI 生图未接入: 需模型 API 后再实现; 当前用 make_title_card() 兜底.")
