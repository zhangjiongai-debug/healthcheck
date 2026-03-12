"""2.11 事件与日志异常检查。"""

from collections import Counter
from kubernetes import client as kclient
from ..result import CheckGroup, Severity

_CRITICAL_REASONS = {
    "FailedScheduling", "BackOff", "Unhealthy", "FailedMount",
    "FailedAttachVolume", "Evicted", "OOMKilling", "FailedCreate",
    "FailedDelete", "NodeNotReady", "NetworkNotReady",
}


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.11 事件与日志异常检查")
    core: kclient.CoreV1Api = clients["core"]

    # ━━━━━ Warning Events ━━━━━
    try:
        events = core.list_event_for_all_namespaces(field_selector="type=Warning")
        if not events.items:
            g.ok("Warning 事件", "无 Warning 事件")
        else:
            reason_counter = Counter()
            ns_counter = Counter()
            sample_messages = {}

            for ev in events.items:
                reason = ev.reason or "Unknown"
                reason_counter[reason] += ev.count or 1
                ns_counter[ev.metadata.namespace] += 1
                if reason not in sample_messages:
                    obj = ev.involved_object
                    sample_messages[reason] = (
                        f"  {obj.namespace}/{obj.name}: {ev.message[:100] if ev.message else ''}"
                    )

            total_warnings = sum(reason_counter.values())

            # 关键事件
            critical_details = []
            for reason in _CRITICAL_REASONS:
                count = reason_counter.get(reason, 0)
                if count > 0:
                    critical_details.append(f"{reason}: {count} 次")
                    if reason in sample_messages:
                        critical_details.append(sample_messages[reason])

            if critical_details:
                g.error("关键 Warning 事件", f"发现关键告警事件",
                        detail="\n".join(critical_details))
            else:
                g.ok("关键 Warning 事件", "无关键告警事件")

            # 总览
            top_reasons = reason_counter.most_common(10)
            detail_lines = [f"{r}: {c} 次" for r, c in top_reasons]
            sev = Severity.WARN if total_warnings > 10 else Severity.OK
            g.add("Warning 事件总览", sev,
                  f"共 {total_warnings} 条 Warning 事件",
                  detail="\n".join(detail_lines))

            # 命名空间分布
            top_ns = ns_counter.most_common(5)
            if len(top_ns) > 1:
                ns_lines = [f"{ns}: {c} 条" for ns, c in top_ns]
                g.add("Warning 事件分布", Severity.OK,
                      "按命名空间分布", detail="\n".join(ns_lines))

    except Exception as e:
        g.error("事件检查", f"获取失败: {e}")

    return g
