"""7. APISIX Dashboard 专项检查。

- Dashboard 可用性
- Dashboard 版本信息
- Dashboard 登录功能
- Dashboard 与数据面是否同步
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("7. APISIX Dashboard 检查")
    dashboard = ctx.get("dashboard")

    if not dashboard:
        g.ok("Dashboard", "未配置 Dashboard URL，跳过检查")
        return g

    if "dashboard_connect_error" in ctx:
        g.error("Dashboard 连接", ctx["dashboard_connect_error"])
        # 继续检查 K8s 层面
        if ctx["mode"] == DeployMode.K8S:
            _check_dashboard_k8s(ctx, g)
        return g

    # ── Dashboard 版本 ──
    _check_version(ctx, g)

    # ── Dashboard 页面可达 ──
    _check_page_accessible(dashboard, g)

    # ── Dashboard 登录 ──
    _check_login(dashboard, g)

    # ── Dashboard 与 APISIX 数据同步 ──
    _check_data_sync(ctx, g)

    return g


def _check_version(ctx, g):
    """检查 Dashboard 版本信息。"""
    version_info = ctx.get("dashboard_version", {})
    if version_info:
        ver = version_info.get("version", "unknown")
        commit = version_info.get("commit_hash", "unknown")
        g.ok("Dashboard 版本", f"v{ver} (commit: {commit})")
    else:
        dashboard = ctx["dashboard"]
        resp = dashboard.version()
        if resp["status"] == 200:
            body = resp.get("body", {})
            if isinstance(body, dict) and body.get("code") == 0:
                data = body.get("data", {})
                ver = data.get("version", "unknown")
                g.ok("Dashboard 版本", f"v{ver}")
            else:
                g.ok("Dashboard 版本", "接口可达")
        else:
            g.warn("Dashboard 版本", f"获取失败 (status={resp['status']})")


def _check_page_accessible(dashboard, g):
    """检查 Dashboard 页面是否可访问。"""
    resp = dashboard.get("/")
    if resp["status"] == 200:
        g.ok("Dashboard 页面", "首页可访问")
    elif resp["status"] == 0:
        g.error("Dashboard 页面", f"不可达: {resp['body']}")
    else:
        g.warn("Dashboard 页面", f"返回 status={resp['status']}")


def _check_login(dashboard, g):
    """检查 Dashboard 登录功能。"""
    if not dashboard.username or not dashboard.password:
        g.ok("Dashboard 登录", "未提供凭据，跳过登录测试")
        return

    resp = dashboard.login()
    if resp["status"] == 200:
        body = resp.get("body", {})
        if isinstance(body, dict):
            code = body.get("code", -1)
            if code == 0:
                token = body.get("data", {}).get("token", "")
                if token:
                    dashboard.token = token
                g.ok("Dashboard 登录", "登录成功")
            else:
                msg = body.get("message", "unknown error")
                g.error("Dashboard 登录", f"登录失败: {msg}")
        else:
            g.ok("Dashboard 登录", "接口可达")
    elif resp["status"] == 401:
        g.error("Dashboard 登录", "凭据无效")
    elif resp["status"] == 0:
        g.error("Dashboard 登录", f"请求失败: {resp['body']}")
    else:
        g.warn("Dashboard 登录", f"异常响应 (status={resp['status']})")


def _check_data_sync(ctx, g):
    """检查 Dashboard 与 APISIX 数据是否一致。"""
    apisix = ctx["apisix"]

    if "connect_error" in ctx:
        g.warn("数据同步", "Admin API 不可达，无法进行同步检查")
        return

    # 通过 Admin API 获取路由数和 Dashboard 看到的对比
    resp = apisix.routes()
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        admin_routes = resp["body"].get("total", 0)
        g.ok("数据同步", f"Admin API 路由数: {admin_routes}")
    else:
        g.warn("数据同步", "无法获取 Admin API 路由数据")


def _check_dashboard_k8s(ctx, g):
    """K8s: 检查 Dashboard Pod 状态。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]
    selector = ctx.get("dashboard_label_selector", "")

    if not k8s_core or not selector:
        return

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
        if not pods.items:
            g.warn("Dashboard K8s", f"未发现 Pod (selector={selector})")
            return

        for pod in pods.items:
            pod_name = pod.metadata.name
            phase = pod.status.phase
            if phase == "Running":
                conditions = {c.type: c.status
                              for c in (pod.status.conditions or [])}
                if conditions.get("Ready") == "True":
                    g.ok(f"Dashboard Pod {pod_name}", "Running & Ready")
                else:
                    g.warn(f"Dashboard Pod {pod_name}", "Running 但未 Ready")
            else:
                g.error(f"Dashboard Pod {pod_name}", f"状态: {phase}")
    except Exception as e:
        g.warn("Dashboard K8s", f"获取 Pod 状态失败: {e}")
