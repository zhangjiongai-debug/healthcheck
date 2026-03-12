"""6.7 PostgreSQL 风险预警。

- 磁盘即将写满
- 连接数逼近上限
- replication lag 持续升高
- autovacuum 落后
- 长事务阻塞 vacuum
- checkpoint 过密
- 锁冲突激增
- 备份失败
- 主从角色异常漂移
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6.7 PostgreSQL 风险预警")
    pg = ctx["pg"]

    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 连接数逼近上限 ──
    _warn_connections(pg, g)

    # ── replication lag ──
    _warn_replication_lag(pg, g)

    # ── autovacuum 落后: 接近 wraparound ──
    _warn_autovacuum_wraparound(pg, g)

    # ── 长事务阻塞 vacuum ──
    _warn_long_tx_blocking_vacuum(pg, g)

    # ── checkpoint 过密 ──
    _warn_checkpoint_frequency(pg, g)

    # ── 锁冲突 ──
    _warn_lock_conflicts(pg, g)

    # ── 备份失败 ──
    _warn_backup_failure(pg, g)

    # ── 主从角色异常 ──
    _warn_role_anomaly(pg, g)

    # ── XID wraparound 风险 ──
    _warn_xid_wraparound(pg, g)

    return g


def _warn_connections(pg, g):
    """连接数预警。"""
    try:
        max_conn = int(pg.query_scalar("SHOW max_connections") or 100)
        reserved = int(pg.query_scalar("SHOW superuser_reserved_connections") or 3)
        current = int(pg.query_scalar(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE backend_type = 'client backend'") or 0)

        available = max_conn - reserved
        usage_pct = (current / available * 100) if available > 0 else 0

        if usage_pct > 95:
            g.fatal("连接数预警", f"连接数即将耗尽! {current}/{available} ({usage_pct:.0f}%)")
        elif usage_pct > 85:
            g.error("连接数预警", f"连接数接近上限 {current}/{available} ({usage_pct:.0f}%)")
        elif usage_pct > 75:
            g.warn("连接数预警", f"连接数偏高 {current}/{available} ({usage_pct:.0f}%)")
        else:
            g.ok("连接数预警", f"连接数正常 {current}/{available} ({usage_pct:.0f}%)")
    except Exception:
        pass


def _warn_replication_lag(pg, g):
    """复制延迟预警。"""
    try:
        in_recovery = pg.is_in_recovery()
        if in_recovery:
            lag_row = pg.query_one(
                "SELECT now() - pg_last_xact_replay_timestamp() AS lag")
            if lag_row and lag_row["lag"]:
                lag_s = lag_row["lag"].total_seconds()
                if lag_s > 600:
                    g.error("复制延迟预警", f"从库延迟 {lag_s:.0f} 秒")
                elif lag_s > 60:
                    g.warn("复制延迟预警", f"从库延迟 {lag_s:.0f} 秒")
                else:
                    g.ok("复制延迟预警", f"从库延迟 {lag_s:.1f} 秒")
        else:
            # 主库检查从库 lag
            senders = pg.safe_query(
                "SELECT application_name, "
                "pg_wal_lsn_diff(sent_lsn, replay_lsn) AS lag_bytes "
                "FROM pg_stat_replication", default=[])
            max_lag_mb = 0
            for s in senders:
                lag_mb = int(s.get("lag_bytes") or 0) / (1024 * 1024)
                max_lag_mb = max(max_lag_mb, lag_mb)
            if max_lag_mb > 1024:
                g.error("复制延迟预警", f"最大从库延迟 {max_lag_mb:.0f} MB")
            elif max_lag_mb > 100:
                g.warn("复制延迟预警", f"最大从库延迟 {max_lag_mb:.1f} MB")
            elif senders:
                g.ok("复制延迟预警", f"最大从库延迟 {max_lag_mb:.1f} MB")
    except Exception:
        pass


def _warn_autovacuum_wraparound(pg, g):
    """autovacuum wraparound 风险预警。"""
    try:
        rows = pg.query(
            "SELECT datname, age(datfrozenxid) AS age, "
            "current_setting('autovacuum_freeze_max_age')::bigint AS freeze_max "
            "FROM pg_database "
            "WHERE datallowconn "
            "ORDER BY age DESC")

        for r in rows:
            age = int(r["age"])
            freeze_max = int(r["freeze_max"])
            pct = (age / 2147483647) * 100  # max XID

            if pct > 75:
                g.fatal(f"Wraparound 风险 ({r['datname']})",
                        f"XID age = {age:,} ({pct:.1f}% of max)，"
                        "急需 VACUUM FREEZE!")
            elif pct > 50:
                g.error(f"Wraparound 风险 ({r['datname']})",
                        f"XID age = {age:,} ({pct:.1f}% of max)")
            elif age > freeze_max:
                g.warn(f"Wraparound 预警 ({r['datname']})",
                       f"XID age = {age:,} 超过 autovacuum_freeze_max_age ({freeze_max:,})")
    except Exception:
        pass


def _warn_long_tx_blocking_vacuum(pg, g):
    """长事务阻塞 vacuum 预警。"""
    try:
        rows = pg.query(
            "SELECT pid, usename, datname, "
            "now() - xact_start AS duration, "
            "left(query, 80) AS query "
            "FROM pg_stat_activity "
            "WHERE xact_start IS NOT NULL "
            "AND now() - xact_start > interval '30 minutes' "
            "AND state IN ('idle in transaction', 'active') "
            "ORDER BY xact_start ASC "
            "LIMIT 5")

        if rows:
            detail_lines = [
                f"PID {r['pid']}: {r['usename']}@{r['datname']} "
                f"持续 {r['duration']} ({r['query']})"
                for r in rows]
            g.warn("长事务阻塞 Vacuum",
                   f"{len(rows)} 个超过 30 分钟的事务可能阻塞 vacuum",
                   detail="\n".join(detail_lines))
        else:
            g.ok("长事务阻塞 Vacuum", "无长事务阻塞风险")
    except Exception:
        pass


def _warn_checkpoint_frequency(pg, g):
    """checkpoint 频率预警。"""
    try:
        row = pg.query_one(
            "SELECT checkpoints_timed, checkpoints_req, "
            "stats_reset "
            "FROM pg_stat_bgwriter")
        if row:
            total = row["checkpoints_timed"] + row["checkpoints_req"]
            if total > 0:
                req_pct = (row["checkpoints_req"] / total) * 100
                if req_pct > 70:
                    g.error("Checkpoint 频率预警",
                            f"请求触发占比 {req_pct:.0f}%，"
                            "WAL 写入过快，建议增大 max_wal_size")
                elif req_pct > 50:
                    g.warn("Checkpoint 频率预警",
                           f"请求触发占比 {req_pct:.0f}%")
                else:
                    g.ok("Checkpoint 频率", f"请求触发占比 {req_pct:.0f}%")
    except Exception:
        pass


def _warn_lock_conflicts(pg, g):
    """锁冲突预警。"""
    try:
        row = pg.query_one(
            "SELECT sum(deadlocks) AS deadlocks, "
            "sum(conflicts) AS conflicts "
            "FROM pg_stat_database")
        if row:
            deadlocks = int(row["deadlocks"] or 0)
            conflicts = int(row["conflicts"] or 0)
            if deadlocks > 100:
                g.error("锁冲突预警", f"累计 deadlock {deadlocks} 次, conflicts {conflicts} 次")
            elif deadlocks > 10 or conflicts > 1000:
                g.warn("锁冲突预警", f"deadlock {deadlocks} 次, conflicts {conflicts} 次")
            else:
                g.ok("锁冲突", f"deadlock {deadlocks} 次, conflicts {conflicts} 次")
    except Exception:
        pass


def _warn_backup_failure(pg, g):
    """备份失败预警。"""
    try:
        row = pg.query_one(
            "SELECT failed_count, last_failed_time, last_failed_wal "
            "FROM pg_stat_archiver")
        if row and row.get("failed_count") and int(row["failed_count"]) > 0:
            g.warn("归档失败预警",
                   f"累计 {row['failed_count']} 次归档失败",
                   detail=f"最后失败: {row['last_failed_wal']} ({row['last_failed_time']})")
    except Exception:
        pass


def _warn_role_anomaly(pg, g):
    """主从角色异常预警。"""
    try:
        in_recovery = pg.is_in_recovery()

        if in_recovery:
            # 从库: 检查是否能接收 WAL
            receiver = pg.query_one(
                "SELECT status FROM pg_stat_wal_receiver")
            if not receiver or receiver["status"] != "streaming":
                g.error("角色异常预警",
                        "从库 WAL receiver 非 streaming 状态，"
                        "可能已与主库断开")
        else:
            # 主库: 检查是否有期望的从库连接
            slots = pg.safe_query(
                "SELECT slot_name, active FROM pg_replication_slots",
                default=[])
            inactive_slots = [s for s in slots if not s["active"]]
            if inactive_slots:
                g.warn("角色异常预警",
                       f"{len(inactive_slots)} 个复制槽非活跃，从库可能已离线",
                       detail="\n".join(s["slot_name"] for s in inactive_slots))
    except Exception:
        pass


def _warn_xid_wraparound(pg, g):
    """XID wraparound 紧急预警 (补充 autovacuum wraparound 检查)。"""
    try:
        # 检查单个表级别的 age
        rows = pg.query(
            "SELECT n.nspname AS schema, c.relname AS table, "
            "age(c.relfrozenxid) AS xid_age "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY age(c.relfrozenxid) DESC "
            "LIMIT 5")

        if rows:
            max_age = int(rows[0]["xid_age"])
            pct = (max_age / 2147483647) * 100
            if pct > 50:
                detail = "\n".join(
                    f"{r['schema']}.{r['table']}: age={int(r['xid_age']):,}"
                    for r in rows)
                g.error("表级 XID Age",
                        f"最大 age = {max_age:,} ({pct:.1f}%)",
                        detail=detail)
            else:
                g.ok("表级 XID Age", f"最大 age = {max_age:,} ({pct:.1f}%)")
    except Exception:
        pass
