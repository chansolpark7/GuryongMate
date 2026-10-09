"""L2 Figma 대조 이미지: 왼쪽 Figma 프레임, 오른쪽 앱 캡처 (L1 cmp 와 같은 810×894 형식).

    python docs/app/review/l2/make_cmp.py
앱 캡처는 app/ 의 `flutter test test/capture_trip_test.dart --dart-define=CAPTURE=true` (fx_*) 와
integration_test/trip_flow_test.dart (it_*, build/screens_l2 에서 복사) 결과다.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
FONT = "C:/Windows/Fonts/malgunbd.ttf"
SMALL = "C:/Windows/Fonts/malgun.ttf"

# (cmp 이름, 제목, Figma 파일, 앱 파일, 앱 설명)
PAIRS = [
    ("11", "11 · Figma", "11_trip_start", "fx_11_trip_start", "앱 (녹화 fixture)"),
    ("12", "12 · Figma", "12_not_trip_day", "fx_12_not_trip_day", "앱 (녹화 fixture)"),
    ("13", "13 · Figma", "13_location", "fx_13_location", "앱 (녹화 fixture)"),
    ("14", "14 · Figma", "14_today", "fx_14_today", "앱 (장면 ④ 데이터)"),
    ("14-live", "14 · Figma", "14_today", "it_14_today_visiting", "앱 (각본 LLM 스택 + 재생기)"),
    ("14-S1", "14-S1 · Figma", "14s1_offline", "fx_14s1_offline", "앱 (녹화 fixture)"),
    ("14-S2", "14-S2 · Figma", "14s2_idle_demo", "fx_14s2_idle_demo", "앱 (녹화 fixture)"),
    ("14-S3", "14-S3 · Figma", "14s3_fab_preview", "fx_14s3_fab_preview", "앱 (장면 ③ 녹화)"),
    ("14-S3-live", "14-S3 · Figma", "14s3_fab_preview", "it_14s3_delay_peek", "앱 (각본 LLM 스택 + 재생기)"),
    ("15", "15 · Figma", "15_moving", "fx_15_moving", "앱 (장면 ④ 녹화)"),
    ("16", "16 · Figma", "16_arrival", "fx_16_arrival", "앱 (장면 ② 녹화)"),
    ("16-live", "16 · Figma", "16_arrival", "it_16_arrival", "앱 (각본 LLM 스택 + 재생기)"),
    ("16-S1", "16-S1 · Figma", "16s1_no_note", "fx_16s1_no_note", "앱 (노트 없음)"),
    ("17", "17 · Figma", "17_guide_note", "fx_17_guide_note", "앱 (f4 노트)"),
    ("18", "18 · Figma", "18_docent_chat", "fx_18_docent_chat", "앱 (f4 SSE)"),
    ("18-live", "18 · Figma", "18_docent_chat", "it_18_docent_chat", "앱 (각본 LLM 스택)"),
    ("19", "19 · Figma", "19_proactive_delay", "fx_19_proactive_delay", "앱 (f5 선제 세션)"),
    ("19-live", "19 · Figma", "19_proactive_delay", "it_19_proactive_delay", "앱 (각본 LLM 스택 + 재생기)"),
    ("20", "20 · Figma", "20_revised", "fx_20_revised", "앱 (f5 SSE)"),
    ("20-live", "20 · Figma", "20_revised", "it_20_revised", "앱 (각본 LLM 스택 + 재생기)"),
    ("21-S1", "21-S1 · Figma", "21s1_compare_delay", "fx_21s1_compare_delay", "앱 (f5)"),
    ("21-S1-live", "21-S1 · Figma", "21s1_compare_delay", "it_21s1_compare_delay", "앱 (각본 LLM 스택 + 재생기)"),
    ("22", "22 · Figma", "22_proactive_safety", "fx_22_proactive_safety", "앱 (f6 선제 세션)"),
    ("22-live", "22 · Figma", "22_proactive_safety", "it_22_proactive_safety", "앱 (각본 LLM 스택 + 재생기)"),
    ("23", "23 · Figma", "23_hazard", "fx_23_hazard", "앱 (장면 ④ near_hazard)"),
    ("23-live", "23 · Figma", "23_hazard", "it_23_hazard", "앱 (각본 LLM 스택 + 재생기)"),
    ("24", "24 · Figma", "24_inbox", "fx_24_inbox", "앱 (장면 ④ 이벤트)"),
    ("24-live", "24 · Figma", "24_inbox", "it_24_inbox", "앱 (각본 LLM 스택 + 재생기)"),
    ("30", "30 · Figma", "30_trip_end", "fx_30_trip_end", "앱 (장면 ④ 끝)"),
]


def main() -> None:
    out = HERE / "cmp"
    out.mkdir(exist_ok=True)
    title = ImageFont.truetype(FONT, 17)
    small = ImageFont.truetype(SMALL, 13)
    for name, left, fig, app, desc in PAIRS:
        fp, ap = HERE / "figma" / f"{fig}.png", HERE / "app" / f"{app}.png"
        if not fp.exists() or not ap.exists():
            print("건너뜀:", name, "(파일 없음)", fp.name if not fp.exists() else ap.name)
            continue
        canvas = Image.new("RGB", (810, 894), "white")
        canvas.paste(Image.open(fp).convert("RGB").resize((390, 844)), (10, 40))
        canvas.paste(Image.open(ap).convert("RGB").resize((390, 844)), (410, 40))
        d = ImageDraw.Draw(canvas)
        d.text((10, 10), left, font=title, fill="#111827")
        d.text((410, 10), desc, font=title, fill="#111827")
        d.text((800 - d.textlength(app, font=small), 14), app, font=small, fill="#8B95A1")
        canvas.save(out / f"{name}.png")
        print("만듦:", name)


if __name__ == "__main__":
    main()
