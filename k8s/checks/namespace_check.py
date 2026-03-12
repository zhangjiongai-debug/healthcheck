"""2.4 Namespace 维度检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity

_SYSTEM_NS = {"kube-system", "kube-public", "kube-node-lease", "default"}


def check(clients: dict, include_ns: list[str] = None, exclude_system: bool = True) -> CheckGroup:
    g = CheckGroup("2.4 Namespace 维度检查")
    core: kclient.CoreV1Api = clients["core"]

    try:
        namespaces = core.list_namespace()
    except Exception as e:
        g.fatal("Namespace 列表", f"获取失败: {e}")
        return g

    terminating = []
    inactive = []

    for ns in namespaces.items:
        name = ns.metadata.name
        phase = ns.status.phase

        if phase == "Terminating":
            terminating.append(name)
        elif phase != "Active":
            inactive.append(f"{name} (phase={phase})")

    if not terminating and not inactive:
        g.ok("Namespace 状态", f"全部 {len(namespaces.items)} 个 Namespace Active")
    if terminating:
        g.warn("Namespace Terminating", f"{len(terminating)} 个 Namespace 卡在 Terminating",
               detail="\n".join(terminating))
    if inactive:
        g.error("Namespace 异常", f"{len(inactive)} 个状态异常", detail="\n".join(inactive))

    # ── ResourceQuota 检查 ──
    target_ns = include_ns if include_ns else [
        ns.metadata.name for ns in namespaces.items
        if not (exclude_system and ns.metadata.name in _SYSTEM_NS)
    ]

    quota_warnings = []
    for ns_name in target_ns:
        try:
            quotas = core.list_namespaced_resource_quota(ns_name)
        except Exception:
            continue
        for q in quotas.items:
            hard = q.status.hard or {}
            used = q.status.used or {}
            for resource, hard_val in hard.items():
                used_val = used.get(resource, "0")
                try:
                    h = _parse_quantity(hard_val)
                    u = _parse_quantity(used_val)
                    if h > 0 and u / h > 0.85:
                        pct = u / h * 100
                        quota_warnings.append(f"{ns_name}: {resource} {pct:.0f}% ({used_val}/{hard_val})")
                except (ValueError, ZeroDivisionError):
                    pass

    if not quota_warnings:
        g.ok("ResourceQuota", "无配额告警")
    else:
        g.warn("ResourceQuota", f"{len(quota_warnings)} 项接近上限",
               detail="\n".join(quota_warnings))

    # ── LimitRange 检查 ──
    ns_without_lr = []
    for ns_name in target_ns:
        try:
            lr = core.list_namespaced_limit_range(ns_name)
            if not lr.items:
                ns_without_lr.append(ns_name)
        except Exception:
            pass

    if ns_without_lr:
        g.warn("LimitRange", f"{len(ns_without_lr)} 个业务 Namespace 无 LimitRange",
               detail="\n".join(ns_without_lr[:20]))
    else:
        g.ok("LimitRange", "所有业务 Namespace 均已设置 LimitRange")

    return g


def _parse_quantity(val: str) -> float:
    """简易解析 K8s 资源数量字符串。"""
    val = str(val).strip()
    suffixes = {
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
        "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
        "m": 0.001,
    }
    for suffix, multiplier in sorted(suffixes.items(), key=lambda x: -len(x[0])):
        if val.endswith(suffix):
            return float(val[:-len(suffix)]) * multiplier
    return float(val)
