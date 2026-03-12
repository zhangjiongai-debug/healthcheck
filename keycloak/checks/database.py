"""4.2 Keycloak 数据库连接状态检查。

- Keycloak 到 PostgreSQL 是否连通
- 数据库连接池是否耗尽
- 是否存在连接失败、超时
- migration/schema 检查是否正常
- 启动日志中是否有 DB 初始化失败
"""

import re

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4.2 数据库连接状态")
    kc = ctx["kc"]
    mode = ctx["mode"]

    # ── 通过 health 端点检查 DB 连通性 ──
    resp = kc.health_ready()
    if resp["status"] in (200, 503) and isinstance(resp["body"], dict):
        checks = resp["body"].get("checks", [])
        db_check = None
        for c in checks:
            name_lower = c.get("name", "").lower()
            if "database" in name_lower or "datasource" in name_lower:
                db_check = c
                break
        if db_check:
            if db_check.get("status") == "UP":
                g.ok("数据库健康检查", "UP")
            else:
                g.error("数据库健康检查", f"状态: {db_check.get('status', 'unknown')}",
                        detail=_format_data(db_check.get("data", {})))
        else:
            g.warn("数据库健康检查", "health 端点中未找到数据库检查项")
    elif resp["status"] == 0:
        g.fatal("数据库健康检查", f"无法访问 health 端点: {resp['body']}")
    else:
        g.warn("数据库健康检查", f"health 端点返回 HTTP {resp['status']}")

    # ── 通过 metrics 检查连接池 ──
    _check_db_pool_metrics(kc, g)

    # ── 通过日志检查 DB 问题 ──
    if mode == DeployMode.K8S:
        _check_k8s_logs(ctx, g)
    elif mode == DeployMode.DOCKER:
        _check_docker_logs(ctx, g)

    return g


def _check_db_pool_metrics(kc, g: CheckGroup):
    """从 Prometheus metrics 中提取连接池指标。"""
    resp = kc.metrics()
    if resp["status"] != 200 or not isinstance(resp["body"], str):
        g.warn("连接池 Metrics", "无法获取 /metrics 端点数据")
        return

    body = resp["body"]

    # Agroal 连接池指标 (Quarkus/Keycloak 默认)
    active = _extract_metric(body, r'agroal_active_count\s+(\d+\.?\d*)')
    available = _extract_metric(body, r'agroal_available_count\s+(\d+\.?\d*)')
    max_size = _extract_metric(body, r'agroal_max_used_count\s+(\d+\.?\d*)')
    awaiting = _extract_metric(body, r'agroal_awaiting_count\s+(\d+\.?\d*)')

    # 也尝试 vendor_ 前缀的 (旧版 Keycloak)
    if active is None:
        active = _extract_metric(body, r'vendor_agroal_active_count\s+(\d+\.?\d*)')
        available = _extract_metric(body, r'vendor_agroal_available_count\s+(\d+\.?\d*)')

    if active is not None and available is not None:
        total = active + available
        if total > 0:
            usage_pct = (active / total) * 100
            if usage_pct > 90:
                g.error("连接池使用率", f"{usage_pct:.0f}% ({active:.0f}/{total:.0f})")
            elif usage_pct > 70:
                g.warn("连接池使用率", f"{usage_pct:.0f}% ({active:.0f}/{total:.0f})")
            else:
                g.ok("连接池使用率", f"{usage_pct:.0f}% ({active:.0f}/{total:.0f})")
        else:
            g.ok("连接池使用率", f"active={active:.0f}, available={available:.0f}")
    else:
        g.warn("连接池 Metrics", "未找到 Agroal 连接池指标")

    if awaiting is not None and awaiting > 0:
        g.warn("连接池等待", f"{awaiting:.0f} 个线程等待获取连接")

    # 检查连接获取超时
    timeout_count = _extract_metric(body, r'agroal_timeout_total\s+(\d+\.?\d*)')
    if timeout_count is not None and timeout_count > 0:
        g.error("连接池超时", f"累计 {timeout_count:.0f} 次连接获取超时")


def _extract_metric(text: str, pattern: str):
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


_DB_ERROR_PATTERNS = [
    (r"Unable to obtain connection", "连接获取失败"),
    (r"Connection refused", "数据库连接被拒绝"),
    (r"Connection timed out", "数据库连接超时"),
    (r"password authentication failed", "数据库认证失败"),
    (r"FATAL.*database.*does not exist", "数据库不存在"),
    (r"migration.*failed", "数据库迁移失败"),
    (r"schema.*error", "Schema 错误"),
    (r"Flyway.*failed", "Flyway 迁移失败"),
    (r"liquibase.*error", "Liquibase 错误"),
    (r"JTA.*rollback", "事务回滚"),
]


def _check_k8s_logs(ctx: dict, g: CheckGroup):
    """从 K8s Pod 日志中检查数据库相关错误。"""
    k8s_core = ctx.get("k8s_core")
    if not k8s_core:
        return

    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception:
        return

    errors_found = []
    for pod in (pods.items or [])[:5]:
        try:
            logs = k8s_core.read_namespaced_pod_log(
                pod.metadata.name, ns, tail_lines=200, container="keycloak")
        except Exception:
            try:
                logs = k8s_core.read_namespaced_pod_log(
                    pod.metadata.name, ns, tail_lines=200)
            except Exception:
                continue

        for pattern, desc in _DB_ERROR_PATTERNS:
            if re.search(pattern, logs, re.IGNORECASE):
                errors_found.append(f"{pod.metadata.name}: {desc}")

    if errors_found:
        g.error("日志 DB 错误", f"发现 {len(errors_found)} 个数据库相关错误",
                detail="\n".join(errors_found[:10]))
    else:
        g.ok("日志 DB 检查", "最近日志中未发现数据库错误")


def _check_docker_logs(ctx: dict, g: CheckGroup):
    """从 Docker 容器日志中检查数据库相关错误。"""
    import subprocess

    docker_client = ctx.get("docker_client")
    container_name = ctx.get("docker_container")

    logs = None
    if docker_client and container_name:
        try:
            c = docker_client.containers.get(container_name)
            logs = c.logs(tail=200).decode("utf-8", errors="replace")
        except Exception:
            pass

    if logs is None:
        try:
            # 降级: docker logs 命令
            result = subprocess.run(
                ["docker", "ps", "--filter", "ancestor=quay.io/keycloak/keycloak",
                 "-q", "--no-trunc"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                cid = result.stdout.strip().splitlines()[0]
                result = subprocess.run(
                    ["docker", "logs", "--tail", "200", cid],
                    capture_output=True, text=True, timeout=10,
                )
                logs = result.stdout + result.stderr
        except Exception:
            pass

    if not logs:
        return

    errors_found = []
    for pattern, desc in _DB_ERROR_PATTERNS:
        if re.search(pattern, logs, re.IGNORECASE):
            errors_found.append(desc)

    if errors_found:
        g.error("日志 DB 错误", f"发现 {len(errors_found)} 个数据库相关错误",
                detail="\n".join(errors_found))
    else:
        g.ok("日志 DB 检查", "最近日志中未发现数据库错误")


def _format_data(data: dict) -> str:
    if not data:
        return None
    lines = [f"  {k}: {v}" for k, v in data.items()]
    return "\n".join(lines)
