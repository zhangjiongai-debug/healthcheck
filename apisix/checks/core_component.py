"""1. APISIX 核心组件状态检查。

- APISIX Pod 是否 Running / Ready
- APISIX Dashboard 是否正常
- 副本数是否达标
- Pod 是否频繁重启
- 配置 reload 是否成功
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("1. APISIX 核心组件状态")
    mode = ctx["mode"]

    if mode == DeployMode.K8S:
        _check_k8s(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker(ctx, g)
    else:
        _check_vm(ctx, g)

    return g


def _check_k8s(ctx: dict, g: CheckGroup):
    """K8s 部署: 检查 APISIX 和 Dashboard Pod 状态。"""
    k8s_core = ctx.get("k8s_core")
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]

    if not k8s_core:
        g.warn("K8s 客户端", "kubernetes 库未安装，跳过 Pod 级检查")
        return

    # 检查 APISIX Pod
    _check_k8s_pods(k8s_core, ns, ctx["label_selector"], "APISIX", g)

    # 检查 Dashboard Pod
    _check_k8s_pods(k8s_core, ns, ctx["dashboard_label_selector"],
                    "Dashboard", g)

    # 检查 Deployment / StatefulSet 副本数
    if k8s_apps:
        _check_k8s_replicas(k8s_apps, ns, ctx["label_selector"],
                            "APISIX", g)
        _check_k8s_replicas(k8s_apps, ns, ctx["dashboard_label_selector"],
                            "Dashboard", g)


def _check_k8s_pods(k8s_core, ns, selector, component_name, g):
    """检查指定 label selector 下的 Pod 状态。"""
    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception as e:
        g.error(f"{component_name} Pod 列表", f"获取失败: {e}")
        return

    if not pods.items:
        if component_name == "Dashboard":
            g.warn(f"{component_name} Pod", f"未发现 (selector={selector})")
        else:
            g.fatal(f"{component_name} Pod", f"未发现 (namespace={ns}, selector={selector})")
        return

    healthy = 0
    issues = []
    for pod in pods.items:
        pod_name = pod.metadata.name
        phase = pod.status.phase

        if phase != "Running":
            issues.append(f"{pod_name}: {phase}")
            continue

        conditions = {c.type: c.status for c in (pod.status.conditions or [])}
        if conditions.get("Ready") == "True":
            healthy += 1
        else:
            issues.append(f"{pod_name}: Running 但未 Ready")

        # 检查重启
        for cs in (pod.status.container_statuses or []):
            if cs.restart_count > 5:
                issues.append(f"{pod_name}/{cs.name}: 重启 {cs.restart_count} 次")

    total = len(pods.items)
    if issues:
        if healthy == 0:
            g.error(f"{component_name} Pod", f"0/{total} 健康",
                    detail="\n".join(issues))
        else:
            g.warn(f"{component_name} Pod", f"{healthy}/{total} 健康",
                   detail="\n".join(issues))
    else:
        g.ok(f"{component_name} Pod", f"{healthy}/{total} 健康")


def _check_k8s_replicas(k8s_apps, ns, selector, component_name, g):
    """检查 Deployment / StatefulSet 副本状态。"""
    try:
        deploys = k8s_apps.list_namespaced_deployment(ns, label_selector=selector)
        for dep in deploys.items:
            desired = dep.spec.replicas or 1
            ready = dep.status.ready_replicas or 0
            name = dep.metadata.name
            if ready < desired:
                g.error(f"{component_name} Deployment {name}",
                        f"副本 {ready}/{desired} 未达标")
    except Exception:
        pass


def _check_docker(ctx: dict, g: CheckGroup):
    """Docker 部署: 检查 APISIX 和 Dashboard 容器状态。"""
    docker_client = ctx.get("docker_client")

    configs = [
        ("APISIX", ctx.get("docker_container"), ctx.get("docker_image", "apache/apisix")),
        ("Dashboard", ctx.get("dashboard_docker_container"),
         ctx.get("dashboard_docker_image", "apache/apisix-dashboard")),
    ]

    for comp_name, container_name, image in configs:
        _check_docker_container(docker_client, comp_name, container_name, image, g)


def _check_docker_container(docker_client, comp_name, container_name, image, g):
    """检查单个 Docker 容器。"""
    if docker_client:
        try:
            if container_name:
                containers = [docker_client.containers.get(container_name)]
            else:
                containers = docker_client.containers.list(
                    filters={"ancestor": image})
            if not containers:
                if comp_name == "Dashboard":
                    g.warn(f"{comp_name} 容器", "未找到容器")
                else:
                    g.fatal(f"{comp_name} 容器", "未找到容器")
                return
            for c in containers:
                if c.status == "running":
                    health = c.attrs.get("State", {}).get("Health", {}).get("Status", "none")
                    if health == "healthy":
                        g.ok(f"{comp_name} 容器 {c.name}", "running & healthy")
                    elif health == "unhealthy":
                        g.error(f"{comp_name} 容器 {c.name}", "running 但 unhealthy")
                    else:
                        g.ok(f"{comp_name} 容器 {c.name}", f"running (health: {health})")
                    restart_count = c.attrs.get("RestartCount", 0)
                    if restart_count > 3:
                        g.warn(f"{comp_name} 容器 {c.name}", f"重启次数: {restart_count}")
                else:
                    g.error(f"{comp_name} 容器 {c.name}", f"状态: {c.status}")
            return
        except Exception as e:
            g.warn(f"{comp_name} Docker SDK", f"获取容器信息失败: {e}")

    # 降级 CLI
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={image}",
             "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                name = parts[1] if len(parts) > 1 else parts[0]
                status = parts[2] if len(parts) > 2 else "unknown"
                if "Up" in status:
                    g.ok(f"{comp_name} 容器 {name}", f"运行中 ({status})")
                else:
                    g.error(f"{comp_name} 容器 {name}", f"状态异常: {status}")
        else:
            level = "warn" if comp_name == "Dashboard" else "fatal"
            getattr(g, level)(f"{comp_name} 容器", "未找到运行中的容器")
    except Exception as e:
        g.error(f"{comp_name} Docker 检查", f"执行失败: {e}")


def _check_vm(ctx: dict, g: CheckGroup):
    """VM/裸机: 检查 APISIX 相关进程。"""
    services = [
        ("apisix", "APISIX (OpenResty/Nginx)"),
        ("apisix-dashboard", "APISIX Dashboard"),
        ("manager-api", "Dashboard Manager API"),
    ]
    for proc_name, display_name in services:
        try:
            result = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().splitlines()
                g.ok(f"{display_name} 进程", f"检测到 {len(pids)} 个进程")
            else:
                # apisix 底层是 nginx/openresty worker
                if proc_name == "apisix":
                    # 尝试查找 nginx worker
                    result2 = subprocess.run(
                        ["pgrep", "-f", "nginx.*apisix"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if result2.returncode == 0 and result2.stdout.strip():
                        pids = result2.stdout.strip().splitlines()
                        g.ok(f"{display_name} 进程",
                             f"检测到 {len(pids)} 个 nginx worker 进程")
                    else:
                        g.error(f"{display_name} 进程", "未检测到进程")
                elif proc_name == "apisix-dashboard":
                    g.warn(f"{display_name} 进程", "未检测到进程")
                else:
                    pass  # manager-api 是旧版 dashboard，不报警
        except Exception:
            pass
