# -*- coding: utf-8 -*-
"""把 raid_images/<key>/ 的原图整理成适合 QQ 发送的切块长图。
输出 raid_images_proc/<key>/seg_NN.jpg（宽 1500，每块高≤3400）。
只在小图(<120px)和损坏文件处跳过。"""
import os
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True
SRC, DST = "raid_images", "raid_images_proc"
TARGET_W, MAX_H, GAP, MIN_DIM = 1500, 3400, 30, 120

for key in sorted(os.listdir(SRC)):
    sdir = os.path.join(SRC, key)
    if not os.path.isdir(sdir) or key == "dl.sh":
        continue
    imgs = []
    for fn in sorted(os.listdir(sdir)):
        p = os.path.join(sdir, fn)
        try:
            im = Image.open(p)
            im = im.convert("RGB")
            if min(im.size) < MIN_DIM:
                continue
            if im.width > TARGET_W:
                im = im.resize((TARGET_W, round(im.height * TARGET_W / im.width)), Image.LANCZOS)
            imgs.append(im)
        except Exception as e:
            print("skip", p, e)
    if not imgs:
        continue
    width = max(i.width for i in imgs)
    total_h = sum(i.height for i in imgs) + GAP * (len(imgs) - 1)
    sheet = Image.new("RGB", (width, total_h), "white")
    y = 0
    for im in imgs:
        sheet.paste(im, ((width - im.width) // 2, y))
        y += im.height + GAP
    ddir = os.path.join(DST, key)
    os.makedirs(ddir, exist_ok=True)
    for f in os.listdir(ddir):
        os.remove(os.path.join(ddir, f))
    n, seg_h = 0, 0
    while seg_h < total_h:
        box = (0, seg_h, width, min(seg_h + MAX_H, total_h))
        seg = sheet.crop(box)
        n += 1
        seg.save(os.path.join(ddir, f"seg_{n:02d}.jpg"), quality=80)
        seg_h = box[3]
    print(key, len(imgs), "imgs ->", n, "segments,",
          round(os.path.getsize(os.path.join(ddir, 'seg_01.jpg')) / 1024), "KB first seg")
print("done")
