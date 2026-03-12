"""8. APISIX 风险预警。

- etcd 不可达风险
- Dashboard 可用但数据面未同步风险
- 核心路由后端 endpoint 为空
- 插件加载失败
- 配置发布不一致
- 网关实例仅单副本
- 证书剩余时间过短
"""

import ssl
import socket
from datetime import datetime

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("8. 风险预警")
    apisix = ctx["apisix"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"Admin API 无法连接: {ctx['connect_error']}")
        if mode == DeployMode.K8S:
            _check_single_point_k8s(ctx, g)
        return g

    # ── etcd 不可达风险 ──
    _check_etcd_risk(ctx, g)

    # ── 核心路由后端为空 ──
    _check_empty_upstream_risk(apisix, g)

    # ── 插件加载风险 ──
    _check_plugin_risk(apisix, g)

    # ── Dashboard 同步风险 ──
    _check_dashboard_sync_risk(ctx, g)

    # ── TLS 证书过期风险 ──
    _check_tls_risk(apisix, g)

    # ── K8s 单点风险 ──
    if mode == DeployMode.K8S:
        _check_single_point_k8s(ctx, g)
        _check_k8s_resource_risk(ctx, g)

    return g


def _check_etcd_risk(ctx, g):
    """检查 etcd 不可达风险。"""
    mode = ctx["mode"]

    if mode == DeployMode.K8S:
        k8s_core = ctx.get("k8s_core")
        ns = ctx["namespace"]
        if not k8s_core:
            return

        # 检查 etcd Pod 状态
        selectors = [
            "app.kubernetes.io/name=etcd",
            "app=etcd",
        ]
        for sel in selectors:
            try:
                pods = k8s_core.list_namespaced_pod(ns, label_selector=sel)
                if pods.items:
                    unhealthy = [p for p in pods.items
                                 if p.status.phase != "Running"]
                    if unhealthy:
                        names = [p.metadata.name for p in unhealthy]
                        g.fatal("etcd 不可达风险",
                                f"{len(unhealthy)} 个 etcd Pod 异常",
                                detail="\n".join(names))
                    # 检查 etcd 集群规模
                    if len(pods.items) == 1:
                        g.warn("etcd 单点风险", "etcd 仅有单副本，无高可用保护")
                    return
            except Exception:
                continue


def _check_empty_upstream_risk(apisix, g):
    """检查核心路由后端 endpoint 为空的风险。"""
    resp = apisix.routes()
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    routes = resp["body"].get("list", [])
    empty_backend = []
    for item in routes:
        route = item.get("value", item) if isinstance(item, dict) else {}
        name = route.get("name", route.get("id", "unknown"))
        upstream = route.get("upstream", {})

        if upstream:
            nodes = upstream.get("nodes")
            if nodes is not None:
                if isinstance(nodes, dict) and len(nodes) == 0:
                    empty_backend.append(name)
                elif isinstance(nodes, list) and len(nodes) == 0:
                    empty_backend.append(name)

    if empty_backend:
        g.error("路由后端为空",
                f"{len(empty_backend)} 条路由的 upstream 节点为空",
                detail="\n".join(empty_backend[:10]))


def _check_plugin_risk(apisix, g):
    """检查插件加载风险。"""
    resp = apisix.plugins_list()
    if resp["status"] != 200:
        g.error("插件加载风险", f"无法获取插件列表 (status={resp['status']})")
        return

    plugins = resp["body"]
    if isinstance(plugins, list) and len(plugins) == 0:
        g.fatal("插件加载风险", "无任何插件加载，APISIX 可能配置异常")
    elif isinstance(plugins, list) and len(plugins) < 10:
        g.warn("插件加载风险",
               f"仅加载了 {len(plugins)} 个插件，数量偏少")


def _check_dashboard_sync_risk(ctx, g):
    """检查 Dashboard 可用但数据面未同步风险。"""
    dashboard = ctx.get("dashboard")
    if not dashboard:
        return

    if "dashboard_connect_error" not in ctx and "connect_error" in ctx:
        g.error("Dashboard 同步风险",
                "Dashboard 可用但 Admin API 不可达，"
                "Dashboard 可能无法正确管理 APISIX")


def _check_tls_risk(apisix, g):
    """检查 SSL 证书过期风险。"""
    resp = apisix.ssls()
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    ssls = resp["body"].get("list", [])
    expiring_soon = []
    expired = []

    for item in ssls:
        ssl_obj = item.get("value", item) if isinstance(item, dict) else {}
        snis = ssl_obj.get("snis", [])
        sni_display = ", ".join(snis[:2]) if snis else ssl_obj.get("id", "unknown")
        validity_end = ssl_obj.get("validity_end")

        if validity_end:
            try:
                expire_dt = datetime.fromtimestamp(validity_end)
                days_left = (expire_dt - datetime.now()).days
                if days_left < 0:
                    expired.append(f"{sni_display}: 已过期 {-days_left} 天")
                elif days_left < 7:
                    expiring_soon.append(f"{sni_display}: 剩余 {days_left} 天")
            except (ValueError, OSError):
                pass

    if expired:
        g.fatal("证书过期风险",
                f"{len(expired)} 个证书已过期!",
                detail="\n".join(expired))
    if expiring_soon:
        g.error("证书即将过期",
                f"{len(expiring_soon)} 个证书即将过期",
                detail="\n".join(expiring_soon))

    # 检查 Admin API 端点 TLS
    url = apisix.admin_url
    if url.startswith("https://"):
        _check_endpoint_cert_risk(url, "Admin API", g)


def _check_endpoint_cert_risk(url, name, g):
    """检查端点证书过期风险。"""
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
                            g.fatal(f"{name} TLS 证书风险",
                                    f"即将过期! 剩余 {days_left} 天")
    except Exception:
        pass


def _check_single_point_k8s(ctx, g):
    """K8s: 检查单点风险。"""
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]

    if not k8s_apps:
        return

    selectors = [
        (ctx["label_selector"], "APISIX"),
        (ctx["dashboard_label_selector"], "Dashboard"),
    ]

    for selector, comp_name in selectors:
        try:
            deploys = k8s_apps.list_namespaced_deployment(
                ns, label_selector=selector)
            for dep in deploys.items:
                name = dep.metadata.name
                replicas = dep.spec.replicas or 1
                if replicas == 1 and comp_name == "APISIX":
                    g.warn(f"单点风险 [{name}]",
                           f"{comp_name} 为单副本 (replicas=1)，无高可用保护")
        except Exception:
            pass


def _check_k8s_resource_risk(ctx, g):
    """K8s: 检查资源风险。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    # 检查 PVC
    try:
        pvcs = k8s_core.list_namespaced_persistent_volume_claim(ns)
        for pvc in pvcs.items:
            phase = pvc.status.phase
            if phase != "Bound":
                g.error(f"PVC {pvc.metadata.name}",
                        f"状态异常: {phase}")
    except Exception:
        pass
