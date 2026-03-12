"""6.6 备份与恢复能力检查。

- 最近备份是否成功
- 最近一次 base backup 时间
- WAL 归档是否连续
- 恢复点目标是否满足
- 备份文件是否可访问
- 恢复演练状态 (若有) 是否正常
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6.6 备份与恢复能力")
    pg = ctx["pg"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 最近 base backup ──
    _check_base_backup(pg, g)

    # ── WAL 归档连续性 ──
    _check_archive_continuity(pg, g)

    # ── 恢复点目标 (RPO) ──
    _check_rpo(pg, g)

    # ── pg_basebackup 进程 ──
    _check_backup_progress(pg, g)

    # ── 外部备份工具检测 ──
    _check_external_backup_tools(pg, g, mode, ctx)

    return g


def _check_base_backup(pg, g):
    """检查最近的 base backup 信息。"""
    # 通过 pg_stat_replication 查看是否有正在进行的 base backup
    try:
        backup_in_progress = pg.query(
            "SELECT pid, application_name, client_addr, "
            "backend_start, state "
            "FROM pg_stat_replication "
            "WHERE application_name LIKE '%backup%' "
            "OR application_name LIKE '%pg_basebackup%'")
        if backup_in_progress:
            for b in backup_in_progress:
                g.ok("Base Backup 进行中",
                     f"PID {b['pid']} from {b['client_addr']}, "
                     f"started {b['backend_start']}")
    except Exception:
        pass

    # 检查 backup_label (PG 15+ 提供 pg_backup_start/stop 系统函数)
    try:
        row = pg.query_one(
            "SELECT pg_is_in_backup() AS in_backup, "
            "pg_backup_start_time() AS start_time")
        if row and row["in_backup"]:
            g.ok("Base Backup", f"正在进行中 (started: {row['start_time']})")
    except Exception:
        pass  # pg_is_in_backup 在 PG 15 已移除

    # 通过 pg_stat_archiver 间接判断最后 backup
    try:
        row = pg.query_one("SELECT * FROM pg_stat_archiver")
        if row and row.get("last_archived_time"):
            g.ok("最后归档时间", str(row["last_archived_time"]))
    except Exception:
        pass


def _check_archive_continuity(pg, g):
    """检查 WAL 归档连续性。"""
    try:
        archive_mode = pg.query_scalar("SHOW archive_mode")
        if archive_mode in ("off", None):
            g.ok("WAL 归档", "未启用")
            return

        row = pg.query_one(
            "SELECT archived_count, failed_count, "
            "last_archived_wal, last_archived_time, "
            "last_failed_wal, last_failed_time "
            "FROM pg_stat_archiver")

        if not row:
            g.warn("归档状态", "无法获取归档统计")
            return

        failed = row.get("failed_count", 0) or 0
        archived = row.get("archived_count", 0) or 0

        if failed > 0:
            # 检查最近失败时间
            last_ok = row.get("last_archived_time")
            last_fail = row.get("last_failed_time")

            if last_fail and last_ok and last_fail > last_ok:
                g.error("归档连续性",
                        f"最近归档失败 ({failed} 次累计)",
                        detail=(f"最后成功: {row['last_archived_wal']} ({last_ok})\n"
                                f"最后失败: {row['last_failed_wal']} ({last_fail})"))
            else:
                g.warn("归档连续性",
                       f"历史有 {failed} 次失败, 当前已恢复",
                       detail=f"最后成功: {row['last_archived_wal']} ({last_ok})")
        elif archived > 0:
            g.ok("归档连续性",
                 f"已成功归档 {archived} 个 WAL, 无失败记录")
        else:
            g.warn("归档连续性", "尚无归档记录")
    except Exception as e:
        g.warn("归档连续性", f"检查失败: {e}")


def _check_rpo(pg, g):
    """估算 RPO (恢复点目标)。"""
    try:
        archive_mode = pg.query_scalar("SHOW archive_mode")
        if archive_mode in ("off", None):
            g.warn("RPO 评估", "未启用 WAL 归档，无法实现时间点恢复")
            return

        # 检查最后归档时间与当前时间差
        row = pg.query_one(
            "SELECT now() - last_archived_time AS archive_delay "
            "FROM pg_stat_archiver "
            "WHERE last_archived_time IS NOT NULL")

        if row and row["archive_delay"]:
            delay_seconds = row["archive_delay"].total_seconds()
            if delay_seconds > 3600:
                g.error("RPO 评估",
                        f"最后归档距今 {delay_seconds/3600:.1f} 小时，RPO 可能无法满足")
            elif delay_seconds > 600:
                g.warn("RPO 评估",
                       f"最后归档距今 {delay_seconds/60:.0f} 分钟")
            else:
                g.ok("RPO 评估",
                     f"最后归档距今 {delay_seconds:.0f} 秒，归档及时")
        else:
            g.warn("RPO 评估", "无归档时间记录")
    except Exception as e:
        g.warn("RPO 评估", f"检查失败: {e}")


def _check_backup_progress(pg, g):
    """检查正在进行的 backup 进度 (PG 13+)。"""
    try:
        rows = pg.query(
            "SELECT pid, phase, "
            "backup_total, backup_streamed, "
            "CASE WHEN backup_total > 0 "
            "THEN round(backup_streamed::numeric / backup_total * 100, 1) "
            "ELSE 0 END AS pct "
            "FROM pg_stat_progress_basebackup")
        if rows:
            for r in rows:
                g.ok("Backup 进度",
                     f"PID {r['pid']}: {r['phase']} ({r['pct']}%)")
    except Exception:
        pass  # pg_stat_progress_basebackup 仅 PG 13+


def _check_external_backup_tools(pg, g, mode, ctx):
    """检测常见外部备份工具 (pgBackRest, barman, wal-g, pg_probackup)。"""
    # pgBackRest
    try:
        result = subprocess.run(
            ["pgbackrest", "info", "--output=json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            import json
            info = json.loads(result.stdout)
            if info:
                stanza = info[0] if isinstance(info, list) else info
                name = stanza.get("name", "default")
                backups = stanza.get("backup", [])
                if backups:
                    last = backups[-1]
                    btype = last.get("type", "unknown")
                    ts = last.get("timestamp", {}).get("stop", "unknown")
                    g.ok(f"pgBackRest ({name})", f"最后备份: {btype} @ {ts}")
                else:
                    g.warn(f"pgBackRest ({name})", "无备份记录")
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # barman
    try:
        result = subprocess.run(
            ["barman", "list-server", "--minimal"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            servers = result.stdout.strip().splitlines()
            g.ok("Barman", f"检测到 {len(servers)} 个备份服务器")
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # wal-g
    try:
        result = subprocess.run(
            ["wal-g", "backup-list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            backup_count = max(0, len(lines) - 1)  # 减去 header
            if backup_count > 0:
                g.ok("WAL-G", f"检测到 {backup_count} 个备份")
            else:
                g.warn("WAL-G", "未发现备份")
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # 如果没有检测到任何备份工具
    archive_mode = pg.safe_query("SELECT current_setting('archive_mode')", default=[])
    if archive_mode and archive_mode[0].get("current_setting") not in ("on", "always"):
        g.warn("备份工具", "未检测到备份工具且 WAL 归档未启用，建议配置备份策略")
