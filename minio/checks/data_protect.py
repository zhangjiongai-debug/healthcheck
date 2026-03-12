"""5.5 数据保护与后台任务检查。

- versioning 是否符合预期
- lifecycle policy 是否正常
- replication 是否正常
- ILM/过期清理是否正常
- healing/self-heal 任务是否异常
- 后台扫描是否报错
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5.5 数据保护与后台任务")
    mc = ctx["mc"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    if not mc.access_key:
        g.warn("认证信息", "未提供 access_key，跳过数据保护检查")
        return g

    # ── Versioning 检查 ──
    _check_versioning(mc, g)

    # ── Lifecycle 检查 ──
    _check_lifecycle(mc, g)

    # ── Replication 检查 ──
    _check_replication(mc, g)

    # ── Healing 任务 ──
    _check_healing(mc, g)

    # ── 后台扫描 (Scanner) ──
    _check_scanner(mc, g)

    return g


def _check_versioning(mc, g):
    """检查 bucket versioning 状态。"""
    buckets = mc.list_buckets_sdk()
    if buckets is None:
        return

    if not mc.mc_available():
        return

    versioned = []
    unversioned = []

    for bucket in buckets[:10]:  # 限制检查数量
        output = mc.mc_command(
            ["version", "info", f"_healthcheck/{bucket}"], timeout=10)
        if output:
            if "enabled" in output.lower():
                versioned.append(bucket)
            elif "suspended" in output.lower():
                unversioned.append(bucket)
            else:
                unversioned.append(bucket)

    if versioned:
        g.ok("Versioning", f"{len(versioned)} 个 bucket 已启用版本控制",
             detail=", ".join(versioned[:10]))
    if unversioned:
        g.ok("Versioning (未启用)", f"{len(unversioned)} 个 bucket 未启用版本控制",
             detail=", ".join(unversioned[:10]))


def _check_lifecycle(mc, g):
    """检查 lifecycle 规则。"""
    buckets = mc.list_buckets_sdk()
    if buckets is None or not mc.mc_available():
        return

    lifecycle_count = 0
    for bucket in buckets[:10]:
        output = mc.mc_command(
            ["ilm", "rule", "list", f"_healthcheck/{bucket}"], timeout=10)
        if output and "No lifecycle" not in output and output.strip():
            lifecycle_count += 1

    if lifecycle_count > 0:
        g.ok("Lifecycle 规则", f"{lifecycle_count} 个 bucket 配置了生命周期规则")
    else:
        g.ok("Lifecycle 规则", "无 bucket 配置生命周期规则")


def _check_replication(mc, g):
    """检查 bucket replication 状态。"""
    if not mc.mc_available():
        return

    output = mc.mc_command(
        ["admin", "replicate", "status", "_healthcheck"], timeout=15)
    if output is None:
        # 可能是单节点，无 site replication
        return

    if "SiteReplication is not enabled" in output:
        g.ok("站点复制", "未启用 (单站点模式)")
    elif "error" in output.lower():
        g.error("站点复制", "复制状态异常",
                detail=output.strip()[:500])
    else:
        g.ok("站点复制", "已启用",
             detail=output.strip()[:300])


def _check_healing(mc, g):
    """检查 healing 任务状态。"""
    if not mc.mc_available():
        return

    output = mc.mc_command(
        ["admin", "heal", "--json", "_healthcheck"], timeout=15)
    if output is None:
        return

    import json
    try:
        data = json.loads(output)
        items_healed = data.get("itemsHealed", 0)
        items_failed = data.get("itemsFailed", 0)
        bytes_done = data.get("bytesScanned", 0)

        if items_failed > 0:
            g.warn("Self-Heal", f"修复失败 {items_failed} 项",
                   detail=f"已修复: {items_healed}, 已扫描: {bytes_done/(1024**2):.1f} MB")
        elif items_healed > 0:
            g.ok("Self-Heal", f"已修复 {items_healed} 项")
    except json.JSONDecodeError:
        # 非 JSON 输出，可能没有 healing 任务
        if "no" in output.lower() and "heal" in output.lower():
            g.ok("Self-Heal", "无活跃的修复任务")


def _check_scanner(mc, g):
    """通过 metrics 检查后台扫描状态。"""
    resp = mc.metrics_cluster()
    if resp["status"] != 200 or not isinstance(resp["body"], str):
        return

    body = resp["body"]

    # 检查 scanner 相关 metrics
    for line in body.splitlines():
        if line.startswith("#"):
            continue

        # 修复队列
        if "minio_heal_objects_error_total" in line:
            try:
                val = float(line.split()[-1])
                if val > 0:
                    g.warn("后台修复错误", f"累计 {int(val)} 个对象修复失败")
            except (ValueError, IndexError):
                pass

        # Scanner 速度
        if "minio_scanner_objects_scanned" in line:
            try:
                val = float(line.split()[-1])
                if val > 0:
                    g.ok("后台扫描", f"已扫描 {int(val)} 个对象")
            except (ValueError, IndexError):
                pass
