"""2.2 控制面组件检查。"""

import re
from kubernetes import client as kclient
from ..result import CheckGroup, Severity


_CONTROL_PLANE_LABELS = {
    "etcd": "component=etcd",
    "kube-apiserver": "component=kube-apiserver",
    "kube-controller-manager": "component=kube-controller-manager",
    "kube-scheduler": "component=kube-scheduler",
}

_COREDNS_LABELS = ["k8s-app=kube-dns", "app=coredns"]

_ETCD_BAD_PATTERNS = [
    "leader changed", "apply request took too long",
    "database space exceeded", "backend quota",
]
_APISERVER_BAD_PATTERNS = [
    "etcd", "webhook timeout", "x509", "certificate",
    "authn", "authz", "connection refused",
]


def _check_pods(core: kclient.CoreV1Api, g: CheckGroup, name: str,
                label_selector: str, namespace: str = "kube-system",
                log_patterns: list[str] = None):
    """检查指定组件的 Pod 状态与日志。"""
    try:
        pods = core.list_namespaced_pod(namespace, label_selector=label_selector)
    except Exception as e:
        g.warn(f"{name} Pod", f"无法获取 (托管集群可忽略): {e}")
        return

    if not pods.items:
        g.warn(f"{name} Pod", "未发现 Pod (托管集群可忽略)")
        return

    all_ok = True
    details = []
    for pod in pods.items:
        phase = pod.status.phase
        ready = all(
            cs.ready for cs in (pod.status.container_statuses or [])
        ) if pod.status.container_statuses else False
        restarts = sum(cs.restart_count for cs in (pod.status.container_statuses or []))

        if phase != "Running" or not ready:
            all_ok = False
            details.append(f"{pod.metadata.name}: phase={phase}, ready={ready}")
        if restarts > 5:
            all_ok = False
            details.append(f"{pod.metadata.name}: 重启 {restarts} 次")

    if all_ok:
        g.ok(f"{name} Pod", f"{len(pods.items)} 个 Pod 正常")
    else:
        g.error(f"{name} Pod", "存在异常", detail="\n".join(details))

    # 检查日志关键字
    if log_patterns:
        bad_lines = []
        for pod in pods.items[:2]:  # 只检查前2个Pod避免过慢
            try:
                log = core.read_namespaced_pod_log(
                    pod.metadata.name, namespace, tail_lines=200, _request_timeout=5,
                )
            except Exception:
                continue
            for pattern in log_patterns:
                for line in log.splitlines():
                    if pattern.lower() in line.lower():
                        bad_lines.append(f"[{pod.metadata.name}] {line[:120]}")
                        break  # 每个 pattern 每个 pod 只取一条
        if bad_lines:
            g.warn(f"{name} 日志异常", f"发现 {len(bad_lines)} 条告警日志",
                   detail="\n".join(bad_lines[:10]))


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.2 控制面组件检查")
    core: kclient.CoreV1Api = clients["core"]

    # etcd
    _check_pods(core, g, "etcd", _CONTROL_PLANE_LABELS["etcd"],
                log_patterns=_ETCD_BAD_PATTERNS)

    # kube-apiserver
    _check_pods(core, g, "kube-apiserver", _CONTROL_PLANE_LABELS["kube-apiserver"],
                log_patterns=_APISERVER_BAD_PATTERNS)

    # readyz verbose (子项检查)
    try:
        api_client: kclient.ApiClient = clients["api_client"]
        resp = api_client.call_api(
            "/readyz", "GET", query_params=[("verbose", "true")],
            response_type="str", auth_settings=["BearerToken"],
            _return_http_data_only=True,
        )
        failed = [line for line in resp.splitlines() if "failed" in line.lower() or "- " in line and "ok" not in line.lower()]
        if failed:
            g.warn("API /readyz 子项", f"{len(failed)} 项异常", detail="\n".join(failed[:10]))
        else:
            g.ok("API /readyz 子项", "全部通过")
    except Exception:
        g.warn("API /readyz 子项", "无法获取详细状态")

    # kube-controller-manager
    _check_pods(core, g, "kube-controller-manager",
                _CONTROL_PLANE_LABELS["kube-controller-manager"])

    # kube-scheduler
    _check_pods(core, g, "kube-scheduler", _CONTROL_PLANE_LABELS["kube-scheduler"])

    # Pending Pods (调度问题)
    try:
        pending = core.list_pod_for_all_namespaces(field_selector="status.phase=Pending")
        count = len(pending.items)
        if count == 0:
            g.ok("Pending Pod", "无 Pending Pod")
        elif count <= 5:
            g.warn("Pending Pod", f"存在 {count} 个 Pending Pod",
                   detail="\n".join(f"{p.metadata.namespace}/{p.metadata.name}" for p in pending.items))
        else:
            g.error("Pending Pod", f"存在 {count} 个 Pending Pod (可能调度异常)")
    except Exception as e:
        g.warn("Pending Pod", f"检查失败: {e}")

    # CoreDNS
    for label in _COREDNS_LABELS:
        try:
            pods = core.list_namespaced_pod("kube-system", label_selector=label)
            if pods.items:
                running = [p for p in pods.items if p.status.phase == "Running"]
                if len(running) == len(pods.items):
                    g.ok("CoreDNS", f"{len(running)} 个 Pod 正常")
                else:
                    g.error("CoreDNS", f"{len(running)}/{len(pods.items)} Running")
                break
        except Exception:
            continue
    else:
        g.warn("CoreDNS", "未找到 CoreDNS Pod")

    # kube-proxy DaemonSet
    try:
        apps: kclient.AppsV1Api = clients["apps"]
        ds_list = apps.list_namespaced_daemon_set("kube-system", label_selector="k8s-app=kube-proxy")
        if ds_list.items:
            ds = ds_list.items[0]
            desired = ds.status.desired_number_scheduled or 0
            ready = ds.status.number_ready or 0
            if desired == ready and desired > 0:
                g.ok("kube-proxy", f"{ready}/{desired} 就绪")
            else:
                g.error("kube-proxy", f"{ready}/{desired} 就绪")
        else:
            g.warn("kube-proxy", "未找到 DaemonSet (可能使用其他方案)")
    except Exception as e:
        g.warn("kube-proxy", f"检查失败: {e}")

    return g
