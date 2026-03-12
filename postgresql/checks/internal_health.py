"""6.5 数据库内部健康检查。

- 数据库是否能执行简单 SQL
- 核心表是否可查询
- 锁等待是否严重
- deadlock 是否频繁
- 长事务是否存在
- autovacuum 是否正常
- bloating 是否严重
- 是否存在 invalid index
- 是否有 failed transaction 持续积累
"""

from ..result import CheckGroup


# 长事务阈值 (秒)
_LONG_TX_WARN_SECONDS = 600       # 10 分钟
_LONG_TX_ERROR_SECONDS = 3600     # 1 小时

# bloat 阈值
_BLOAT_WARN_RATIO = 0.3    # dead tuple > 30%
_BLOAT_WARN_MIN_SIZE = 50  # MB, 仅对大表告警


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6.5 数据库内部健康")
    pg = ctx["pg"]

    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 简单 SQL 执行 ──
    try:
        result = pg.query_scalar("SELECT 1")
        if result == 1:
            g.ok("SQL 执行", "SELECT 1 成功")
        else:
            g.error("SQL 执行", f"SELECT 1 返回: {result}")
    except Exception as e:
        g.fatal("SQL 执行", f"SELECT 1 失败: {e}")
        return g

    # ── 核心系统表可查 ──
    _check_system_tables(pg, g)

    # ── 锁等待 ──
    _check_locks(pg, g)

    # ── Deadlock ──
    _check_deadlocks(pg, g)

    # ── 长事务 ──
    _check_long_transactions(pg, g)

    # ── Autovacuum ──
    _check_autovacuum(pg, g)

    # ── Table bloat ──
    _check_bloat(pg, g)

    # ── Invalid index ──
    _check_invalid_indexes(pg, g)

    # ── Failed/aborted transactions ──
    _check_transaction_stats(pg, g)

    return g


def _check_system_tables(pg, g):
    """检查核心系统表是否可查询。"""
    tables = [
        ("pg_stat_activity", "SELECT count(*) FROM pg_stat_activity"),
        ("pg_stat_user_tables", "SELECT count(*) FROM pg_stat_user_tables"),
        ("pg_stat_bgwriter", "SELECT count(*) FROM pg_stat_bgwriter"),
    ]
    for name, sql in tables:
        try:
            pg.query_scalar(sql)
            g.ok(f"系统表 {name}", "可正常查询")
        except Exception as e:
            g.error(f"系统表 {name}", f"查询失败: {e}")


def _check_locks(pg, g):
    """检查锁等待情况。"""
    try:
        # 等待中的锁
        waiting = pg.query(
            "SELECT bl.pid AS blocked_pid, "
            "ba.usename AS blocked_user, "
            "ba.query AS blocked_query, "
            "kl.pid AS blocking_pid, "
            "ka.usename AS blocking_user, "
            "now() - ba.query_start AS wait_duration "
            "FROM pg_locks bl "
            "JOIN pg_stat_activity ba ON ba.pid = bl.pid "
            "JOIN pg_locks kl ON kl.transactionid = bl.transactionid AND kl.pid != bl.pid "
            "JOIN pg_stat_activity ka ON ka.pid = kl.pid "
            "WHERE NOT bl.granted "
            "ORDER BY ba.query_start "
            "LIMIT 20")

        if not waiting:
            g.ok("锁等待", "无锁等待")
            return

        long_waits = [w for w in waiting
                      if w["wait_duration"] and w["wait_duration"].total_seconds() > 30]

        detail_lines = []
        for w in waiting[:10]:
            query_preview = (w["blocked_query"] or "")[:80]
            detail_lines.append(
                f"PID {w['blocked_pid']} ({w['blocked_user']}) "
                f"被 PID {w['blocking_pid']} ({w['blocking_user']}) 阻塞 "
                f"{w['wait_duration']}\n  Query: {query_preview}")

        if long_waits:
            g.error("锁等待", f"{len(waiting)} 个锁等待, {len(long_waits)} 个超过 30 秒",
                    detail="\n".join(detail_lines))
        else:
            g.warn("锁等待", f"{len(waiting)} 个锁等待 (均短暂)",
                   detail="\n".join(detail_lines))
    except Exception as e:
        # 简化查询作为降级
        try:
            count = pg.query_scalar(
                "SELECT count(*) FROM pg_locks WHERE NOT granted")
            if count and int(count) > 0:
                g.warn("锁等待", f"{count} 个锁等待中")
            else:
                g.ok("锁等待", "无锁等待")
        except Exception:
            g.warn("锁等待", f"检查失败: {e}")


def _check_deadlocks(pg, g):
    """检查 deadlock 计数。"""
    try:
        rows = pg.query(
            "SELECT datname, deadlocks, stats_reset "
            "FROM pg_stat_database "
            "WHERE datname NOT LIKE 'template%' AND deadlocks > 0 "
            "ORDER BY deadlocks DESC")

        if not rows:
            g.ok("Deadlock", "所有数据库均无 deadlock 记录")
            return

        total_deadlocks = sum(r["deadlocks"] for r in rows)
        detail_lines = [f"{r['datname']}: {r['deadlocks']} (since {r['stats_reset']})"
                        for r in rows[:5]]

        if total_deadlocks > 100:
            g.error("Deadlock", f"累计 {total_deadlocks} 次",
                    detail="\n".join(detail_lines))
        elif total_deadlocks > 10:
            g.warn("Deadlock", f"累计 {total_deadlocks} 次",
                   detail="\n".join(detail_lines))
        else:
            g.ok("Deadlock", f"累计 {total_deadlocks} 次 (少量)",
                 detail="\n".join(detail_lines))
    except Exception as e:
        g.warn("Deadlock", f"检查失败: {e}")


def _check_long_transactions(pg, g):
    """检查长事务。"""
    try:
        rows = pg.query(
            "SELECT pid, usename, datname, state, "
            "now() - xact_start AS tx_duration, "
            "left(query, 100) AS query "
            "FROM pg_stat_activity "
            "WHERE xact_start IS NOT NULL "
            "AND state != 'idle' "
            "AND now() - xact_start > interval '10 minutes' "
            "ORDER BY xact_start ASC")

        if not rows:
            g.ok("长事务", "无超过 10 分钟的活跃事务")
            return

        error_rows = [r for r in rows
                      if r["tx_duration"].total_seconds() > _LONG_TX_ERROR_SECONDS]
        detail_lines = []
        for r in rows[:10]:
            detail_lines.append(
                f"PID {r['pid']}: {r['usename']}@{r['datname']} "
                f"持续 {r['tx_duration']} ({r['state']})\n"
                f"  Query: {r['query']}")

        if error_rows:
            g.error("长事务", f"{len(rows)} 个长事务, {len(error_rows)} 个超过 1 小时",
                    detail="\n".join(detail_lines))
        else:
            g.warn("长事务", f"{len(rows)} 个超过 10 分钟的事务",
                   detail="\n".join(detail_lines))
    except Exception as e:
        g.warn("长事务", f"检查失败: {e}")


def _check_autovacuum(pg, g):
    """检查 autovacuum 状态。"""
    # autovacuum 是否启用
    try:
        av_on = pg.query_scalar("SHOW autovacuum")
        if av_on != "on":
            g.error("Autovacuum", f"已关闭 (autovacuum={av_on})")
            return
    except Exception:
        pass

    # 当前正在运行的 autovacuum
    try:
        running = pg.query(
            "SELECT pid, datname, query, now() - query_start AS duration "
            "FROM pg_stat_activity "
            "WHERE query LIKE 'autovacuum:%' "
            "ORDER BY query_start ASC")

        if running:
            long_running = [r for r in running
                            if r["duration"].total_seconds() > 3600]
            if long_running:
                detail = "\n".join(
                    f"PID {r['pid']}: {r['datname']} 运行 {r['duration']}"
                    for r in long_running)
                g.warn("Autovacuum 运行中",
                       f"{len(running)} 个, {len(long_running)} 个超过 1 小时",
                       detail=detail)
            else:
                g.ok("Autovacuum 运行中", f"{len(running)} 个正在执行")
        else:
            g.ok("Autovacuum", "当前无活跃 autovacuum 进程")
    except Exception:
        pass

    # 长时间未被 vacuum 的表
    try:
        rows = pg.query(
            "SELECT schemaname, relname, "
            "n_dead_tup, n_live_tup, "
            "last_autovacuum, last_vacuum, "
            "now() - COALESCE(last_autovacuum, last_vacuum) AS since_vacuum "
            "FROM pg_stat_user_tables "
            "WHERE n_dead_tup > 10000 "
            "AND (last_autovacuum IS NULL AND last_vacuum IS NULL "
            "     OR COALESCE(last_autovacuum, last_vacuum) < now() - interval '7 days') "
            "ORDER BY n_dead_tup DESC "
            "LIMIT 10")

        if rows:
            detail_lines = [
                f"{r['schemaname']}.{r['relname']}: "
                f"dead={r['n_dead_tup']}, live={r['n_live_tup']}, "
                f"last_vacuum={r.get('last_autovacuum') or r.get('last_vacuum') or 'never'}"
                for r in rows]
            g.warn("Autovacuum 落后",
                   f"{len(rows)} 个表超过 7 天未 vacuum 且有大量 dead tuple",
                   detail="\n".join(detail_lines))
        else:
            g.ok("Autovacuum 及时性", "所有表 vacuum 状态正常")
    except Exception as e:
        g.warn("Autovacuum 检查", f"查询失败: {e}")


def _check_bloat(pg, g):
    """检查表膨胀 (dead tuple 比例)。"""
    try:
        rows = pg.query(
            "SELECT schemaname, relname, "
            "n_live_tup, n_dead_tup, "
            "pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(relname)) AS total_bytes "
            "FROM pg_stat_user_tables "
            "WHERE n_live_tup + n_dead_tup > 0 "
            "ORDER BY n_dead_tup DESC "
            "LIMIT 20")

        bloated = []
        for r in rows:
            total_tup = r["n_live_tup"] + r["n_dead_tup"]
            if total_tup == 0:
                continue
            dead_ratio = r["n_dead_tup"] / total_tup
            size_mb = r["total_bytes"] / (1024**2)
            if dead_ratio > _BLOAT_WARN_RATIO and size_mb > _BLOAT_WARN_MIN_SIZE:
                bloated.append(
                    f"{r['schemaname']}.{r['relname']}: "
                    f"dead={r['n_dead_tup']} ({dead_ratio:.0%}), "
                    f"size={size_mb:.0f} MB")

        if bloated:
            g.warn("表膨胀", f"{len(bloated)} 个大表 dead tuple 占比超过 30%",
                   detail="\n".join(bloated[:10]))
        else:
            g.ok("表膨胀", "无严重膨胀的大表")
    except Exception as e:
        g.warn("表膨胀", f"检查失败: {e}")


def _check_invalid_indexes(pg, g):
    """检查无效索引。"""
    try:
        rows = pg.query(
            "SELECT schemaname, tablename, indexname "
            "FROM pg_indexes i "
            "JOIN pg_index idx ON idx.indexrelid = "
            "  (quote_ident(i.schemaname)||'.'||quote_ident(i.indexname))::regclass "
            "WHERE NOT idx.indisvalid")

        if rows:
            detail_lines = [f"{r['schemaname']}.{r['indexname']} on {r['tablename']}"
                            for r in rows]
            g.error("无效索引", f"发现 {len(rows)} 个无效索引",
                    detail="\n".join(detail_lines))
        else:
            g.ok("无效索引", "无无效索引")
    except Exception:
        # 降级: 使用更简单的查询
        try:
            count = pg.query_scalar(
                "SELECT count(*) FROM pg_index WHERE NOT indisvalid")
            if count and int(count) > 0:
                g.error("无效索引", f"发现 {count} 个无效索引")
            else:
                g.ok("无效索引", "无无效索引")
        except Exception as e:
            g.warn("无效索引", f"检查失败: {e}")


def _check_transaction_stats(pg, g):
    """检查事务统计 (提交/回滚比率)。"""
    try:
        rows = pg.query(
            "SELECT datname, xact_commit, xact_rollback, "
            "stats_reset "
            "FROM pg_stat_database "
            "WHERE datname NOT LIKE 'template%' "
            "AND xact_commit + xact_rollback > 0 "
            "ORDER BY xact_rollback DESC")

        high_rollback = []
        for r in rows:
            total = r["xact_commit"] + r["xact_rollback"]
            if total == 0:
                continue
            rollback_pct = (r["xact_rollback"] / total) * 100
            if rollback_pct > 10 and r["xact_rollback"] > 1000:
                high_rollback.append(
                    f"{r['datname']}: rollback {rollback_pct:.1f}% "
                    f"({r['xact_rollback']}/{total})")

        if high_rollback:
            g.warn("事务回滚率", f"{len(high_rollback)} 个数据库回滚率超过 10%",
                   detail="\n".join(high_rollback))
        else:
            g.ok("事务回滚率", "所有数据库回滚率在正常范围")
    except Exception as e:
        g.warn("事务统计", f"检查失败: {e}")
