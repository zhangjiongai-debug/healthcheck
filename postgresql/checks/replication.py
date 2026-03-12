"""6.3 主从复制/高可用检查。

- 是否存在主库
- 从库是否全部在线
- replication lag 是否超阈值
- wal sender / receiver 是否正常
- failover 状态是否正常
- Patroni/repmgr/Operator 状态是否正常
- 是否出现 split brain 风险
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


# replication lag 阈值 (秒)
_LAG_WARN_SECONDS = 30
_LAG_ERROR_SECONDS = 300


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6.3 主从复制/高可用")
    pg = ctx["pg"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("数据库连接", f"无法连接: {ctx['connect_error']}")
        return g

    in_recovery = pg.is_in_recovery()

    if in_recovery:
        g.ok("当前实例", "Standby (从库)")
        _check_standby_status(pg, g)
    else:
        g.ok("当前实例", "Primary (主库)")
        _check_primary_replication(pg, g)

    # ── HA 管理器检测 ──
    _check_ha_manager(ctx, g, mode)

    return g


def _check_primary_replication(pg, g):
    """主库视角: 检查 WAL sender 和从库状态。"""
    try:
        senders = pg.query(
            "SELECT pid, application_name, client_addr, state, "
            "sync_state, sent_lsn, write_lsn, flush_lsn, replay_lsn, "
            "now() - backend_start AS uptime "
            "FROM pg_stat_replication")
    except Exception as e:
        g.warn("复制状态", f"查询 pg_stat_replication 失败: {e}")
        return

    if not senders:
        g.warn("WAL Sender", "没有连接的从库 (无复制流)")
        return

    g.ok("WAL Sender", f"检测到 {len(senders)} 个从库连接")

    for s in senders:
        name = s["application_name"] or s["client_addr"] or f"PID {s['pid']}"
        state = s["state"]

        if state != "streaming":
            g.warn(f"从库 {name}", f"状态: {state} (非 streaming)")
            continue

        # 计算 lag
        try:
            lag = pg.query_scalar(
                "SELECT CASE WHEN pg_wal_lsn_diff(sent_lsn, replay_lsn) IS NOT NULL "
                "THEN pg_wal_lsn_diff(sent_lsn, replay_lsn) ELSE 0 END "
                "FROM pg_stat_replication WHERE pid = %s",
                (s["pid"],))
            lag_bytes = int(lag or 0)
            lag_mb = lag_bytes / (1024 * 1024)
            sync = s.get("sync_state", "async")

            detail = (f"sync_state: {sync}\n"
                      f"sent_lsn: {s['sent_lsn']}\n"
                      f"replay_lsn: {s['replay_lsn']}\n"
                      f"lag: {lag_mb:.2f} MB")

            if lag_mb > 1024:
                g.error(f"从库 {name}", f"复制延迟 {lag_mb:.0f} MB", detail=detail)
            elif lag_mb > 100:
                g.warn(f"从库 {name}", f"复制延迟 {lag_mb:.1f} MB", detail=detail)
            else:
                g.ok(f"从库 {name}", f"streaming, lag {lag_mb:.1f} MB ({sync})",
                     detail=detail)
        except Exception:
            g.ok(f"从库 {name}", f"streaming ({s.get('sync_state', 'async')})")

    # 检查复制槽
    _check_replication_slots(pg, g)


def _check_standby_status(pg, g):
    """从库视角: 检查 WAL receiver 和复制延迟。"""
    # WAL receiver 状态
    try:
        receiver = pg.query_one(
            "SELECT pid, status, sender_host, sender_port, "
            "received_lsn, latest_end_lsn "
            "FROM pg_stat_wal_receiver")
        if receiver:
            if receiver["status"] == "streaming":
                g.ok("WAL Receiver", f"streaming from {receiver['sender_host']}:{receiver['sender_port']}")
            else:
                g.error("WAL Receiver", f"状态: {receiver['status']}")
        else:
            g.warn("WAL Receiver", "未检测到 WAL receiver (可能已断开)")
    except Exception as e:
        g.warn("WAL Receiver", f"查询失败: {e}")

    # 复制延迟 (时间维度)
    try:
        lag_row = pg.query_one(
            "SELECT now() - pg_last_xact_replay_timestamp() AS lag, "
            "pg_last_xact_replay_timestamp() AS last_replay")
        if lag_row and lag_row["lag"]:
            lag_seconds = lag_row["lag"].total_seconds()
            if lag_seconds > _LAG_ERROR_SECONDS:
                g.error("复制延迟 (时间)", f"{lag_seconds:.0f} 秒",
                        detail=f"最后回放: {lag_row['last_replay']}")
            elif lag_seconds > _LAG_WARN_SECONDS:
                g.warn("复制延迟 (时间)", f"{lag_seconds:.0f} 秒")
            else:
                g.ok("复制延迟 (时间)", f"{lag_seconds:.1f} 秒")
        elif lag_row and lag_row["last_replay"] is None:
            g.warn("复制延迟", "无法计算 (从未回放过事务)")
    except Exception as e:
        g.warn("复制延迟", f"查询失败: {e}")

    # 复制延迟 (字节维度)
    try:
        lag_bytes = pg.query_scalar(
            "SELECT pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn())")
        if lag_bytes is not None:
            lag_mb = int(lag_bytes) / (1024 * 1024)
            if lag_mb > 1024:
                g.error("复制延迟 (字节)", f"{lag_mb:.0f} MB 待回放")
            elif lag_mb > 100:
                g.warn("复制延迟 (字节)", f"{lag_mb:.1f} MB 待回放")
            else:
                g.ok("复制延迟 (字节)", f"{lag_mb:.1f} MB 待回放")
    except Exception:
        pass


def _check_replication_slots(pg, g):
    """检查复制槽状态，特别关注非活跃槽导致的 WAL 堆积。"""
    try:
        slots = pg.query(
            "SELECT slot_name, slot_type, active, "
            "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes "
            "FROM pg_replication_slots")
    except Exception:
        return

    if not slots:
        return

    inactive_slots = []
    for s in slots:
        if not s["active"]:
            retained_mb = (int(s["retained_bytes"] or 0)) / (1024 * 1024)
            inactive_slots.append(f"{s['slot_name']} ({s['slot_type']}): "
                                  f"已保留 {retained_mb:.0f} MB WAL")

    if inactive_slots:
        g.warn("复制槽", f"{len(inactive_slots)} 个非活跃复制槽 (可能导致 WAL 堆积)",
               detail="\n".join(inactive_slots))
    else:
        g.ok("复制槽", f"{len(slots)} 个复制槽, 全部活跃")


def _check_ha_manager(ctx: dict, g, mode):
    """检测 Patroni/repmgr/Operator 等 HA 管理器状态。"""
    pg = ctx["pg"]

    # 通过 K8s 标签检测 Operator 类型
    if mode == DeployMode.K8S:
        k8s_core = ctx.get("k8s_core")
        if k8s_core:
            _check_k8s_operator(ctx, g)
            return

    # 尝试检测 Patroni
    try:
        result = subprocess.run(
            ["patronictl", "list", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            import json
            members = json.loads(result.stdout)
            leaders = [m for m in members if m.get("Role") == "Leader"]
            replicas = [m for m in members if m.get("Role") in ("Replica", "Sync Standby")]

            if len(leaders) == 1:
                g.ok("Patroni Leader", f"{leaders[0].get('Member', 'unknown')}")
            elif len(leaders) == 0:
                g.fatal("Patroni Leader", "没有 Leader! 可能存在 split brain 风险")
            else:
                g.fatal("Patroni Leader",
                        f"检测到 {len(leaders)} 个 Leader! Split brain 风险!",
                        detail="\n".join(l.get("Member", "") for l in leaders))

            for r in replicas:
                lag = r.get("Lag in MB", 0)
                state = r.get("State", "unknown")
                name = r.get("Member", "unknown")
                if state == "running":
                    g.ok(f"Patroni 副本 {name}", f"running, lag {lag} MB")
                else:
                    g.warn(f"Patroni 副本 {name}", f"状态: {state}")
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # 尝试检测 repmgr
    try:
        result = subprocess.run(
            ["repmgr", "cluster", "show", "--compact"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            g.ok("repmgr", "集群状态正常", detail=result.stdout.strip()[:500])
            return
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # 检查是否有 pg_stat_replication 中的连接
    try:
        count = pg.query_scalar("SELECT count(*) FROM pg_stat_replication")
        if count and int(count) > 0:
            g.ok("HA 管理器", f"未检测到 Patroni/repmgr，但存在 {count} 个复制连接")
        else:
            in_recovery = pg.is_in_recovery()
            if in_recovery:
                g.ok("HA 管理器", "当前为从库，未检测到额外 HA 管理器")
            else:
                g.warn("HA 管理器", "未检测到 HA 管理器和复制连接 (单节点模式)")
    except Exception:
        pass


def _check_k8s_operator(ctx: dict, g):
    """在 K8s 环境中检测 PostgreSQL Operator 状态。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
        if not pods.items:
            return

        # 检查常见 Operator 标签
        labels = pods.items[0].metadata.labels or {}
        operator = None
        if "cluster-name" in labels and "application" in labels:
            if labels.get("application") == "spilo":
                operator = "Zalando Postgres Operator"
        elif "postgresql" in labels.get("app.kubernetes.io/managed-by", "").lower():
            operator = "CloudNativePG"
        elif "cnpg.io/cluster" in labels:
            operator = "CloudNativePG"
        elif "postgres-operator.crunchydata.com" in str(labels):
            operator = "CrunchyData PGO"

        if operator:
            g.ok("K8s Operator", f"检测到 {operator}")

            # 检查 Pod 角色分布
            roles = {}
            for pod in pods.items:
                pod_labels = pod.metadata.labels or {}
                role = (pod_labels.get("role") or
                        pod_labels.get("cnpg.io/instanceRole") or
                        pod_labels.get("spilo-role") or
                        "unknown")
                roles.setdefault(role, []).append(pod.metadata.name)

            detail_lines = []
            for role, names in roles.items():
                detail_lines.append(f"{role}: {', '.join(names)}")
            g.ok("Pod 角色分布", f"{len(pods.items)} 个实例",
                 detail="\n".join(detail_lines))

            # 检查 split brain: 多个 master/primary
            primary_count = len(roles.get("master", []) + roles.get("primary", []))
            if primary_count > 1:
                g.fatal("Split Brain 风险",
                        f"检测到 {primary_count} 个 primary Pod!")
            elif primary_count == 0:
                g.error("Primary Pod", "未检测到 primary Pod")
        else:
            g.ok("K8s Operator", "未检测到已知 Operator (可能为自管理部署)")
    except Exception as e:
        g.warn("K8s Operator", f"检测失败: {e}")
