"""Команда `chronicle`: что идёт во всех сессиях Claude Code и что в них сделано.

  chronicle sessions [--hours 24] [--project X]   список сессий по проектам (без модели, мгновенно)
  chronicle run [--all] [--no-llm]               проход: дочитать журналы и разобрать новое (для таймера)
  chronicle overview [--hours 24]                общая сводка моделью: что в работе, что ждёт тебя
  chronicle report [--hours 24]                  отчёт о состоянии сессий в .md, печатает путь
  chronicle work [--days 7 | --from … --to …] [--project X] [--style "…"]
                                                 отчёт о проделанной работе за период (OKR), печатает путь
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta

from . import core, db


def _period(args) -> tuple[float, float]:
    """Период отчёта: --from/--to (даты включительно) или последние --days дней до конца сегодняшнего."""
    day = lambda s: datetime.strptime(s, "%Y-%m-%d")
    end = day(args.date_to) if args.date_to else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end += timedelta(days=1)
    start = day(args.date_from) if args.date_from else end - timedelta(days=args.days or 7)
    return start.timestamp(), end.timestamp()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="chronicle", description="Летописец сессий Claude Code")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sessions", help="сессии по проектам: задача, сделано, что ждёт")
    s.add_argument("--hours", type=float, default=24)
    s.add_argument("--project", help="только проект, где встречается это слово")
    s.add_argument("--json", action="store_true")

    r = sub.add_parser("run", help="дочитать журналы и разобрать новое моделью")
    r.add_argument("--all", action="store_true", help="разобрать всё накопленное, без лимита вызовов")
    r.add_argument("--no-llm", action="store_true", help="только собрать, без модели")

    o = sub.add_parser("overview", help="общая сводка моделью")
    o.add_argument("--hours", type=float, default=24)

    rp = sub.add_parser("report", help="отчёт о состоянии сессий в .md")
    rp.add_argument("--hours", type=float, default=24)
    rp.add_argument("--no-llm", action="store_true")

    w = sub.add_parser("work", help="отчёт о проделанной работе за период (OKR)")
    w.add_argument("--days", type=float, help="за последние N дней (по умолчанию 7)")
    w.add_argument("--from", dest="date_from", help="с даты ГГГГ-ММ-ДД")
    w.add_argument("--to", dest="date_to", help="по дату ГГГГ-ММ-ДД включительно (по умолчанию сегодня)")
    w.add_argument("--project", help="только этот проект")
    w.add_argument("--style", help="свой формат отчёта словами вместо OKR")
    w.add_argument("--no-llm", action="store_true")

    args = p.parse_args(argv)
    try:
        if args.cmd == "sessions":
            core.scan()
            items = core.sessions(hours=args.hours, project=args.project)
            print(json.dumps(items, ensure_ascii=False) if args.json else core.render(items))
        elif args.cmd == "run":
            stats = core.run(llm=not args.no_llm, limit=10**9 if args.all else core.CALLS_PER_RUN)
            print(json.dumps(stats, ensure_ascii=False))
        elif args.cmd == "overview":
            core.scan()
            print(core.overview(hours=args.hours))
        elif args.cmd == "report":
            print(core.report(hours=args.hours, llm=not args.no_llm))
        elif args.cmd == "work":
            print(core.work_report(*_period(args), project=args.project, style=args.style, llm=not args.no_llm))
    except (db.ChronicleError, ValueError, OSError, subprocess.TimeoutExpired) as err:
        print(f"chronicle: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
