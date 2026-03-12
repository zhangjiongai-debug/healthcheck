"""1. GitLab 核心服务状态检查。

- Webservice Pod 是否正常
- Sidekiq Pod 是否正常
- Toolbox Pod 是否正常
- Shell 是否正常
- Gitaly 是否正常
- 副本数是否满足
- Pod 是否频繁重启
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode

# GitLab Helm chart 中的核心组件
_CORE_COMPONENTS = [
    ("webservice", "Webservice"),
    ("sidekiq", "Sidekiq"),
    ("gitaly", "Gitaly"),
    ("gitlab-shell", "Shell"),
    ("toolbox", "Toolbox"),
    ("kas", "KAS"),
    ("gitlab-exporter", "Exporter"),
    ("registry", "Registry"),
]


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("1. 核心服务状态")
    mode = ctx["mode"]

    if mode == DeployMode.K8S:
        _check_k8s(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker(ctx, g)
    else:
        _check_vm(ctx, g)

    return g


def _check_k8s(ctx: dict, g: CheckGroup):
    """K8s 部署: 检查各组件 Pod / Deployment / StatefulSet 状态。"""
    k8s_core = ctx.get("k8s_core")
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    if not k8s_core:
        g.warn("K8s 客户端", "kubernetes 库未安装，跳过 Pod 级检查")
        return

    # 获取所有 GitLab Pod
    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception as e:
        g.error("Pod 列表", f"获取失败: {e}")
        return

    if not pods.items:
        g.fatal("Pod 发现", f"未找到 GitLab Pod (namespace={ns}, selector={selector})")
        return

    # 按组件分类
    component_pods = {}
    for pod in pods.items:
        labels = pod.metadata.labels or {}
        # GitLab Helm chart 使用 app label
        app = labels.get("app", "")
        component_pods.setdefault(app, []).append(pod)

    # 检查每个核心组件
    found_components = set()
    for comp_key, comp_name in _CORE_COMPONENTS:
        matched = component_pods.get(comp_key, [])
        if not matched:
            # 某些组件可能没有 (如 registry 未启用)
            continue

        found_components.add(comp_key)
        # 过滤掉已完成的 Job Pod
        running_pods = [p for p in matched if p.status.phase != "Succeeded"]
        if not running_pods:
            g.ok(f"{comp_name} Pod", f"{len(matched)} 个 Pod (均已完成)")
            continue

        healthy = 0
        issues = []
        for pod in running_pods:
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

        if issues:
            if healthy == 0:
                g.error(f"{comp_name} Pod", f"0/{len(running_pods)} 健康",
                        detail="\n".join(issues))
            else:
                g.warn(f"{comp_name} Pod",
                       f"{healthy}/{len(running_pods)} 健康",
                       detail="\n".join(issues))
        else:
            g.ok(f"{comp_name} Pod", f"{healthy}/{len(running_pods)} 健康")

    # 汇总
    total_pods = sum(1 for p in pods.items if p.status.phase != "Succeeded")
    g.ok("Pod 总数", f"共 {total_pods} 个运行中的 Pod, "
         f"涵盖 {len(found_components)} 个组件")

    # 检查 Deployment / StatefulSet 副本数
    if k8s_apps:
        _check_k8s_replicas(k8s_apps, ns, selector, g)


def _check_k8s_replicas(k8s_apps, ns, selector, g):
    """检查 Deployment 和 StatefulSet 副本状态。"""
    try:
        deploys = k8s_apps.list_namespaced_deployment(ns, label_selector=selector)
        for dep in deploys.items:
            desired = dep.spec.replicas or 1
            ready = dep.status.ready_replicas or 0
            name = dep.metadata.name
            if ready < desired:
                g.error(f"Deployment {name}", f"副本 {ready}/{desired} 未达标")
            # 不逐个报 OK，太多了
    except Exception:
        pass

    try:
        stss = k8s_apps.list_namespaced_stateful_set(ns, label_selector=selector)
        for sts in stss.items:
            desired = sts.spec.replicas or 1
            ready = sts.status.ready_replicas or 0
            name = sts.metadata.name
            if ready < desired:
                g.error(f"StatefulSet {name}", f"副本 {ready}/{desired} 未达标")
    except Exception:
        pass


def _check_docker(ctx: dict, g: CheckGroup):
    """Docker 部署: 检查 GitLab 容器状态。"""
    docker_client = ctx.get("docker_client")
    container_name = ctx.get("docker_container")
    docker_image = ctx.get("docker_image", "gitlab/gitlab-ce")

    if docker_client:
        try:
            if container_name:
                containers = [docker_client.containers.get(container_name)]
            else:
                containers = docker_client.containers.list(
                    filters={"ancestor": docker_image})
            if not containers:
                g.fatal("Docker 容器", "未找到 GitLab 容器")
                return
            for c in containers:
                if c.status == "running":
                    health = c.attrs.get("State", {}).get("Health", {}).get("Status", "none")
                    if health == "healthy":
                        g.ok(f"容器 {c.name}", "running & healthy")
                    elif health == "unhealthy":
                        g.error(f"容器 {c.name}", "running 但 unhealthy")
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
            g.fatal("Docker 容器", "未找到运行中的 GitLab 容器")
    except Exception as e:
        g.error("Docker 检查", f"执行失败: {e}")


def _check_vm(ctx: dict, g: CheckGroup):
    """VM/裸机: 检查 GitLab 相关进程。"""
    services = [
        ("puma", "Puma (Webservice)"),
        ("sidekiq", "Sidekiq"),
        ("gitaly", "Gitaly"),
        ("gitlab-workhorse", "Workhorse"),
        ("nginx", "Nginx"),
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
                g.warn(f"{display_name} 进程", "未检测到进程")
        except Exception:
            pass
