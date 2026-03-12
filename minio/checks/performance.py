"""5.6 MinIO 性能与告警检查。

- PUT/GET 延迟是否异常
- 5xx 错误率是否升高
- network throughput 是否异常
- 磁盘使用率接近阈值
- 某节点离线导致降级
- quorum 即将不足
- bucket 数量或对象数过大导致压力
"""

import re

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5.6 MinIO 性能与告警")
    mc = ctx["mc"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── Prometheus metrics 分析 ──
    _check_metrics_performance(mc, g)

    # ── mc admin info 汇总 ──
    _check_admin_summary(mc, g)

    # ── quorum 风险 ──
    _check_quorum_risk(mc, g)

    return g


def _check_metrics_performance(mc, g):
    """从 Prometheus metrics 分析性能指标。"""
    resp = mc.metrics_cluster()
    if resp["status"] != 200 or not isinstance(resp["body"], str):
        g.warn("Prometheus Metrics", "无法获取 metrics 数据")
        return

    body = resp["body"]
    metrics = _parse_metrics(body)

    # ── 5xx 错误 ──
    errors_5xx = 0
    total_requests = 0
    for key, val in metrics.items():
        if "minio_s3_requests_errors_total" in key:
            errors_5xx += val
        if "minio_s3_requests_total" in key:
            total_requests += val

    if total_requests > 0:
        error_rate = (errors_5xx / total_requests) * 100
        if error_rate > 5:
            g.error("5xx 错误率", f"{error_rate:.2f}% ({int(errors_5xx)}/{int(total_requests)})")
        elif error_rate > 1:
            g.warn("5xx 错误率", f"{error_rate:.2f}% ({int(errors_5xx)}/{int(total_requests)})")
        else:
            g.ok("5xx 错误率", f"{error_rate:.2f}%")
    elif errors_5xx > 0:
        g.warn("5xx 错误", f"累计 {int(errors_5xx)} 个错误")

    # ── 请求延迟 (TTFB) ──
    ttfb_sum = 0
    ttfb_count = 0
    for key, val in metrics.items():
        if "minio_s3_requests_ttfb_seconds_distribution_sum" in key:
            ttfb_sum += val
        if "minio_s3_requests_ttfb_seconds_distribution_count" in key:
            ttfb_count += val

    if ttfb_count > 0:
        avg_ttfb = ttfb_sum / ttfb_count
        if avg_ttfb > 1.0:
            g.error("平均响应延迟", f"{avg_ttfb*1000:.0f}ms")
        elif avg_ttfb > 0.5:
            g.warn("平均响应延迟", f"{avg_ttfb*1000:.0f}ms")
        else:
            g.ok("平均响应延迟", f"{avg_ttfb*1000:.1f}ms")

    # ── 网络吞吐 ──
    rx_bytes = 0
    tx_bytes = 0
    for key, val in metrics.items():
        if "minio_s3_traffic_received_bytes" in key:
            rx_bytes += val
        if "minio_s3_traffic_sent_bytes" in key:
            tx_bytes += val

    if rx_bytes > 0 or tx_bytes > 0:
        rx_gb = rx_bytes / (1024 ** 3)
        tx_gb = tx_bytes / (1024 ** 3)
        g.ok("网络吞吐统计", f"接收 {rx_gb:.2f} GB, 发送 {tx_gb:.2f} GB")

    # ── 磁盘使用率 (从 metrics) ──
    total_space = 0
    used_space = 0
    for key, val in metrics.items():
        if "minio_cluster_capacity_raw_total_bytes" in key:
            total_space += val
        if "minio_cluster_capacity_raw_free_bytes" in key:
            total_space = total_space  # 保留
            used_space = total_space - val if total_space > val else 0

    # 通过 used/free 计算
    free_space = 0
    for key, val in metrics.items():
        if "minio_cluster_capacity_raw_free_bytes" in key:
            free_space += val

    if total_space > 0 and free_space >= 0:
        used = total_space - free_space
        pct = (used / total_space) * 100 if total_space > 0 else 0
        if pct > 90:
            g.fatal("集群磁盘使用率", f"{pct:.1f}% — 即将耗尽!")
        elif pct > 80:
            g.error("集群磁盘使用率", f"{pct:.1f}%")
        elif pct > 70:
            g.warn("集群磁盘使用率", f"{pct:.1f}%")
        else:
            g.ok("集群磁盘使用率", f"{pct:.1f}%")

    # ── 对象数量 ──
    for key, val in metrics.items():
        if "minio_cluster_objects_size_distribution" not in key and \
           "minio_bucket_usage_total_bytes" not in key and \
           "minio_cluster_bucket_total" in key:
            g.ok("Bucket 总数", str(int(val)))
        if "minio_cluster_objects_total" in key and \
           "distribution" not in key:
            if val > 100_000_000:
                g.warn("对象总数", f"{val/1_000_000:.0f}M — 对象数量很大，可能影响性能")
            else:
                g.ok("对象总数", f"{int(val):,}")


def _check_admin_summary(mc, g):
    """通过 mc admin info 获取汇总信息。"""
    info = mc.mc_admin_info()
    if not info:
        return

    servers = info.get("info", {}).get("servers", info.get("servers", []))
    if not servers:
        return

    # 检查是否有节点降级
    degraded = []
    for s in servers:
        endpoint = s.get("endpoint", "unknown")
        disks = s.get("disks", [])
        offline_disks = [d for d in disks if d.get("state") != "ok"]
        if offline_disks:
            degraded.append(f"{endpoint}: {len(offline_disks)} 盘离线")

    if degraded:
        g.warn("节点降级", f"{len(degraded)} 个节点有磁盘离线",
               detail="\n".join(degraded))


def _check_quorum_risk(mc, g):
    """检查 quorum 风险。"""
    info = mc.mc_admin_info()
    if not info:
        return

    servers = info.get("info", {}).get("servers", info.get("servers", []))
    if not servers:
        return

    total_disks = 0
    online_disks = 0
    for s in servers:
        for d in s.get("disks", []):
            total_disks += 1
            if d.get("state") == "ok":
                online_disks += 1

    if total_disks <= 1:
        # 单盘/单节点模式
        if online_disks == 0:
            g.fatal("Quorum 风险", "磁盘离线，服务不可用")
        else:
            g.warn("Quorum 风险", "单盘模式，无纠删码保护，任何磁盘故障将导致数据丢失")
        return

    # 纠删码模式: 至少需要 total/2 + 1 个磁盘来写入
    write_quorum = total_disks // 2 + 1
    read_quorum = total_disks // 2  # 读只需 N/2

    spare_write = online_disks - write_quorum
    spare_read = online_disks - read_quorum

    if spare_write < 0:
        g.fatal("Quorum 风险",
                f"写入不可用! 在线 {online_disks}/{total_disks}, "
                f"需要至少 {write_quorum} 个磁盘")
    elif spare_write == 0:
        g.error("Quorum 风险",
                f"写入 quorum 临界! 在线 {online_disks}/{total_disks}, "
                "再损失 1 个磁盘将无法写入")
    elif spare_write <= 1:
        g.warn("Quorum 风险",
               f"在线 {online_disks}/{total_disks}, "
               f"写入余量仅 {spare_write} 个磁盘")
    else:
        g.ok("Quorum 状态",
             f"在线 {online_disks}/{total_disks}, "
             f"写入余量 {spare_write}, 读取余量 {spare_read}")


def _parse_metrics(body: str) -> dict:
    """解析 Prometheus text 格式为 {metric_line: value}。"""
    metrics = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            try:
                metrics[parts[0]] = float(parts[1])
            except ValueError:
                pass
    return metrics
