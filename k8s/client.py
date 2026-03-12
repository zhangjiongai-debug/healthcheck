"""K8s client 初始化与公共工具函数。"""

import time
from kubernetes import client, config


def init_client(kubeconfig: str = None, context: str = None):
    """加载 kubeconfig 并初始化各 API 客户端。"""
    if kubeconfig:
        config.load_kube_config(config_file=kubeconfig, context=context)
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(context=context)

    return {
        "core": client.CoreV1Api(),
        "apps": client.AppsV1Api(),
        "batch": client.BatchV1Api(),
        "networking": client.NetworkingV1Api(),
        "storage": client.StorageV1Api(),
        "autoscaling": client.AutoscalingV1Api(),
        "policy": client.PolicyV1Api(),
        "version": client.VersionApi(),
        "custom": client.CustomObjectsApi(),
        "api_client": client.ApiClient(),
    }


def measure_api_latency(core: client.CoreV1Api, times: int = 3) -> float:
    """测量 API Server 平均响应延迟(ms)。"""
    latencies = []
    for _ in range(times):
        start = time.time()
        try:
            core.list_namespace(limit=1)
        except Exception:
            pass
        latencies.append((time.time() - start) * 1000)
    return sum(latencies) / len(latencies)


def safe_call(func, *args, default=None, **kwargs):
    """安全调用，异常时返回 default。"""
    try:
        return func(*args, **kwargs)
    except Exception:
        return default


def age_hours(timestamp) -> float:
    """计算从 timestamp 到现在的小时数。"""
    if timestamp is None:
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    delta = now - timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else now - timestamp
    return delta.total_seconds() / 3600
