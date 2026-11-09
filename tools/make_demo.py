"""Render actual CLI output from fictional sessions; no model or personal logs.

Run from the repository root: python3 tools/make_demo.py
Optional PNG export for GitHub social preview requires Pillow: add --png.
"""

import contextlib
import html
import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chronicle import core, db
from chronicle.__main__ import main


def capture_demo():
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc).timestamp()
    with tempfile.TemporaryDirectory(prefix="chronicle-demo-") as tmp:
        sandbox = Path(tmp)
        projects = sandbox / ".claude" / "projects"
        logs = projects / "demo"
        logs.mkdir(parents=True)
        project = sandbox / "projects" / "demo-shop"
        project.mkdir(parents=True)
        jobs = sandbox / ".claude" / "jobs"
        for sid, title, prompt, age in (
            ("a1b2c3d4-demo", "Лендинг магазина", "Собери страницу каталога товаров", 180),
            ("e5f6a7b8-demo", "Запись клиентов", "Добавь напоминание о записи", 1200),
        ):
            records = [
                {"type": "ai-title", "aiTitle": title},
                {"type": "user", "cwd": str(project),
                 "timestamp": datetime.fromtimestamp(now - age, timezone.utc).isoformat(),
                 "message": {"content": prompt}},
            ]
            (logs / f"{sid}.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        job = jobs / "reminder"
        job.mkdir(parents=True)
        (job / "state.json").write_text(json.dumps({
            "sessionId": "e5f6a7b8-demo", "state": "needs_input",
            "name": "Запись клиентов", "detail": "Нужен текст напоминания",
        }), encoding="utf-8")
        with contextlib.ExitStack() as stack:
            for obj, name, value in (
                (core, "CLAUDE_HOME", sandbox / ".claude"), (core, "PROJECTS", projects),
                (core, "JOBS", jobs), (core, "ROOTS", [sandbox / "projects"]),
                (db, "HOME", sandbox / "data"), (db, "DB_PATH", sandbox / "data" / "chronicle.db"),
            ):
                stack.enter_context(patch.object(obj, name, value))
            stack.enter_context(patch.object(Path, "home", return_value=sandbox))
            stack.enter_context(patch("time.time", return_value=now))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert main(["sessions"]) == 0
            return out.getvalue().strip()


def main_demo():
    output = capture_demo()
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "demo.txt").write_text("$ chronicle sessions\n\n" + output + "\n", encoding="utf-8")
    lines = ["$ chronicle sessions", "", *output.splitlines()]
    text = "\n".join(
        f'<text x="70" y="{190 + i * 33}" fill="{"#85dfbd" if i == 0 else "#e0e5ee"}">'
        f'{html.escape(line)}</text>' for i, line in enumerate(lines))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">
<rect width="1280" height="640" fill="#10151f"/>
<text x="58" y="66" font-family="sans-serif" font-size="36" fill="#ffffff">claude-chronicle</text>
<text x="58" y="105" font-family="sans-serif" font-size="22" fill="#b1bbc9">Все сессии Claude Code — в одном списке</text>
<rect x="44" y="138" width="1192" height="410" rx="14" fill="#1b2432"/>
<g font-family="DejaVu Sans Mono, monospace" font-size="21" xml:space="preserve">{text}</g>
<text x="58" y="595" font-family="sans-serif" font-size="19" fill="#a3afbf">Реальный вывод CLI · вымышленные сессии · без вызова модели</text>
</svg>'''
    (assets / "demo.svg").write_text(svg, encoding="utf-8")
    if "--png" in sys.argv:
        from PIL import Image, ImageDraw, ImageFont
        frame = Image.new("RGB", (1280, 640), "#10151f")
        draw = ImageDraw.Draw(frame)
        regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        mono = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
        draw.text((58, 28), "claude-chronicle", font=ImageFont.truetype(regular, 36), fill="white")
        draw.text((58, 78), "Все сессии Claude Code — в одном списке", font=ImageFont.truetype(regular, 22), fill="#b1bbc9")
        draw.rounded_rectangle((44, 138, 1236, 548), radius=14, fill="#1b2432")
        for i, line in enumerate(lines):
            draw.text((70, 168 + i * 33), line, font=ImageFont.truetype(mono, 21),
                      fill="#85dfbd" if i == 0 else "#e0e5ee")
        draw.text((58, 575), "Реальный вывод CLI · вымышленные сессии · без вызова модели",
                  font=ImageFont.truetype(regular, 19), fill="#a3afbf")
        frame.save(assets / "social-preview.png")
    print("Created assets/demo.svg and assets/demo.txt" + (" and assets/social-preview.png" if "--png" in sys.argv else ""))


if __name__ == "__main__":
    main_demo()
