"""1. Jenkins 控制器状态检查。

- Jenkins Pod/实例是否 Running / Ready
- Web UI 是否可访问
- 登录页是否正常
- /login / /api/json 是否可访问
- Pod 是否频繁重启
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("1. Jenkins 控制器状态")
    jk = ctx["jk"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        # 仍然检查基础设施层
        if mode == DeployMode.K8S:
            _check_k8s(ctx, g)
        elif mode == DeployMode.DOCKER:
            _check_docker(ctx, g)
        return g

    # ── 登录页 ──
    resp = jk.get("/login")
    if resp["status"] == 200:
        g.ok("登录页", "可正常访问")
    elif resp["status"] in (301, 302, 303):
        g.ok("登录页", f"重定向 (status={resp['status']})")
    else:
        g.error("登录页", f"异常 (status={resp['status']})")

    # ── JSON API ──
    resp = jk.api_json()
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        mode_str = resp["body"].get("mode", "unknown")
        version = resp["body"].get("hudson", {})
        desc = resp["body"].get("description", "")
        num_exec = resp["body"].get("numExecutors", "?")
        g.ok("JSON API", f"可访问, 模式: {mode_str}, Executors: {num_exec}")

        # 检查是否有 views
        views = resp["body"].get("views", [])
        if views:
            g.ok("Views", f"共 {len(views)} 个视图")
    elif resp["status"] == 403:
        g.warn("JSON API", "需要认证 (403), 请提供 --user 和 --password")
    else:
        g.error("JSON API", f"不可用 (status={resp['status']})")

    # ── Jenkins 版本 (从 header 获取) ──
    resp = jk.get("/")
    if resp["status"] in (200, 301, 302, 303, 403):
        # Jenkins 版本通常在 X-Jenkins header，但 urllib 不暴露
        # 通过 api/json 获取
        ver_resp = jk.api_json(tree="systemMessage")
        if ver_resp["status"] == 200:
            pass  # 已在上面检查过

    # ── 基础设施层检查 ──
    if mode == DeployMode.K8S:
        _check_k8s(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker(ctx, g)
    else:
        _check_vm(ctx, g)

    return g


def _check_k8s(ctx: dict, g: CheckGroup):
    """K8s 部署: 检查 Pod / StatefulSet 状态。"""
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

    # 过滤出控制器 Pod (非 agent)
    controller_pods = []
    for pod in pods.items:
        labels = pod.metadata.labels or {}
        component = labels.get("app.kubernetes.io/component", "")
        if "agent" in component.lower():
            continue
        controller_pods.append(pod)

    if not controller_pods:
        g.fatal("Pod 发现", f"未找到 Jenkins 控制器 Pod (namespace={ns}, selector={selector})")
        return

    g.ok("Pod 发现", f"找到 {len(controller_pods)} 个 Jenkins 控制器 Pod")

    for pod in controller_pods:
        pod_name = pod.metadata.name
        phase = pod.status.phase

        if phase != "Running":
            g.error(f"Pod {pod_name}", f"阶段: {phase}")
            continue

        conditions = {c.type: c.status for c in (pod.status.conditions or [])}
        if conditions.get("Ready") == "True":
            g.ok(f"Pod {pod_name}", "Running & Ready")
        else:
            g.warn(f"Pod {pod_name}", "Running 但未 Ready")

        for cs in (pod.status.container_statuses or []):
            if cs.restart_count > 3:
                g.warn(f"Pod {pod_name}/{cs.name}",
                       f"重启次数: {cs.restart_count}")

    # StatefulSet
    if k8s_apps:
        try:
            stss = k8s_apps.list_namespaced_stateful_set(ns, label_selector=selector)
            for sts in stss.items:
                desired = sts.spec.replicas or 1
                ready = sts.status.ready_replicas or 0
                if ready >= desired:
                    g.ok(f"StatefulSet {sts.metadata.name}", f"副本 {ready}/{desired}")
                else:
                    g.error(f"StatefulSet {sts.metadata.name}",
                            f"副本 {ready}/{desired} 未达标")
        except Exception:
            pass


def _check_docker(ctx: dict, g: CheckGroup):
    """Docker 部署: 检查容器状态。"""
    docker_client = ctx.get("docker_client")
    container_name = ctx.get("docker_container")
    docker_image = ctx.get("docker_image", "jenkins/jenkins")

    if docker_client:
        try:
            if container_name:
                containers = [docker_client.containers.get(container_name)]
            else:
                containers = docker_client.containers.list(
                    filters={"ancestor": docker_image})
            if not containers:
                g.fatal("Docker 容器", "未找到 Jenkins 容器")
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
                    restart_count = c.attrs.get("RestartCount", 0)
                    if restart_count > 3:
                        g.warn(f"容器 {c.name}", f"重启次数: {restart_count}")
                else:
                    g.error(f"容器 {c.name}", f"状态: {c.status}")
            return
        except Exception as e:
            g.warn("Docker SDK", f"获取容器信息失败: {e}")

    # 降级 CLI
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={docker_image}",
             "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                name = parts[1] if len(parts) > 1 else parts[0]
                status = parts[2] if len(parts) > 2 else "unknown"
                if "Up" in status:
                    g.ok(f"容器 {name}", f"运行中 ({status})")
                else:
                    g.error(f"容器 {name}", f"状态异常: {status}")
        else:
            g.fatal("Docker 容器", "未找到运行中的 Jenkins 容器")
    except Exception as e:
        g.error("Docker 检查", f"执行失败: {e}")


def _check_vm(ctx: dict, g: CheckGroup):
    """VM/裸机: 检查 Jenkins 进程。"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "jenkins.war"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            g.ok("Jenkins 进程", f"检测到 {len(pids)} 个 jenkins 进程")
        else:
            g.warn("Jenkins 进程", "本地未检测到 jenkins 进程 (可能为远程实例)")
    except Exception:
        g.ok("部署模式", "VM/裸机 — 实例检查通过 HTTP 端点完成")
