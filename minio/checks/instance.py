"""5.1 MinIO 实例与集群状态检查。

- MinIO Pod 是否 Running / Ready
- 副本/实例数是否达标
- MinIO 集群状态是否正常
- 是否有节点离线
- 分布式模式下磁盘数是否满足纠删码要求
- 是否存在 quorum 风险
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5.1 MinIO 实例与集群状态")
    mc = ctx["mc"]
    mode = ctx["mode"]

    # ── 健康端点 ──
    _check_health_endpoints(mc, g)

    # ── mc admin info (集群信息) ──
    _check_cluster_info(mc, g)

    # ── 基础设施层检查 ──
    if mode == DeployMode.K8S:
        _check_k8s_instance(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker_instance(ctx, g)
    else:
        _check_vm_instance(ctx, g)

    return g


def _check_health_endpoints(mc, g):
    """检查 MinIO 健康端点。"""
    # liveness
    resp = mc.health_live()
    if resp["status"] == 200:
        g.ok("Liveness", "MinIO 实例存活")
    elif resp["status"] == 0:
        g.fatal("Liveness", f"健康检查失败: {resp['body']}")
        return
    else:
        # 旧版 MinIO 可能没有 /minio/health/* 端点
        # 通过 S3 API 验证存活
        buckets = mc.list_buckets_sdk()
        if buckets is not None:
            g.ok("Liveness", "MinIO 实例存活 (通过 S3 API 验证)")
        else:
            # 端口可达但没有标准端点
            root_resp = mc.get("/")
            if root_resp["status"] != 0:
                g.ok("Liveness", f"MinIO 端口可达 (status={root_resp['status']})")
            else:
                g.fatal("Liveness", f"健康检查失败 (status={resp['status']})")
        return  # 旧版无 readiness/cluster 端点，跳过

    # readiness / cluster
    resp = mc.health_ready()
    if resp["status"] == 200:
        g.ok("Readiness", "MinIO 集群可写就绪")
    else:
        g.error("Readiness", f"集群未就绪 (status={resp['status']})")

    # cluster verify
    resp = mc.health_cluster()
    if resp["status"] == 200:
        g.ok("集群健康", "集群验证通过")
    elif resp["status"] == 412:
        g.error("集群健康", "集群验证失败 — quorum 不足或有节点离线")
    elif resp["status"] != 0:
        g.warn("集群健康", f"集群验证返回 status={resp['status']}")


def _check_cluster_info(mc, g):
    """通过 mc admin info 获取集群详情。"""
    info = mc.mc_admin_info()
    if not info:
        return

    # 服务器信息
    servers = info.get("info", {}).get("servers", [])
    if not servers:
        # 尝试旧版格式
        servers = info.get("servers", [])

    if servers:
        online = sum(1 for s in servers if s.get("state") == "ok")
        total = len(servers)
        if online == total:
            g.ok("集群节点", f"{online}/{total} 节点在线")
        elif online > 0:
            g.error("集群节点", f"仅 {online}/{total} 节点在线")
            offline = [s.get("endpoint", "unknown") for s in servers if s.get("state") != "ok"]
            if offline:
                g.error("离线节点", ", ".join(offline))
        else:
            g.fatal("集群节点", "所有节点离线")

        # 磁盘统计
        total_disks = 0
        online_disks = 0
        for s in servers:
            for d in s.get("disks", []):
                total_disks += 1
                if d.get("state") == "ok":
                    online_disks += 1
        if total_disks > 0:
            if online_disks == total_disks:
                g.ok("磁盘状态", f"{online_disks}/{total_disks} 磁盘在线")
            elif online_disks >= total_disks // 2 + 1:
                g.warn("磁盘状态",
                       f"{online_disks}/{total_disks} 磁盘在线，"
                       f"{total_disks - online_disks} 个离线")
            else:
                g.fatal("磁盘状态",
                        f"仅 {online_disks}/{total_disks} 磁盘在线，"
                        "低于纠删码最低要求!")

    # 模式
    mode_str = info.get("info", {}).get("mode", info.get("mode", ""))
    if mode_str:
        g.ok("运行模式", mode_str)

    # 版本
    version = info.get("info", {}).get("version", info.get("version", ""))
    if version:
        g.ok("MinIO 版本", version)


def _check_k8s_instance(ctx: dict, g: CheckGroup):
    """K8s 部署模式: 检查 Pod / Deployment / StatefulSet 状态。"""
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

    # 过滤掉已完成的 Job Pod
    running_pods = [p for p in pods.items if p.status.phase != "Succeeded"]
    if not running_pods:
        g.warn("Pod 发现", "所有 Pod 已完成 (Job)，无运行中的 MinIO 实例")
        return

    g.ok("Pod 发现", f"找到 {len(running_pods)} 个 MinIO Pod"
         + (f" (另有 {len(pods.items) - len(running_pods)} 个已完成的 Job Pod)"
            if len(running_pods) < len(pods.items) else ""))

    ready_count = 0
    for pod in running_pods:
        pod_name = pod.metadata.name
        phase = pod.status.phase

        if phase != "Running":
            g.error(f"Pod {pod_name}", f"阶段: {phase}")
            continue

        conditions = {c.type: c.status for c in (pod.status.conditions or [])}
        if conditions.get("Ready") == "True":
            ready_count += 1
        else:
            g.warn(f"Pod {pod_name}", "Running 但未 Ready")

        for cs in (pod.status.container_statuses or []):
            if cs.restart_count > 5:
                g.warn(f"Pod {pod_name}/{cs.name}", f"重启次数: {cs.restart_count}")

    if ready_count == len(running_pods):
        g.ok("Ready 副本", f"{ready_count}/{len(running_pods)} 就绪")
    else:
        g.error("Ready 副本", f"{ready_count}/{len(running_pods)} 就绪")

    # 副本数检查 (Deployment 或 StatefulSet)
    if k8s_apps:
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
    docker_image = ctx.get("docker_image", "minio/minio")

    if docker_client:
        try:
            if container_name:
                containers = [docker_client.containers.get(container_name)]
            else:
                containers = docker_client.containers.list(
                    filters={"ancestor": docker_image})
            if not containers:
                g.fatal("Docker 容器", "未找到 MinIO 容器")
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
                    if restart_count > 5:
                        g.warn(f"容器 {c.name}", f"重启次数: {restart_count}")
                else:
                    g.error(f"容器 {c.name}", f"状态: {c.status}")
            return
        except Exception as e:
            g.warn("Docker SDK", f"获取容器信息失败，降级使用 CLI: {e}")

    # 降级: 使用 docker CLI
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={docker_image}",
             "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            g.error("Docker 容器", f"docker ps 失败: {result.stderr.strip()}")
            return
        lines = result.stdout.strip().splitlines()
        if not lines:
            g.fatal("Docker 容器", "未找到运行中的 MinIO 容器")
            return
        for line in lines:
            parts = line.split("\t")
            name = parts[1] if len(parts) > 1 else parts[0]
            status = parts[2] if len(parts) > 2 else "unknown"
            if "Up" in status:
                g.ok(f"容器 {name}", f"运行中 ({status})")
            else:
                g.error(f"容器 {name}", f"状态异常: {status}")
    except Exception as e:
        g.error("Docker 检查", f"执行失败: {e}")


def _check_vm_instance(ctx: dict, g: CheckGroup):
    """VM/裸机 部署模式: 检查 MinIO 进程。"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "minio server"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            g.ok("MinIO 进程", f"检测到 {len(pids)} 个 minio 进程")
        else:
            g.warn("MinIO 进程", "本地未检测到 minio 进程 (可能为远程实例)")
    except Exception:
        g.ok("部署模式", "VM/裸机 模式 — 实例检查通过 HTTP 端点完成")
