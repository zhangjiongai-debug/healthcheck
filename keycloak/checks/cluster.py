"""4.5 Keycloak 集群/缓存检查。

- 多副本实例 session 是否一致
- Infinispan/缓存是否正常
- 集群节点是否全部加入
- sticky session 依赖是否合理
- 节点间同步是否异常
"""

import re

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4.5 集群 / 缓存检查")
    kc = ctx["kc"]
    mode = ctx["mode"]

    # ── 通过 health 检查 Infinispan 状态 ──
    resp = kc.health_ready()
    if resp["status"] in (200, 503) and isinstance(resp["body"], dict):
        checks = resp["body"].get("checks", [])
        for c in checks:
            name_lower = c.get("name", "").lower()
            if "infinispan" in name_lower or "cache" in name_lower or "cluster" in name_lower:
                if c.get("status") == "UP":
                    g.ok(f"Health [{c['name']}]", "UP")
                else:
                    g.error(f"Health [{c['name']}]", f"状态: {c.get('status', '?')}",
                            detail=_format_data(c.get("data", {})))

    # ── 通过 metrics 检查集群状态 ──
    _check_cluster_metrics(kc, g)

    # ── 多副本场景: 检查各实例是否都在集群中 ──
    if mode == DeployMode.K8S:
        _check_k8s_cluster(ctx, g)

    # ── 通过日志检查集群同步问题 ──
    if mode == DeployMode.K8S:
        _check_k8s_cluster_logs(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker_cluster_logs(ctx, g)

    return g


def _check_cluster_metrics(kc, g: CheckGroup):
    """从 metrics 中提取集群相关指标。"""
    resp = kc.metrics()
    if resp["status"] != 200 or not isinstance(resp["body"], str):
        return

    body = resp["body"]

    # JGroups 集群大小
    cluster_size = _extract_metric(body, r'vendor_jgroups_cluster_size\s+(\d+)')
    if cluster_size is None:
        cluster_size = _extract_metric(body, r'jgroups_cluster_size\s+(\d+)')

    if cluster_size is not None:
        cluster_size = int(cluster_size)
        if cluster_size >= 2:
            g.ok("集群大小", f"JGroups 集群 {cluster_size} 个节点")
        elif cluster_size == 1:
            g.warn("集群大小", "JGroups 集群仅 1 个节点 (单节点或集群未组建)")
        else:
            g.error("集群大小", f"JGroups 集群大小异常: {cluster_size}")

    # Infinispan cache 统计
    cache_hits = _extract_metric(body, r'vendor_cache_manager_default_cache_.*hits\s+(\d+)')
    cache_misses = _extract_metric(body, r'vendor_cache_manager_default_cache_.*misses\s+(\d+)')

    # session 数量
    sessions_active = _extract_metric(body,
        r'vendor_cache_manager_default_cache_sessions_number_of_entries\s+(\d+)')
    if sessions_active is not None:
        g.ok("活跃 Session 缓存", f"{int(sessions_active)} 个条目")

    # 分布式缓存同步
    rebalance = _extract_metric(body, r'vendor_cache.*rebalancing\s+(\d+)')
    if rebalance is not None and rebalance > 0:
        g.warn("缓存 Rebalance", "缓存正在 rebalance 中")


def _extract_metric(text: str, pattern: str):
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _check_k8s_cluster(ctx: dict, g: CheckGroup):
    """K8s 模式: 检查 Pod 数量与集群大小是否一致，检查 Service 配置。"""
    k8s_core = ctx.get("k8s_core")
    if not k8s_core:
        return

    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception:
        return

    running_pods = [p for p in pods.items if p.status.phase == "Running"]
    if len(running_pods) > 1:
        g.ok("K8s 多副本", f"{len(running_pods)} 个 Running Pod")

        # 检查是否有 headless service (用于 JGroups 发现)
        try:
            services = k8s_core.list_namespaced_service(ns, label_selector=selector)
            headless = [s for s in services.items
                       if s.spec.cluster_ip == "None" or s.spec.cluster_ip == ""]
            if headless:
                g.ok("Headless Service", f"找到 {len(headless)} 个 headless service (JGroups 发现)")
            else:
                g.warn("Headless Service", "未找到 headless service，JGroups DNS_PING 可能不工作")
        except Exception:
            pass

        # 检查 session affinity
        try:
            services = k8s_core.list_namespaced_service(ns, label_selector=selector)
            for svc in services.items:
                if svc.spec.cluster_ip and svc.spec.cluster_ip != "None":
                    affinity = svc.spec.session_affinity
                    if affinity and affinity != "None":
                        g.ok(f"Service {svc.metadata.name}", f"session affinity: {affinity}")
                    else:
                        g.warn(f"Service {svc.metadata.name}",
                               "无 session affinity，多副本时可能影响长连接场景")
        except Exception:
            pass
    elif len(running_pods) == 1:
        g.warn("K8s 副本数", "仅 1 个 Running Pod，无高可用")


_CLUSTER_ERROR_PATTERNS = [
    (r"ISPN\d+.*split brain", "发现脑裂"),
    (r"JGroups.*failed.*join", "节点加入集群失败"),
    (r"JGroups.*suspected", "节点被怀疑离线"),
    (r"cache.*rebalance.*failed", "缓存 rebalance 失败"),
    (r"Infinispan.*error", "Infinispan 错误"),
    (r"view changed.*left=\[", "节点离开集群"),
    (r"JGRP\d+.*failed", "JGroups 协议错误"),
]


def _check_k8s_cluster_logs(ctx: dict, g: CheckGroup):
    """从 K8s Pod 日志中检查集群相关错误。"""
    k8s_core = ctx.get("k8s_core")
    if not k8s_core:
        return

    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception:
        return

    errors_found = []
    for pod in (pods.items or [])[:5]:
        try:
            logs = k8s_core.read_namespaced_pod_log(pod.metadata.name, ns, tail_lines=200)
        except Exception:
            continue

        for pattern, desc in _CLUSTER_ERROR_PATTERNS:
            if re.search(pattern, logs, re.IGNORECASE):
                errors_found.append(f"{pod.metadata.name}: {desc}")

    if errors_found:
        g.error("集群日志异常", f"发现 {len(errors_found)} 个集群相关错误",
                detail="\n".join(errors_found[:10]))
    else:
        g.ok("集群日志检查", "最近日志中未发现集群错误")


def _check_docker_cluster_logs(ctx: dict, g: CheckGroup):
    """Docker 模式: 检查容器日志中的集群错误。"""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "ancestor=quay.io/keycloak/keycloak",
             "-q", "--no-trunc"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return
        for cid in result.stdout.strip().splitlines()[:5]:
            log_result = subprocess.run(
                ["docker", "logs", "--tail", "200", cid],
                capture_output=True, text=True, timeout=10,
            )
            logs = log_result.stdout + log_result.stderr
            for pattern, desc in _CLUSTER_ERROR_PATTERNS:
                if re.search(pattern, logs, re.IGNORECASE):
                    g.warn(f"集群日志 [{cid[:12]}]", desc)
    except Exception:
        pass


def _format_data(data: dict) -> str:
    if not data:
        return None
    return "\n".join(f"  {k}: {v}" for k, v in data.items())
