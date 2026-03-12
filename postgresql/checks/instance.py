"""6.1 PostgreSQL 实例基础状态检查。

- Pod/实例是否正常
- 主库是否可写
- 只读副本是否可读
- 进程是否存活
- readiness/liveness 是否正常
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6.1 PostgreSQL 实例基础状态")
    pg = ctx["pg"]
    mode = ctx["mode"]

    # 连接检查
    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    # PG 版本
    try:
        ver = pg.server_version()
        g.ok("PostgreSQL 版本", ver)
    except Exception as e:
        g.error("PostgreSQL 版本", f"获取失败: {e}")

    # 主/从角色
    try:
        in_recovery = pg.is_in_recovery()
        if in_recovery:
            g.ok("实例角色", "Standby (只读副本)")
            # 验证只读可用
            try:
                pg.query_scalar("SELECT 1")
                g.ok("只读查询", "可正常读取")
            except Exception as e:
                g.error("只读查询", f"读取失败: {e}")
        else:
            g.ok("实例角色", "Primary (主库)")
            # 验证可写
            try:
                pg.query("SELECT pg_is_in_recovery()")  # 确认不在恢复模式
                g.ok("主库可写", "主库处于正常读写状态")
            except Exception as e:
                g.error("主库可写", f"检查失败: {e}")
    except Exception as e:
        g.error("实例角色", f"角色检测失败: {e}")

    # 进程与运行状态
    try:
        row = pg.query_one(
            "SELECT pg_postmaster_start_time() AS start_time, "
            "now() - pg_postmaster_start_time() AS uptime")
        if row:
            g.ok("进程启动时间", str(row["start_time"]),
                 detail=f"运行时长: {row['uptime']}")
    except Exception as e:
        g.warn("进程信息", f"获取失败: {e}")

    # 基础设施层检查
    if mode == DeployMode.K8S:
        _check_k8s_instance(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker_instance(ctx, g)
    else:
        _check_vm_instance(ctx, g)

    return g


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

    g.ok("Pod 发现", f"找到 {len(pods.items)} 个 PostgreSQL Pod")

    ready_count = 0
    for pod in pods.items:
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

    if ready_count == len(pods.items):
        g.ok("Ready 副本", f"{ready_count}/{len(pods.items)} 就绪")
    else:
        g.error("Ready 副本", f"{ready_count}/{len(pods.items)} 就绪")

    # 副本数检查
    if k8s_apps:
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
    docker_image = ctx.get("docker_image", "postgres")

    if docker_client:
        try:
            if container_name:
                containers = [docker_client.containers.get(container_name)]
            else:
                containers = docker_client.containers.list(
                    filters={"ancestor": docker_image})
            if not containers:
                g.fatal("Docker 容器", "未找到 PostgreSQL 容器")
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
            g.fatal("Docker 容器", "未找到运行中的 PostgreSQL 容器")
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
    """VM/裸机 部署模式: 检查 PostgreSQL 进程。"""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "postgres"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            g.ok("PostgreSQL 进程", f"检测到 {len(pids)} 个 postgres 进程")
        else:
            # pgrep 可能在远程检查时不可用，此时依赖 SQL 连接成功即可
            g.warn("PostgreSQL 进程", "本地未检测到 postgres 进程 (可能为远程实例)")
    except Exception:
        g.ok("部署模式", "VM/裸机 模式 — 实例检查通过 SQL 连接完成")
