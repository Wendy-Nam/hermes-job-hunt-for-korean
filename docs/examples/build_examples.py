#!/usr/bin/env python3
"""예시 산출물 재생성 (docs/examples/).

wiki/ 의 가상 인물 자료로 생성기를 돌려 output/ 에 HTML·PDF를 만들고,
첫 페이지들을 모아 README용 미리보기 몽타주(preview.png)를 그린다.

필요: Chrome/Chromium (CHROME_PATH로 지정 가능), 개발용 패키지 pymupdf · pillow
  python3 -m pip install pymupdf pillow
  CHROME_PATH=/path/to/chrome python3 docs/examples/build_examples.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GENERATOR = ROOT / "bundle" / "job-search" / "bin" / "generate-tailored-resume.py"
OUT = HERE / "output"
COMPANY, POSITION = "에이크미코리아", "Sales Operations Manager"
JD = "Salesforce 파이프라인 매출 예측 CRM 자동화 협업"

# 생성기 출력 접미사 → 예시 파일 이름
DOCS = {
    ("ko", "Resume"): "resume_ko",
    ("en", "Resume"): "resume_en",
    ("ko", "CoverLetter"): "cover_letter_ko",
    ("ko", "Portfolio"): "portfolio_ko",
}
# 몽타주 칸: (예시 파일, 페이지 번호, 라벨)
TILES = [
    ("resume_ko", 1, "국문 이력서 · 1쪽"),
    ("resume_ko", 2, "국문 이력서 · 2쪽"),
    ("resume_en", 1, "영문 이력서"),
    ("cover_letter_ko", 1, "자기소개서"),
    ("portfolio_ko", 1, "포트폴리오 · 1쪽"),
    ("portfolio_ko", 2, "포트폴리오 · 2쪽"),
]


def generate() -> None:
    env = {**os.environ, "WIKI_PATH": str(HERE / "wiki")}
    OUT.mkdir(exist_ok=True)
    for lang in ("ko", "en"):
        with tempfile.TemporaryDirectory() as tmp:
            # 포트폴리오 이미지 상대 경로(images/...)가 PDF 변환 때도 풀리도록 같은 폴더에 둔다
            shutil.copytree(OUT / "images", Path(tmp) / "images")
            subprocess.run([sys.executable, str(GENERATOR), "-c", COMPANY, "-p", POSITION,
                            "--jd-text", JD, "--lang", lang, "--portfolio", "--pdf", "--out", tmp],
                           check=True, env=env, stdout=subprocess.DEVNULL)
            for (doc_lang, suffix), name in DOCS.items():
                if doc_lang != lang:
                    continue
                for ext in ("html", "pdf"):
                    src = next(Path(tmp).glob(f"*_{suffix}.{ext}"))
                    shutil.copy(src, OUT / f"{name}.{ext}")


def montage() -> None:
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont

    width, gap, cols = 520, 36, 3
    pages = []
    for name, page_no, label in TILES:
        doc = pymupdf.open(OUT / f"{name}.pdf")
        page = doc[min(page_no, len(doc)) - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(width / page.rect.width, width / page.rect.width))
        pages.append((Image.frombytes("RGB", (pix.width, pix.height), pix.samples), label))

    tile_h = pages[0][0].height
    label_h = 44
    rows = (len(pages) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * width + (cols + 1) * gap,
                               rows * (tile_h + label_h) + (rows + 1) * gap), "#eceef1")
    draw = ImageDraw.Draw(canvas)
    font = None
    for cand in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                 "/System/Library/Fonts/AppleSDGothicNeo.ttc"):
        if Path(cand).is_file():
            font = ImageFont.truetype(cand, 20)
            break
    for i, (img, label) in enumerate(pages):
        x = gap + (i % cols) * (width + gap)
        y = gap + (i // cols) * (tile_h + label_h + gap)
        draw.rectangle([x + 3, y + 4, x + width + 3, y + tile_h + 4], fill="#d5d8dd")  # 그림자
        canvas.paste(img, (x, y))
        draw.rectangle([x, y, x + width - 1, y + tile_h - 1], outline="#d5d8dd")
        draw.text((x + 2, y + tile_h + 12), label, fill="#3f4650", font=font)
    canvas.save(HERE / "preview.png", optimize=True)


if __name__ == "__main__":
    generate()
    montage()
    print(f"OK → {OUT} , {HERE / 'preview.png'}")
