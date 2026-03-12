"""健康检查结果数据结构与报告输出。"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(Enum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"

    def __lt__(self, other):
        order = {Severity.OK: 0, Severity.WARN: 1, Severity.ERROR: 2, Severity.FATAL: 3}
        return order[self] < order[other]


@dataclass
class CheckItem:
    name: str
    severity: Severity
    message: str
    detail: Optional[str] = None


@dataclass
class CheckGroup:
    title: str
    items: list[CheckItem] = field(default_factory=list)

    def add(self, name: str, severity: Severity, message: str, detail: str = None):
        self.items.append(CheckItem(name=name, severity=severity, message=message, detail=detail))

    def ok(self, name: str, message: str, detail: str = None):
        self.add(name, Severity.OK, message, detail)

    def warn(self, name: str, message: str, detail: str = None):
        self.add(name, Severity.WARN, message, detail)

    def error(self, name: str, message: str, detail: str = None):
        self.add(name, Severity.ERROR, message, detail)

    def fatal(self, name: str, message: str, detail: str = None):
        self.add(name, Severity.FATAL, message, detail)


# ── ANSI colors ──
_COLORS = {
    Severity.OK: "\033[32m",     # green
    Severity.WARN: "\033[33m",   # yellow
    Severity.ERROR: "\033[31m",  # red
    Severity.FATAL: "\033[35m",  # magenta
}
_RESET = "\033[0m"
_BOLD = "\033[1m"


def print_report(groups: list[CheckGroup], *, verbose: bool = False):
    """打印最终检查报告。"""
    total = {s: 0 for s in Severity}

    for group in groups:
        print(f"\n{_BOLD}{'='*60}")
        print(f"  {group.title}")
        print(f"{'='*60}{_RESET}")

        for item in group.items:
            total[item.severity] += 1
            color = _COLORS[item.severity]
            tag = f"[{item.severity.value:>5}]"
            print(f"  {color}{tag}{_RESET} {item.name}: {item.message}")
            if item.detail:
                for line in item.detail.strip().splitlines():
                    print(f"         {line}")

    # summary
    print(f"\n{_BOLD}{'='*60}")
    print("  检查汇总")
    print(f"{'='*60}{_RESET}")
    for sev in Severity:
        color = _COLORS[sev]
        print(f"  {color}{sev.value:>5}{_RESET}: {total[sev]}")
    print()

    worst = max((s for s in Severity if total[s] > 0), default=Severity.OK)
    if worst == Severity.OK:
        print(f"  {_COLORS[Severity.OK]}✅ 集群健康状态良好{_RESET}\n")
    elif worst == Severity.WARN:
        print(f"  {_COLORS[Severity.WARN]}⚠️  集群存在告警项，建议关注{_RESET}\n")
    else:
        print(f"  {_COLORS[Severity.ERROR]}❌ 集群存在异常，请尽快处理{_RESET}\n")
