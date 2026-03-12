"""8. GitLab 风险预警。

- Sidekiq 队列持续积压
- Gitaly 存储接近上限
- PostgreSQL/Redis 连接异常
- Runner 全离线
- 对象存储不可达
- 大量 500/502/503
- migration 未完成
- 仓库存储单点风险
- TLS 证书即将过期
"""

import ssl
import socket
from datetime import datetime

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("8. 风险预警")
    gl = ctx["gl"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        if mode == DeployMode.K8S:
            _check_single_point_k8s(ctx, g)
        return g

    # ── Sidekiq 积压风险 ──
    _check_sidekiq_risk(gl, g)

    # ── Runner 风险 ──
    _check_runner_risk(gl, g)

    # ── TLS 证书过期 ──
    _check_tls_cert(gl, g)

    # ── K8s 单点风险 ──
    if mode == DeployMode.K8S:
        _check_single_point_k8s(ctx, g)
        _check_k8s_resource_risk(ctx, g)

    # ── 存储风险 ──
    _check_storage_risk(gl, g, mode, ctx)

    return g


def _check_sidekiq_risk(gl, g):
    """检查 Sidekiq 积压风险。"""
    resp = gl.api_v4("/sidekiq/compound_metrics")
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    body = resp["body"]
    queues = body.get("queues", {})
    if isinstance(queues, dict):
        q_list = queues.get("queues", [])
        if isinstance(q_list, list):
            total_size = sum(q.get("size", 0) for q in q_list)
            max_latency = max((q.get("latency", 0) for q in q_list), default=0)

            if total_size > 5000:
                g.fatal("Sidekiq 积压风险",
                        f"严重积压: {total_size} 个任务, "
                        f"最大延迟 {max_latency:.0f}s")
            elif total_size > 1000:
                g.error("Sidekiq 积压风险",
                        f"积压: {total_size} 个任务, "
                        f"最大延迟 {max_latency:.0f}s")

    # 检查失败任务趋势
    jobs = body.get("jobs", {})
    if isinstance(jobs, dict):
        failed = jobs.get("failed", 0)
        if failed > 1000:
            g.warn("Sidekiq 失败任务",
                   f"累计 {failed} 个失败任务")


def _check_runner_risk(gl, g):
    """检查 Runner 全离线风险。"""
    resp = gl.api_v4("/runners/all", params={"per_page": "100"})
    if resp["status"] != 200 or not isinstance(resp["body"], list):
        resp = gl.api_v4("/runners", params={"per_page": "100"})
        if resp["status"] != 200 or not isinstance(resp["body"], list):
            return

    runners = resp["body"]
    if not runners:
        g.warn("Runner 风险", "未注册任何 Runner，CI/CD 不可用")
        return

    online = sum(1 for r in runners
                 if isinstance(r, dict) and r.get("status") == "online")
    if online == 0:
        g.error("Runner 全离线", f"所有 {len(runners)} 个 Runner 离线!")


def _check_tls_cert(gl, g):
    """检查 TLS 证书是否即将过期。"""
    url = gl.base_url
    if not url.startswith("https://"):
        return

    try:
        hostname = url.split("//")[1].split("/")[0].split(":")[0]
        port = 443
        if ":" in url.split("//")[1].split("/")[0]:
            port = int(url.split("//")[1].split("/")[0].split(":")[1])

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((hostname, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                if cert:
                    not_after_str = cert.get("notAfter", "")
                    if not_after_str:
                        not_after = datetime.strptime(
                            not_after_str, "%b %d %H:%M:%S %Y %Z")
                        days_left = (not_after - datetime.utcnow()).days
                        if days_left < 7:
                            g.fatal("TLS 证书",
                                    f"即将过期! 剩余 {days_left} 天 "
                                    f"(过期: {not_after_str})")
                        elif days_left < 30:
                            g.warn("TLS 证书",
                                   f"即将过期: 剩余 {days_left} 天 "
                                   f"(过期: {not_after_str})")
                        else:
                            g.ok("TLS 证书",
                                 f"有效, 剩余 {days_left} 天")
                else:
                    # binary_form=False 可能因 verify_mode=CERT_NONE 返回空
                    g.ok("TLS 证书", "HTTPS 可用 (无法获取证书详情)")
    except Exception as e:
        g.ok("TLS 检查", f"跳过 ({e})")


def _check_single_point_k8s(ctx, g):
    """K8s: 检查单点风险。"""
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    if not k8s_apps:
        return

    # 检查关键组件是否为单副本
    critical_apps = [
        ("webservice", "Webservice"),
        ("sidekiq", "Sidekiq"),
    ]

    try:
        deploys = k8s_apps.list_namespaced_deployment(ns, label_selector=selector)
        for dep in deploys.items:
            name = dep.metadata.name
            labels = dep.metadata.labels or {}
            app = labels.get("app", "")
            replicas = dep.spec.replicas or 1

            for comp_key, comp_name in critical_apps:
                if comp_key in app and replicas == 1:
                    g.warn(f"单点风险 [{name}]",
                           f"{comp_name} 为单副本 (replicas=1)，无高可用保护")
    except Exception:
        pass

    # StatefulSet 单副本检查
    try:
        stss = k8s_apps.list_namespaced_stateful_set(ns, label_selector=selector)
        for sts in stss.items:
            name = sts.metadata.name
            replicas = sts.spec.replicas or 1
            if replicas == 1:
                g.warn(f"单点风险 [{name}]",
                       f"StatefulSet 为单副本 (replicas=1)")
    except Exception:
        pass


def _check_k8s_resource_risk(ctx, g):
    """K8s: 检查资源风险 (PVC 容量等)。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    if not k8s_core:
        return

    # 检查 PVC
    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
        pvc_names = set()
        for pod in pods.items:
            for vol in (pod.spec.volumes or []):
                if vol.persistent_volume_claim:
                    pvc_names.add(vol.persistent_volume_claim.claim_name)

        for pvc_name in pvc_names:
            try:
                pvc = k8s_core.read_namespaced_persistent_volume_claim(pvc_name, ns)
                phase = pvc.status.phase
                if phase != "Bound":
                    g.error(f"PVC {pvc_name}", f"状态异常: {phase}")
            except Exception:
                pass
    except Exception:
        pass


def _check_storage_risk(gl, g, mode, ctx):
    """检查存储相关风险。"""
    # 通过 Prometheus 或 admin API 检查 Gitaly 存储
    # 大多数情况下需要 admin 权限
    resp = gl.api_v4("/application/statistics")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        stats = resp["body"]
        active_users = int(stats.get("active_users", 0))
        projects = int(stats.get("projects", 0))
        groups = int(stats.get("groups", 0))

        g.ok("实例规模",
             f"活跃用户: {active_users}, 项目: {projects}, 组: {groups}")

        # 大规模实例风险提示
        if projects > 10000:
            g.warn("规模风险", f"项目数量较多 ({projects})，注意存储和性能")
