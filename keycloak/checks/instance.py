"""4.1 Keycloak 实例状态检查。

- Pod/容器 是否 Running / Ready
- 副本数是否达标
- 是否频繁重启
- 健康检查端点是否正常
- 管理控制台是否可访问
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4.1 Keycloak 实例状态")
    kc = ctx["kc"]
    mode = ctx["mode"]

    # ── 基础设施层实例检查 ──
    if mode == DeployMode.K8S:
        _check_k8s_instance(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker_instance(ctx, g)
    else:
        _check_vm_instance(ctx, g)

    # ── 健康检查端点 ──
    _check_health_endpoints(kc, g)

    # ── 管理控制台 ──
    resp = kc.get("/admin/master/console/")
    if resp["status"] == 200:
        g.ok("管理控制台", "可正常访问")
    elif resp["status"] in (301, 302, 303, 307, 308):
        g.ok("管理控制台", f"重定向 ({resp['status']})，通常正常")
    else:
        g.error("管理控制台", f"无法访问 (HTTP {resp['status']})")

    return g


def _check_health_endpoints(kc, g: CheckGroup):
    """检查 Quarkus 健康端点 /health, /health/ready, /health/live。"""
    for name, func in [("health", kc.health), ("health/ready", kc.health_ready),
                       ("health/live", kc.health_live)]:
        resp = func()
        if resp["status"] == 200:
            body = resp["body"]
            if isinstance(body, dict):
                status = body.get("status", "unknown")
                if status == "UP":
                    g.ok(f"/{name}", "UP")
                else:
                    g.warn(f"/{name}", f"状态: {status}",
                           detail=_format_checks(body.get("checks", [])))
            else:
                g.ok(f"/{name}", "HTTP 200")
        elif resp["status"] == 503:
            body = resp["body"]
            detail = None
            if isinstance(body, dict):
                detail = _format_checks(body.get("checks", []))
            g.error(f"/{name}", "DOWN (503)", detail=detail)
        elif resp["status"] == 0:
            g.fatal(f"/{name}", f"无法连接: {resp['body']}")
        else:
            g.warn(f"/{name}", f"HTTP {resp['status']}")


def _format_checks(checks: list) -> str:
    lines = []
    for c in checks:
        status = c.get("status", "?")
        name = c.get("name", "?")
        lines.append(f"  {status}: {name}")
        data = c.get("data", {})
        if data:
            for k, v in data.items():
                lines.append(f"    {k}: {v}")
    return "\n".join(lines) if lines else None


def _check_k8s_instance(ctx: dict, g: CheckGroup):
    """K8s 部署模式: 检查 Pod 状态、副本数、重启次数。"""
    k8s_core = ctx.get("k8s_core")
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    if not k8s_core:
        g.warn("K8s 客户端", "kubernetes 库未安装，跳过 Pod 级检查")
        return

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception as e:
        g.error("Pod 列表", f"获取失败: {e}")
        return

    if not pods.items:
        g.fatal("Pod 发现", f"未找到匹配的 Pod (namespace={ns}, selector={selector})")
        return

    g.ok("Pod 发现", f"找到 {len(pods.items)} 个 Keycloak Pod")

    # 检查每个 Pod
    total_restarts = 0
    ready_count = 0
    for pod in pods.items:
        pod_name = pod.metadata.name
        phase = pod.status.phase

        if phase != "Running":
            g.error(f"Pod {pod_name}", f"阶段: {phase}")
            continue

        # Ready 状态
        conditions = {c.type: c.status for c in (pod.status.conditions or [])}
        if conditions.get("Ready") == "True":
            ready_count += 1
        else:
            g.warn(f"Pod {pod_name}", "Running 但未 Ready")

        # 重启次数
        for cs in (pod.status.container_statuses or []):
            total_restarts += cs.restart_count
            if cs.restart_count > 5:
                g.warn(f"Pod {pod_name}/{cs.name}", f"重启次数: {cs.restart_count}")

    g.ok("Ready 副本", f"{ready_count}/{len(pods.items)} 就绪") if ready_count == len(pods.items) \
        else g.error("Ready 副本", f"{ready_count}/{len(pods.items)} 就绪")

    # 副本数检查 (Deployment/StatefulSet)
    if k8s_apps:
        _check_replicas(k8s_apps, ns, selector, g)


def _check_replicas(k8s_apps, ns, selector, g: CheckGroup):
    """检查 Deployment 或 StatefulSet 的副本数是否达标。"""
    try:
        deps = k8s_apps.list_namespaced_deployment(ns, label_selector=selector)
        for dep in deps.items:
            desired = dep.spec.replicas or 1
            ready = dep.status.ready_replicas or 0
            if ready >= desired:
                g.ok(f"Deployment {dep.metadata.name}", f"副本 {ready}/{desired}")
            else:
                g.error(f"Deployment {dep.metadata.name}", f"副本 {ready}/{desired} 未达标")
    except Exception:
        pass

    try:
        stss = k8s_apps.list_namespaced_stateful_set(ns, label_selector=selector)
        for sts in stss.items:
            desired = sts.spec.replicas or 1
            ready = sts.status.ready_replicas or 0
            if ready >= desired:
                g.ok(f"StatefulSet {sts.metadata.name}", f"副本 {ready}/{desired}")
            else:
                g.error(f"StatefulSet {sts.metadata.name}", f"副本 {ready}/{desired} 未达标")
    except Exception:
        pass


def _check_docker_instance(ctx: dict, g: CheckGroup):
    """Docker 部署模式: 检查容器运行状态。"""
    docker_client = ctx.get("docker_client")
    container_name = ctx.get("docker_container")

    if not docker_client:
        # 降级: 使用 docker CLI
        import subprocess
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "ancestor=quay.io/keycloak/keycloak",
                 "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                g.error("Docker 容器", f"docker ps 失败: {result.stderr.strip()}")
                return
            lines = result.stdout.strip().splitlines()
            if not lines:
                g.fatal("Docker 容器", "未找到运行中的 Keycloak 容器")
                return
            for line in lines:
                parts = line.split("\t")
                name = parts[1] if len(parts) > 1 else parts[0]
                status = parts[2] if len(parts) > 2 else "unknown"
                if "Up" in status:
                    g.ok(f"容器 {name}", f"运行中 ({status})")
                    if "unhealthy" in status.lower():
                        g.warn(f"容器 {name}", "标记为 unhealthy")
                else:
                    g.error(f"容器 {name}", f"状态异常: {status}")
        except Exception as e:
            g.error("Docker 检查", f"执行失败: {e}")
        return

    # 使用 Docker SDK
    try:
        if container_name:
            containers = [docker_client.containers.get(container_name)]
        else:
            containers = docker_client.containers.list(
                filters={"ancestor": "quay.io/keycloak/keycloak"})
        if not containers:
            g.fatal("Docker 容器", "未找到 Keycloak 容器")
            return
        for c in containers:
            if c.status == "running":
                health = c.attrs.get("State", {}).get("Health", {}).get("Status", "none")
                if health == "healthy":
                    g.ok(f"容器 {c.name}", "running & healthy")
                elif health == "unhealthy":
                    g.warn(f"容器 {c.name}", "running 但 unhealthy")
                else:
                    g.ok(f"容器 {c.name}", f"running (health: {health})")
                # 重启次数
                restart_count = c.attrs.get("RestartCount", 0)
                if restart_count > 5:
                    g.warn(f"容器 {c.name}", f"重启次数: {restart_count}")
            else:
                g.error(f"容器 {c.name}", f"状态: {c.status}")
    except Exception as e:
        g.error("Docker 检查", f"获取容器信息失败: {e}")


def _check_vm_instance(ctx: dict, g: CheckGroup):
    """VM/裸机 部署模式: 通过 HTTP 端点判断实例状态。"""
    g.ok("部署模式", "VM/裸机 模式 — 实例检查仅通过 HTTP 端点")
    # VM 模式下实例活跃度完全依赖 health endpoint (在 _check_health_endpoints 中完成)
