"""2.9 资源容量与资源规范检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.9 资源容量与资源规范检查")
    core: kclient.CoreV1Api = clients["core"]

    try:
        pods = core.list_pod_for_all_namespaces()
    except Exception as e:
        g.error("Pod 列表", f"获取失败: {e}")
        return g

    no_requests = []
    no_limits = []
    best_effort = []

    for pod in pods.items:
        fqn = f"{pod.metadata.namespace}/{pod.metadata.name}"
        if pod.status.phase in ("Succeeded", "Failed"):
            continue

        has_any_request = False
        has_any_limit = False

        for c in (pod.spec.containers or []):
            res = c.resources
            if res:
                if res.requests and (res.requests.get("cpu") or res.requests.get("memory")):
                    has_any_request = True
                if res.limits and (res.limits.get("cpu") or res.limits.get("memory")):
                    has_any_limit = True

        if not has_any_request and not has_any_limit:
            best_effort.append(fqn)
        elif not has_any_request:
            no_requests.append(fqn)
        elif not has_any_limit:
            no_limits.append(fqn)

    if best_effort:
        g.warn("BestEffort Pod", f"{len(best_effort)} 个 Pod 无 requests 和 limits",
               detail="\n".join(best_effort[:20]))
    else:
        g.ok("BestEffort Pod", "无 BestEffort QoS Pod")

    if no_requests:
        g.warn("未设置 requests", f"{len(no_requests)} 个 Pod 未设置 requests",
               detail="\n".join(no_requests[:20]))
    else:
        g.ok("requests 设置", "所有 Pod 均已设置 requests")

    if no_limits:
        g.warn("未设置 limits", f"{len(no_limits)} 个 Pod 未设置 limits",
               detail="\n".join(no_limits[:20]))
    else:
        g.ok("limits 设置", "所有 Pod 均已设置 limits")

    # ━━━━━ HPA 检查 ━━━━━
    try:
        autoscaling: kclient.AutoscalingV1Api = clients["autoscaling"]
        hpas = autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces()
        hpa_issues = []

        for hpa in hpas.items:
            fqn = f"{hpa.metadata.namespace}/{hpa.metadata.name}"
            current = hpa.status.current_replicas or 0
            desired = hpa.status.desired_replicas or 0
            min_r = hpa.spec.min_replicas or 1
            max_r = hpa.spec.max_replicas or 0

            # 当前副本 == max，可能需要关注
            if current >= max_r and max_r > 0:
                hpa_issues.append(f"{fqn}: 已达最大副本 {max_r}")

            # 无法获取指标
            if hpa.status.current_cpu_utilization_percentage is None and hpa.spec.target_cpu_utilization_percentage:
                hpa_issues.append(f"{fqn}: 无法获取 CPU 指标")

        if not hpas.items:
            g.ok("HPA", "集群中无 HPA")
        elif not hpa_issues:
            g.ok("HPA", f"共 {len(hpas.items)} 个 HPA，正常")
        else:
            g.warn("HPA", f"{len(hpa_issues)} 个 HPA 需关注",
                   detail="\n".join(hpa_issues[:20]))
    except Exception as e:
        g.warn("HPA", f"检查失败: {e}")

    return g
