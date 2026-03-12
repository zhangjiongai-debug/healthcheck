"""2.8 PVC / PV / 存储检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.8 PVC / PV / 存储检查")
    core: kclient.CoreV1Api = clients["core"]

    # ━━━━━ PVC 状态 ━━━━━
    try:
        pvcs = core.list_persistent_volume_claim_for_all_namespaces()
        pending_pvcs = []
        lost_pvcs = []

        for pvc in pvcs.items:
            fqn = f"{pvc.metadata.namespace}/{pvc.metadata.name}"
            phase = pvc.status.phase
            if phase == "Pending":
                pending_pvcs.append(fqn)
            elif phase == "Lost":
                lost_pvcs.append(fqn)

        bound = len(pvcs.items) - len(pending_pvcs) - len(lost_pvcs)
        if not pending_pvcs and not lost_pvcs:
            g.ok("PVC 状态", f"共 {len(pvcs.items)} 个 PVC，全部 Bound")
        else:
            if pending_pvcs:
                g.error("PVC Pending", f"{len(pending_pvcs)} 个 PVC 未绑定",
                        detail="\n".join(pending_pvcs[:20]))
            if lost_pvcs:
                g.error("PVC Lost", f"{len(lost_pvcs)} 个 PVC 丢失",
                        detail="\n".join(lost_pvcs[:20]))
    except Exception as e:
        g.error("PVC", f"检查失败: {e}")

    # ━━━━━ PV 状态 ━━━━━
    try:
        pvs = core.list_persistent_volume()
        pv_issues = []
        for pv in pvs.items:
            phase = pv.status.phase
            if phase in ("Failed", "Released"):
                pv_issues.append(f"{pv.metadata.name}: phase={phase}, "
                                 f"reclaim={pv.spec.persistent_volume_reclaim_policy}")

        if not pv_issues:
            g.ok("PV 状态", f"共 {len(pvs.items)} 个 PV，无异常")
        else:
            g.warn("PV 状态", f"{len(pv_issues)} 个 PV 需关注",
                   detail="\n".join(pv_issues[:20]))
    except Exception as e:
        g.error("PV", f"检查失败: {e}")

    # ━━━━━ Pod Volume 挂载事件检查 ━━━━━
    try:
        events = core.list_event_for_all_namespaces(
            field_selector="reason=FailedMount,reason=FailedAttachVolume"
        )
        # field_selector 对 reason 支持有限, 改用过滤
        mount_events = [
            e for e in core.list_event_for_all_namespaces().items
            if e.reason in ("FailedMount", "FailedAttachVolume")
        ]

        if not mount_events:
            g.ok("Volume 挂载", "无 FailedMount/FailedAttachVolume 事件")
        else:
            details = []
            for ev in mount_events[:15]:
                details.append(f"{ev.involved_object.namespace}/{ev.involved_object.name}: "
                               f"{ev.reason} - {ev.message[:100] if ev.message else ''}")
            g.warn("Volume 挂载", f"{len(mount_events)} 个挂载失败事件",
                   detail="\n".join(details))
    except Exception as e:
        g.warn("Volume 挂载事件", f"检查失败: {e}")

    return g
