"""6.4 存储与 WAL 检查。

- 数据目录空间是否充足
- WAL 目录空间是否充足
- checkpoint 是否过于频繁
- archive 是否成功
- WAL 堆积是否异常
- 磁盘 I/O 延迟是否升高
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6.4 存储与 WAL")
    pg = ctx["pg"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 数据目录信息 ──
    _check_data_directory(pg, g, mode, ctx)

    # ── WAL 相关检查 ──
    _check_wal(pg, g)

    # ── Checkpoint ──
    _check_checkpoint(pg, g)

    # ── Archive ──
    _check_archive(pg, g)

    # ── 磁盘 I/O (仅 VM 模式可检测) ──
    if mode == DeployMode.VM:
        _check_disk_io(g)

    return g


def _check_data_directory(pg, g, mode, ctx):
    """检查数据目录空间。"""
    try:
        data_dir = pg.query_scalar("SHOW data_directory")
        g.ok("数据目录", data_dir)
    except Exception as e:
        g.warn("数据目录", f"获取失败: {e}")
        return

    # 通过 SQL 获取数据库大小 (不依赖 OS 命令)
    try:
        rows = pg.query(
            "SELECT datname, pg_database_size(datname) AS size_bytes "
            "FROM pg_database WHERE datallowconn ORDER BY size_bytes DESC")
        total_bytes = sum(r["size_bytes"] for r in rows)
        total_gb = total_bytes / (1024**3)
        detail_lines = []
        for r in rows[:10]:
            size_mb = r["size_bytes"] / (1024**2)
            detail_lines.append(f"{r['datname']}: {size_mb:.1f} MB")
        g.ok("数据库总大小", f"{total_gb:.2f} GB",
             detail="\n".join(detail_lines))
    except Exception as e:
        g.warn("数据库大小", f"获取失败: {e}")

    # 文件系统空间检查
    _check_filesystem_space(data_dir, g, mode, ctx)


def _check_filesystem_space(data_dir, g, mode, ctx):
    """检查文件系统剩余空间。"""
    if mode == DeployMode.K8S:
        # K8s 下通过 PVC 或 exec 检查
        k8s_core = ctx.get("k8s_core")
        if k8s_core:
            _check_k8s_pvc_space(ctx, g)
        return

    if mode == DeployMode.DOCKER:
        _check_docker_disk_space(ctx, g, data_dir)
        return

    # VM: 直接 df
    try:
        result = subprocess.run(
            ["df", "-h", data_dir],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 5:
                    usage_pct = int(parts[4].rstrip("%"))
                    avail = parts[3]
                    if usage_pct > 95:
                        g.fatal("磁盘空间", f"使用率 {usage_pct}%, 剩余 {avail}")
                    elif usage_pct > 85:
                        g.error("磁盘空间", f"使用率 {usage_pct}%, 剩余 {avail}")
                    elif usage_pct > 75:
                        g.warn("磁盘空间", f"使用率 {usage_pct}%, 剩余 {avail}")
                    else:
                        g.ok("磁盘空间", f"使用率 {usage_pct}%, 剩余 {avail}")
    except Exception:
        pass


def _check_k8s_pvc_space(ctx, g):
    """K8s 环境下检查 PVC 状态。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
        pvc_names = set()
        for pod in (pods.items or []):
            for vol in (pod.spec.volumes or []):
                if vol.persistent_volume_claim:
                    pvc_names.add(vol.persistent_volume_claim.claim_name)

        if not pvc_names:
            g.warn("K8s PVC", "未找到关联的 PVC")
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
                    g.error(f"PVC {pvc_name}", f"状态: {phase}")
            except Exception as e:
                g.warn(f"PVC {pvc_name}", f"获取状态失败: {e}")
    except Exception as e:
        g.warn("K8s PVC", f"检查失败: {e}")


def _check_docker_disk_space(ctx, g, data_dir):
    """Docker 环境下检查磁盘空间。"""
    docker_client = ctx.get("docker_client")
    container_name = ctx.get("docker_container")

    if docker_client and container_name:
        try:
            c = docker_client.containers.get(container_name)
            result = c.exec_run(f"df -h {data_dir}")
            if result.exit_code == 0:
                output = result.output.decode("utf-8", errors="replace")
                lines = output.strip().splitlines()
                if len(lines) >= 2:
                    parts = lines[1].split()
                    if len(parts) >= 5:
                        usage_pct = int(parts[4].rstrip("%"))
                        avail = parts[3]
                        if usage_pct > 90:
                            g.error("磁盘空间 (容器)", f"使用率 {usage_pct}%, 剩余 {avail}")
                        elif usage_pct > 75:
                            g.warn("磁盘空间 (容器)", f"使用率 {usage_pct}%, 剩余 {avail}")
                        else:
                            g.ok("磁盘空间 (容器)", f"使用率 {usage_pct}%, 剩余 {avail}")
                        return
        except Exception:
            pass

    # 降级: docker exec
    try:
        docker_image = ctx.get("docker_image", "postgres")
        result = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={docker_image}", "-q"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            cid = result.stdout.strip().splitlines()[0]
            result = subprocess.run(
                ["docker", "exec", cid, "df", "-h", data_dir],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if len(lines) >= 2:
                    parts = lines[1].split()
                    if len(parts) >= 5:
                        usage_pct = int(parts[4].rstrip("%"))
                        avail = parts[3]
                        if usage_pct > 90:
                            g.error("磁盘空间 (容器)", f"使用率 {usage_pct}%, 剩余 {avail}")
                        elif usage_pct > 75:
                            g.warn("磁盘空间 (容器)", f"使用率 {usage_pct}%, 剩余 {avail}")
                        else:
                            g.ok("磁盘空间 (容器)", f"使用率 {usage_pct}%, 剩余 {avail}")
    except Exception:
        pass


def _check_wal(pg, g):
    """检查 WAL 相关状态。"""
    # WAL 目录大小 (PG 10+)
    try:
        wal_dir = pg.query_scalar("SHOW wal_directory") or "pg_wal"
        g.ok("WAL 目录", wal_dir)
    except Exception:
        pass

    # WAL 段大小和数量
    try:
        wal_size = pg.query_scalar(
            "SELECT pg_size_pretty(sum(size)) FROM pg_ls_waldir()")
        if wal_size:
            g.ok("WAL 总大小", wal_size)
    except Exception as e:
        g.warn("WAL 大小", f"获取失败 (需要 superuser): {e}")

    # WAL 段数量
    try:
        wal_count = pg.query_scalar("SELECT count(*) FROM pg_ls_waldir()")
        if wal_count is not None:
            wal_count = int(wal_count)
            if wal_count > 1000:
                g.error("WAL 段数量", f"{wal_count} 个 (WAL 堆积严重)")
            elif wal_count > 500:
                g.warn("WAL 段数量", f"{wal_count} 个 (WAL 堆积)")
            else:
                g.ok("WAL 段数量", f"{wal_count} 个")
    except Exception:
        pass

    # WAL 写入速率 (通过 pg_stat_wal, PG 14+)
    try:
        row = pg.query_one(
            "SELECT wal_records, wal_bytes, wal_buffers_full, "
            "stats_reset FROM pg_stat_wal")
        if row:
            wal_gb = int(row["wal_bytes"]) / (1024**3)
            g.ok("WAL 写入统计",
                 f"总计 {wal_gb:.2f} GB, buffers_full: {row['wal_buffers_full']}",
                 detail=f"自 {row['stats_reset']} 起")
    except Exception:
        pass  # pg_stat_wal 仅 PG 14+


def _check_checkpoint(pg, g):
    """检查 checkpoint 频率和性能。"""
    try:
        row = pg.query_one(
            "SELECT checkpoints_timed, checkpoints_req, "
            "checkpoint_write_time, checkpoint_sync_time, "
            "buffers_checkpoint, buffers_backend, "
            "stats_reset "
            "FROM pg_stat_bgwriter")
        if not row:
            return

        timed = row["checkpoints_timed"]
        req = row["checkpoints_req"]
        total = timed + req

        if total == 0:
            g.ok("Checkpoint", "尚无 checkpoint 记录")
            return

        req_ratio = (req / total) * 100

        write_time_s = row["checkpoint_write_time"] / 1000
        sync_time_s = row["checkpoint_sync_time"] / 1000

        detail = (f"timed: {timed}, requested: {req}\n"
                  f"write_time: {write_time_s:.1f}s, sync_time: {sync_time_s:.1f}s\n"
                  f"buffers_checkpoint: {row['buffers_checkpoint']}\n"
                  f"buffers_backend: {row['buffers_backend']}\n"
                  f"stats_reset: {row['stats_reset']}")

        if req_ratio > 50:
            g.warn("Checkpoint",
                   f"请求触发占比 {req_ratio:.0f}% ({req}/{total})，建议增大 checkpoint 间隔",
                   detail=detail)
        else:
            g.ok("Checkpoint",
                 f"timed: {timed}, requested: {req} (请求占比 {req_ratio:.0f}%)",
                 detail=detail)

        # backend buffers 过高说明 shared_buffers 或 checkpoint 频率不足
        if row["buffers_backend"] > row["buffers_checkpoint"] and row["buffers_checkpoint"] > 0:
            ratio = row["buffers_backend"] / row["buffers_checkpoint"]
            if ratio > 1:
                g.warn("Backend Buffers",
                       f"backend_buffers/checkpoint_buffers = {ratio:.1f}，"
                       "考虑增大 shared_buffers 或调整 checkpoint 参数")
    except Exception as e:
        g.warn("Checkpoint", f"检查失败: {e}")


def _check_archive(pg, g):
    """检查 WAL 归档状态。"""
    try:
        archive_mode = pg.query_scalar("SHOW archive_mode")
        if archive_mode in ("off", None):
            g.ok("WAL 归档", "未启用 (archive_mode=off)")
            return

        g.ok("WAL 归档", f"已启用 (archive_mode={archive_mode})")

        archive_cmd = pg.query_scalar("SHOW archive_command")
        if archive_cmd:
            g.ok("归档命令", archive_cmd[:100])

        # pg_stat_archiver
        row = pg.query_one(
            "SELECT archived_count, last_archived_wal, last_archived_time, "
            "failed_count, last_failed_wal, last_failed_time, "
            "stats_reset FROM pg_stat_archiver")
        if row:
            if row["failed_count"] and int(row["failed_count"]) > 0:
                g.error("归档状态",
                        f"失败 {row['failed_count']} 次",
                        detail=(f"最后成功: {row['last_archived_wal']} ({row['last_archived_time']})\n"
                                f"最后失败: {row['last_failed_wal']} ({row['last_failed_time']})"))
            else:
                g.ok("归档状态",
                     f"已归档 {row['archived_count']} 个 WAL",
                     detail=f"最后归档: {row['last_archived_wal']} ({row['last_archived_time']})")
    except Exception as e:
        g.warn("WAL 归档", f"检查失败: {e}")


def _check_disk_io(g):
    """VM 模式下检查磁盘 I/O (使用 iostat)。"""
    try:
        result = subprocess.run(
            ["iostat", "-x", "1", "1"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            # 查找高 await 或高 util 的磁盘
            high_io = []
            for line in lines:
                parts = line.split()
                if not parts or not parts[0].startswith(("sd", "nvme", "vd", "xvd", "dm-")):
                    continue
                try:
                    # 不同 OS 的 iostat 输出格式不同，尝试提取 %util (通常是最后一列)
                    util = float(parts[-1])
                    if util > 90:
                        high_io.append(f"{parts[0]}: %util={util:.1f}")
                except (ValueError, IndexError):
                    continue
            if high_io:
                g.warn("磁盘 I/O", f"{len(high_io)} 个磁盘利用率高",
                       detail="\n".join(high_io))
            else:
                g.ok("磁盘 I/O", "磁盘利用率正常")
    except FileNotFoundError:
        pass
    except Exception:
        pass
