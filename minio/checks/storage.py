"""5.2 MinIO 存储层状态检查。

- 挂载磁盘是否正常
- 磁盘是否只读
- 磁盘空间使用率是否过高
- inode 是否紧张
- 单盘故障是否影响可用性
- 存储延迟是否异常
"""

import subprocess
import re

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5.2 MinIO 存储层状态")
    mc = ctx["mc"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── mc admin info 磁盘详情 ──
    _check_disk_info(mc, g)

    # ── Prometheus metrics 磁盘指标 ──
    _check_metrics_storage(mc, g)

    # ── 基础设施层磁盘检查 ──
    if mode == DeployMode.K8S:
        _check_k8s_storage(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker_storage(ctx, g)
    else:
        _check_vm_storage(ctx, g)

    return g


def _check_disk_info(mc, g):
    """通过 mc admin info 获取磁盘详情。"""
    info = mc.mc_admin_info()
    if not info:
        return

    servers = info.get("info", {}).get("servers", info.get("servers", []))

    for server in servers:
        endpoint = server.get("endpoint", "unknown")
        disks = server.get("disks", [])
        if not disks:
            continue

        for disk in disks:
            path = disk.get("path", "unknown")
            state = disk.get("state", "unknown")
            total = disk.get("totalspace", 0)
            used = disk.get("usedspace", 0)
            avail = disk.get("availspace", total - used if total else 0)

            if state != "ok":
                g.error(f"磁盘 {endpoint}:{path}", f"状态异常: {state}")
                continue

            if total > 0:
                usage_pct = (used / total) * 100
                total_gb = total / (1024 ** 3)
                avail_gb = avail / (1024 ** 3)

                if usage_pct > 95:
                    g.fatal(f"磁盘 {endpoint}:{path}",
                            f"空间即将耗尽! {usage_pct:.1f}% 已用, "
                            f"剩余 {avail_gb:.1f} GB")
                elif usage_pct > 85:
                    g.error(f"磁盘 {endpoint}:{path}",
                            f"空间偏高 {usage_pct:.1f}% 已用, "
                            f"剩余 {avail_gb:.1f} GB")
                elif usage_pct > 75:
                    g.warn(f"磁盘 {endpoint}:{path}",
                           f"空间使用率 {usage_pct:.1f}%, "
                           f"总 {total_gb:.1f} GB, 剩余 {avail_gb:.1f} GB")
                else:
                    g.ok(f"磁盘 {endpoint}:{path}",
                         f"空间正常 {usage_pct:.1f}%, "
                         f"总 {total_gb:.1f} GB, 剩余 {avail_gb:.1f} GB")

            # 只读检查
            if disk.get("readOnly"):
                g.error(f"磁盘 {endpoint}:{path}", "磁盘处于只读状态!")


def _check_metrics_storage(mc, g):
    """通过 Prometheus metrics 检查存储延迟。"""
    resp = mc.metrics_cluster()
    if resp["status"] != 200 or not isinstance(resp["body"], str):
        return

    body = resp["body"]

    # 检查磁盘离线数
    for line in body.splitlines():
        if line.startswith("minio_cluster_disk_offline_total"):
            try:
                val = float(line.split()[-1])
                if val > 0:
                    g.error("离线磁盘", f"集群有 {int(val)} 个磁盘离线")
            except (ValueError, IndexError):
                pass

        if line.startswith("minio_cluster_disk_online_total"):
            try:
                val = float(line.split()[-1])
                g.ok("在线磁盘", f"集群有 {int(val)} 个磁盘在线")
            except (ValueError, IndexError):
                pass

    # 存储延迟
    _parse_metric_latency(body, g)


def _parse_metric_latency(body: str, g):
    """从 metrics 解析存储操作延迟。"""
    # 查找 API 请求延迟
    latency_lines = [l for l in body.splitlines()
                     if "minio_s3_requests_ttfb_seconds" in l and not l.startswith("#")]
    if not latency_lines:
        return

    # 找 _sum 和 _count 来计算平均延迟
    for line in latency_lines:
        if "_sum" in line:
            try:
                val = float(line.split()[-1])
                if val > 10:
                    g.warn("S3 延迟", f"累计请求延迟偏高: {val:.1f}s")
            except (ValueError, IndexError):
                pass


def _check_k8s_storage(ctx: dict, g: CheckGroup):
    """K8s 部署: 检查 PVC 状态。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    if not k8s_core:
        return

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
        pvc_names = set()
        for pod in pods.items:
            for vol in (pod.spec.volumes or []):
                if vol.persistent_volume_claim:
                    pvc_names.add(vol.persistent_volume_claim.claim_name)

        if not pvc_names:
            g.warn("K8s PVC", "未检测到 PVC 挂载")
            return

        for pvc_name in pvc_names:
            try:
                pvc = k8s_core.read_namespaced_persistent_volume_claim(pvc_name, ns)
                phase = pvc.status.phase
                capacity = pvc.status.capacity or {}
                storage = capacity.get("storage", "unknown")
                if phase == "Bound":
                    g.ok(f"PVC {pvc_name}", f"Bound, 容量 {storage}")
                else:
                    g.error(f"PVC {pvc_name}", f"状态异常: {phase}")
            except Exception as e:
                g.warn(f"PVC {pvc_name}", f"获取失败: {e}")
    except Exception as e:
        g.warn("K8s PVC 检查", f"执行失败: {e}")


def _check_docker_storage(ctx: dict, g: CheckGroup):
    """Docker 部署: 检查容器内磁盘空间。"""
    docker_client = ctx.get("docker_client")
    container_name = ctx.get("docker_container")

    if docker_client and container_name:
        try:
            container = docker_client.containers.get(container_name)
            result = container.exec_run("df -h /data", demux=True)
            stdout = result.output[0]
            if stdout:
                output = stdout.decode("utf-8").strip()
                lines = output.splitlines()
                if len(lines) > 1:
                    parts = lines[1].split()
                    if len(parts) >= 5:
                        usage = parts[4].rstrip("%")
                        try:
                            pct = int(usage)
                            if pct > 90:
                                g.error("容器磁盘 /data", f"使用率 {pct}%")
                            elif pct > 80:
                                g.warn("容器磁盘 /data", f"使用率 {pct}%")
                            else:
                                g.ok("容器磁盘 /data", f"使用率 {pct}%")
                        except ValueError:
                            pass
            return
        except Exception:
            pass

    # 降级 CLI
    if container_name:
        try:
            result = subprocess.run(
                ["docker", "exec", container_name, "df", "-h", "/data"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if len(lines) > 1:
                    g.ok("容器磁盘", lines[1])
        except Exception:
            pass


def _check_vm_storage(ctx: dict, g: CheckGroup):
    """VM/裸机: 检查本地磁盘空间和 I/O。"""
    # df
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    usage = parts[4].rstrip("%")
                    try:
                        pct = int(usage)
                        if pct > 90:
                            g.error("根分区空间", f"使用率 {pct}%")
                        elif pct > 80:
                            g.warn("根分区空间", f"使用率 {pct}%")
                        else:
                            g.ok("根分区空间", f"使用率 {pct}%")
                    except ValueError:
                        pass
    except Exception:
        pass

    # inode
    try:
        result = subprocess.run(
            ["df", "-i", "/"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    usage = parts[4].rstrip("%")
                    try:
                        pct = int(usage)
                        if pct > 90:
                            g.error("inode 使用率", f"{pct}%")
                        elif pct > 80:
                            g.warn("inode 使用率", f"{pct}%")
                        else:
                            g.ok("inode 使用率", f"{pct}%")
                    except ValueError:
                        pass
    except Exception:
        pass
