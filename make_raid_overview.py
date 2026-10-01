# -*- coding: utf-8 -*-
"""把 raid_images_proc 下全部副本掉落表拼成一张汇总图（供人工核对）。
输出 raid_overview.jpg：3 列网格，每格内按原始比例缩放放完整长图。"""
import os
from PIL import Image, ImageDraw, ImageFont
import raid_loot

COLS, CELL_W, CELL_H, LABEL_H, PAD = 3, 820, 1500, 70, 16
FONT = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 44)

keys = [k for k in raid_loot.CHARTS if raid_loot.segments(k)]
rows = (len(keys) + COLS - 1) // COLS
W = COLS * CELL_W + (COLS + 1) * PAD
H = rows * (CELL_H + LABEL_H) + (rows + 1) * PAD
sheet = Image.new("RGB", (W, H), (18, 18, 24))
draw = ImageDraw.Draw(sheet)

for i, key in enumerate(keys):
    r, c = divmod(i, COLS)
    x0 = PAD + c * (CELL_W + PAD)
    y0 = PAD + r * (CELL_H + LABEL_H + PAD)
    segs = raid_loot.segments(key)
    # 该副本完整长图（与 preprocess 相同的拼接逻辑）
    ims = [Image.open(s).convert("RGB") for s in segs]
    w = max(im.width for im in ims)
    total_h = sum(im.height for im in ims) + 30 * (len(ims) - 1)
    chart = Image.new("RGB", (w, total_h), "white")
    y = 0
    for im in ims:
        chart.paste(im, ((w - im.width) // 2, y))
        y += im.height + 30
    scale = min(CELL_W / w, CELL_H / total_h)
    chart = chart.resize((round(w * scale), round(total_h * scale)), Image.LANCZOS)
    # 格子底色 + 居中
    draw.rectangle([x0, y0, x0 + CELL_W, y0 + LABEL_H + CELL_H], fill=(35, 35, 45))
    label = f"{raid_loot.chart_display(key)} ({len(segs)}块)"
    draw.text((x0 + CELL_W // 2, y0 + LABEL_H // 2), label,
              font=FONT, fill=(255, 220, 80), anchor="mm")
    sheet.paste(chart, (x0 + (CELL_W - chart.width) // 2,
                        y0 + LABEL_H + (CELL_H - chart.height) // 2))

sheet.save("raid_overview.jpg", quality=80)
print("saved", sheet.size, round(os.path.getsize("raid_overview.jpg") / 1024), "KB", len(keys), "charts")
