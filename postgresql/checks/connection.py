"""6.2 连接与认证检查。

- 数据库端口是否可达
- 用户认证是否成功
- 关键业务库连接是否正常
- 连接数是否接近上限
- 是否存在大量 idle in transaction
- 连接池 (PgBouncer) 是否正常
"""

import socket

from ..result import CheckGroup


def check(ctx: dict, check_databases: list[str] = None) -> CheckGroup:
    g = CheckGroup("6.2 连接与认证")
    pg = ctx["pg"]

    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 端口可达性 ──
    try:
        sock = socket.create_connection((pg.host, pg.port), timeout=5)
        sock.close()
        g.ok("端口可达", f"{pg.host}:{pg.port} 可连接")
    except Exception as e:
        g.error("端口可达", f"{pg.host}:{pg.port} 不可达: {e}")

    # ── 认证验证 (连接成功即认证通过) ──
    g.ok("用户认证", f"用户 {pg.user} 认证成功")

    # ── 关键业务库连接检查 ──
    if check_databases:
        _check_business_databases(pg, g, check_databases)

    # ── 连接数 ──
    _check_connections(pg, g)

    # ── idle in transaction ──
    _check_idle_in_transaction(pg, g)

    # ── PgBouncer 检测 ──
    _check_pgbouncer(pg, g)

    return g


def _check_business_databases(pg, g, databases: list[str]):
    """检查关键业务库是否可连接。"""
    existing = pg.safe_query(
        "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate",
        default=[])
    existing_names = {r["datname"] for r in existing}

    for db in databases:
        if db in existing_names:
            g.ok(f"业务库 {db}", "存在且允许连接")
        else:
            g.error(f"业务库 {db}", "数据库不存在或不允许连接")


def _check_connections(pg, g):
    """检查当前连接数与上限。"""
    try:
        max_conn = int(pg.query_scalar("SHOW max_connections") or 100)
        superuser_reserved = int(pg.query_scalar(
            "SHOW superuser_reserved_connections") or 3)

        row = pg.query_one(
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE state = 'active') AS active, "
            "count(*) FILTER (WHERE state = 'idle') AS idle, "
            "count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_tx "
            "FROM pg_stat_activity WHERE backend_type = 'client backend'")

        if not row:
            g.warn("连接数", "无法获取连接统计")
            return

        total = row["total"]
        active = row["active"]
        idle = row["idle"]
        available = max_conn - superuser_reserved
        usage_pct = (total / available * 100) if available > 0 else 0

        detail = (f"当前连接: {total} (活跃: {active}, 空闲: {idle})\n"
                  f"最大连接: {max_conn} (保留: {superuser_reserved}, 可用: {available})")

        if usage_pct > 90:
            g.error("连接数", f"使用率 {usage_pct:.0f}% ({total}/{available})", detail=detail)
        elif usage_pct > 75:
            g.warn("连接数", f"使用率 {usage_pct:.0f}% ({total}/{available})", detail=detail)
        else:
            g.ok("连接数", f"使用率 {usage_pct:.0f}% ({total}/{available})", detail=detail)
    except Exception as e:
        g.error("连接数", f"检查失败: {e}")


def _check_idle_in_transaction(pg, g):
    """检查 idle in transaction 连接。"""
    try:
        rows = pg.query(
            "SELECT pid, usename, datname, state, "
            "now() - state_change AS duration "
            "FROM pg_stat_activity "
            "WHERE state = 'idle in transaction' "
            "ORDER BY state_change ASC")

        if not rows:
            g.ok("Idle in Transaction", "无 idle in transaction 连接")
            return

        # 超过 5 分钟的
        long_idle = [r for r in rows
                     if r["duration"] and r["duration"].total_seconds() > 300]

        if long_idle:
            detail_lines = []
            for r in long_idle[:10]:
                detail_lines.append(
                    f"PID {r['pid']}: {r['usename']}@{r['datname']} "
                    f"持续 {r['duration']}")
            g.error("Idle in Transaction",
                    f"{len(rows)} 个连接, 其中 {len(long_idle)} 个超过 5 分钟",
                    detail="\n".join(detail_lines))
        elif len(rows) > 10:
            g.warn("Idle in Transaction", f"{len(rows)} 个 idle in transaction 连接")
        else:
            g.ok("Idle in Transaction", f"{len(rows)} 个 (均在正常范围)")
    except Exception as e:
        g.warn("Idle in Transaction", f"检查失败: {e}")


def _check_pgbouncer(pg, g):
    """检测是否使用 PgBouncer (通过常见端口或 pg_stat_activity 特征)。"""
    try:
        # 检查是否有 pgbouncer 相关进程 (通过 pg_stat_activity)
        rows = pg.safe_query(
            "SELECT count(*) AS cnt FROM pg_stat_activity "
            "WHERE application_name ILIKE '%pgbouncer%'",
            default=[])
        if rows and rows[0]["cnt"] > 0:
            g.ok("PgBouncer", f"检测到 {rows[0]['cnt']} 个 PgBouncer 连接")
        else:
            g.ok("PgBouncer", "未检测到 PgBouncer (直连模式)")
    except Exception:
        pass
