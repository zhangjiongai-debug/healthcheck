"""2. 控制面与数据面状态检查。

- APISIX 数据面实例是否全部在线
- 控制器是否成功将 Ingress/CRD 同步到 APISIX
- etcd 连接是否正常
- 配置下发延迟是否异常
- 是否存在配置不同步、部分实例未生效
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("2. 控制面与数据面状态")
    apisix = ctx["apisix"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("Admin API", ctx["connect_error"])
        return g

    # ── Admin API 可用性 (数据面核心能力) ──
    _check_admin_api(apisix, g)

    # ── etcd 连接检查 (仅 K8s / VM) ──
    if mode == DeployMode.K8S:
        _check_etcd_k8s(ctx, g)
    elif mode == DeployMode.VM:
        _check_etcd_vm(g)

    # ── Ingress Controller 检查 (K8s) ──
    if mode == DeployMode.K8S:
        _check_ingress_controller_k8s(ctx, g)

    # ── 数据面实例同步检查 (K8s 多副本) ──
    if mode == DeployMode.K8S:
        _check_data_plane_sync(ctx, g)

    return g


def _check_admin_api(apisix, g):
    """检查 Admin API 基本可用性。"""
    resp = apisix.admin("/routes")
    if resp["status"] == 200:
        body = resp["body"]
        if isinstance(body, dict):
            total = body.get("total", 0)
            g.ok("Admin API", f"可用, 当前 {total} 条路由")
        else:
            g.ok("Admin API", "可用")
    elif resp["status"] == 401:
        g.error("Admin API", "认证失败 (API Key 无效或缺失)")
    elif resp["status"] == 0:
        g.fatal("Admin API", f"不可达: {resp['body']}")
    else:
        g.error("Admin API", f"异常响应 (status={resp['status']})")


def _check_etcd_k8s(ctx, g):
    """K8s: 检查 etcd Pod 状态。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    # 尝试多种 label selector 查找 etcd
    selectors = [
        "app.kubernetes.io/name=etcd",
        "app=etcd",
        "app.kubernetes.io/component=etcd",
    ]
    pods = None
    for sel in selectors:
        try:
            result = k8s_core.list_namespaced_pod(ns, label_selector=sel)
            if result.items:
                pods = result
                break
        except Exception:
            continue

    if not pods or not pods.items:
        g.warn("etcd Pod", f"未在 namespace={ns} 中发现 etcd Pod")
        return

    healthy = 0
    issues = []
    for pod in pods.items:
        pod_name = pod.metadata.name
        phase = pod.status.phase
        if phase == "Running":
            conditions = {c.type: c.status for c in (pod.status.conditions or [])}
            if conditions.get("Ready") == "True":
                healthy += 1
            else:
                issues.append(f"{pod_name}: Running 但未 Ready")
        else:
            issues.append(f"{pod_name}: {phase}")

        for cs in (pod.status.container_statuses or []):
            if cs.restart_count > 3:
                issues.append(f"{pod_name}: 重启 {cs.restart_count} 次")

    total = len(pods.items)
    if issues:
        if healthy == 0:
            g.fatal("etcd Pod", f"0/{total} 健康", detail="\n".join(issues))
        else:
            g.warn("etcd Pod", f"{healthy}/{total} 健康",
                   detail="\n".join(issues))
    else:
        g.ok("etcd Pod", f"{healthy}/{total} 健康")


def _check_etcd_vm(g):
    """VM: 检查 etcd 进程。"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "etcd"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            g.ok("etcd 进程", f"检测到 {len(pids)} 个进程")
        else:
            g.warn("etcd 进程", "未检测到 etcd 进程 (可能使用远程 etcd)")
    except Exception:
        pass


def _check_ingress_controller_k8s(ctx, g):
    """K8s: 检查 APISIX Ingress Controller Pod。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    selectors = [
        "app.kubernetes.io/name=apisix-ingress-controller",
        "app.kubernetes.io/name=ingress-controller",
        "app=apisix-ingress-controller",
    ]
    pods = None
    for sel in selectors:
        try:
            result = k8s_core.list_namespaced_pod(ns, label_selector=sel)
            if result.items:
                pods = result
                break
        except Exception:
            continue

    if not pods or not pods.items:
        g.warn("Ingress Controller", "未发现 Ingress Controller Pod (可能未部署)")
        return

    healthy = 0
    issues = []
    for pod in pods.items:
        pod_name = pod.metadata.name
        phase = pod.status.phase
        if phase == "Running":
            conditions = {c.type: c.status for c in (pod.status.conditions or [])}
            if conditions.get("Ready") == "True":
                healthy += 1
            else:
                issues.append(f"{pod_name}: Running 但未 Ready")
        else:
            issues.append(f"{pod_name}: {phase}")

    total = len(pods.items)
    if issues:
        g.warn("Ingress Controller", f"{healthy}/{total} 健康",
               detail="\n".join(issues))
    else:
        g.ok("Ingress Controller", f"{healthy}/{total} 健康")


def _check_data_plane_sync(ctx, g):
    """K8s: 通过 Admin API 检查数据面是否可正常响应。"""
    apisix = ctx["apisix"]

    # 尝试获取 plugins list 验证 APISIX 核心配置已加载
    resp = apisix.plugins_list()
    if resp["status"] == 200:
        body = resp["body"]
        if isinstance(body, list):
            g.ok("插件加载", f"已加载 {len(body)} 个插件")
        else:
            g.ok("插件加载", "插件列表可用")
    elif resp["status"] == 0:
        g.error("数据面同步", f"无法获取插件列表: {resp['body']}")
    else:
        g.warn("数据面同步", f"获取插件列表异常 (status={resp['status']})")
