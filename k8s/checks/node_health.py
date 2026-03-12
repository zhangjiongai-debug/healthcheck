"""2.3 Node 节点健康检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity


# 需要关注的 Node Condition
_PRESSURE_CONDITIONS = ["MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"]


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.3 Node 节点健康检查")
    core: kclient.CoreV1Api = clients["core"]

    try:
        nodes = core.list_node()
    except Exception as e:
        g.fatal("节点列表", f"获取失败: {e}")
        return g

    not_ready = []
    unschedulable = []
    pressure_issues = []
    taint_issues = []

    for node in nodes.items:
        name = node.metadata.name

        # ── 节点状态 ──
        conditions = {c.type: c for c in (node.status.conditions or [])}
        ready_cond = conditions.get("Ready")
        if ready_cond is None or ready_cond.status != "True":
            status = ready_cond.status if ready_cond else "Unknown"
            not_ready.append(f"{name} (status={status})")

        # ── SchedulingDisabled ──
        if node.spec.unschedulable:
            unschedulable.append(name)

        # ── 资源压力 Condition ──
        for cond_name in _PRESSURE_CONDITIONS:
            cond = conditions.get(cond_name)
            if cond and cond.status == "True":
                pressure_issues.append(f"{name}: {cond_name}")

        # ── 异常 Taint ──
        for taint in (node.spec.taints or []):
            if taint.effect == "NoSchedule" and taint.key not in (
                "node-role.kubernetes.io/master",
                "node-role.kubernetes.io/control-plane",
                "node.kubernetes.io/not-ready",
                "node.kubernetes.io/unschedulable",
            ):
                taint_issues.append(f"{name}: {taint.key}={taint.value}:{taint.effect}")

    total = len(nodes.items)

    # 节点 Ready 状态
    if not not_ready:
        g.ok("节点 Ready", f"全部 {total} 个节点 Ready")
    else:
        g.error("节点 Ready", f"{len(not_ready)}/{total} 节点异常",
                detail="\n".join(not_ready))

    # SchedulingDisabled
    if not unschedulable:
        g.ok("节点调度", "无 SchedulingDisabled 节点")
    else:
        g.warn("节点调度", f"{len(unschedulable)} 个节点禁止调度",
               detail="\n".join(unschedulable))

    # 资源压力
    if not pressure_issues:
        g.ok("节点资源压力", "无压力告警")
    else:
        g.error("节点资源压力", f"{len(pressure_issues)} 项告警",
                detail="\n".join(pressure_issues))

    # Taint
    if not taint_issues:
        g.ok("节点 Taint", "无异常自定义 Taint")
    else:
        g.warn("节点 Taint", f"{len(taint_issues)} 个自定义 NoSchedule taint",
               detail="\n".join(taint_issues))

    # ── 节点资源使用概览 (通过 allocatable 与 capacity 对比) ──
    details = []
    for node in nodes.items:
        name = node.metadata.name
        cap = node.status.capacity or {}
        alloc = node.status.allocatable or {}
        cpu_cap = cap.get("cpu", "?")
        mem_cap = cap.get("memory", "?")
        cpu_alloc = alloc.get("cpu", "?")
        mem_alloc = alloc.get("memory", "?")
        details.append(f"{name}: CPU={cpu_alloc}/{cpu_cap}, Mem={mem_alloc}/{mem_cap}")

    g.ok("节点容量概览", f"{total} 个节点", detail="\n".join(details))

    # ── kubelet / container runtime 版本 ──
    runtime_info = []
    for node in nodes.items:
        ni = node.status.node_info
        runtime_info.append(
            f"{node.metadata.name}: kubelet={ni.kubelet_version}, "
            f"runtime={ni.container_runtime_version}, os={ni.os_image}"
        )
    g.ok("节点运行时信息", f"{total} 个节点", detail="\n".join(runtime_info))

    return g
