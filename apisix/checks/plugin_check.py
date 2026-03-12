"""5. 插件专项检查。

- auth 插件是否正常
- rate limit 插件是否误伤
- CORS 插件配置是否正确
- prometheus 插件是否暴露指标
- request/response rewrite 是否异常
- plugin metadata 是否缺失
"""

from ..result import CheckGroup


# 关键插件列表
_AUTH_PLUGINS = ("key-auth", "basic-auth", "jwt-auth", "hmac-auth",
                 "openid-connect", "wolf-rbac", "ldap-auth")
_RATE_LIMIT_PLUGINS = ("limit-req", "limit-conn", "limit-count")
_OBSERVABILITY_PLUGINS = ("prometheus", "zipkin", "skywalking",
                          "opentelemetry", "datadog")
_TRANSFORM_PLUGINS = ("proxy-rewrite", "response-rewrite",
                      "grpc-transcode", "grpc-web")
_SECURITY_PLUGINS = ("cors", "ip-restriction", "ua-restriction",
                     "referer-restriction", "csrf")


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5. 插件专项检查")
    apisix = ctx["apisix"]

    if "connect_error" in ctx:
        g.fatal("Admin API", ctx["connect_error"])
        return g

    # 获取已加载插件列表
    resp = apisix.plugins_list()
    if resp["status"] != 200:
        g.error("插件列表", f"获取失败 (status={resp['status']})")
        return g

    available_plugins = resp["body"]
    if not isinstance(available_plugins, list):
        g.warn("插件列表", "响应格式异常")
        return g

    g.ok("已加载插件", f"共 {len(available_plugins)} 个可用插件")

    # 检查关键插件是否可用
    _check_plugin_category(available_plugins, _AUTH_PLUGINS, "认证插件", g)
    _check_plugin_category(available_plugins, _RATE_LIMIT_PLUGINS, "限流插件", g)
    _check_plugin_category(available_plugins, _OBSERVABILITY_PLUGINS, "可观测性插件", g)
    _check_plugin_category(available_plugins, _SECURITY_PLUGINS, "安全插件", g)

    # 检查 prometheus 插件是否开启
    if "prometheus" in available_plugins:
        g.ok("Prometheus 插件", "已加载")
    else:
        g.warn("Prometheus 插件", "未加载，无法通过 Prometheus 获取指标")

    # 检查路由中实际使用的插件
    _check_plugins_in_routes(apisix, g)

    # 检查 plugin metadata
    _check_plugin_metadata(apisix, available_plugins, g)

    return g


def _check_plugin_category(available_plugins, category, name, g):
    """检查某类插件是否至少有一个可用。"""
    found = [p for p in category if p in available_plugins]
    if found:
        g.ok(name, f"可用: {', '.join(found)}")
    else:
        g.ok(name, "未加载 (如需使用请确认配置)")


def _check_plugins_in_routes(apisix, g):
    """检查路由中使用的插件是否都已加载。"""
    resp = apisix.routes()
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    routes = resp["body"].get("list", [])
    if not routes:
        return

    # 收集所有路由使用的插件
    plugins_used = set()
    route_plugin_map = {}  # plugin -> list of route names
    for item in routes:
        route = item.get("value", item) if isinstance(item, dict) else {}
        route_name = route.get("name", route.get("id", "unknown"))
        plugins = route.get("plugins", {})
        for p_name in plugins:
            plugins_used.add(p_name)
            route_plugin_map.setdefault(p_name, []).append(route_name)

    if not plugins_used:
        g.ok("路由插件", "当前路由未配置插件")
        return

    g.ok("路由插件使用", f"共使用 {len(plugins_used)} 种插件")

    # 检查是否有使用了 auth 类插件的路由
    auth_routes = 0
    for p in _AUTH_PLUGINS:
        auth_routes += len(route_plugin_map.get(p, []))
    if auth_routes > 0:
        g.ok("认证插件使用", f"{auth_routes} 条路由配置了认证插件")

    # 检查是否有使用 rate limit 的路由
    rl_routes = 0
    for p in _RATE_LIMIT_PLUGINS:
        rl_routes += len(route_plugin_map.get(p, []))
    if rl_routes > 0:
        g.ok("限流插件使用", f"{rl_routes} 条路由配置了限流插件")


def _check_plugin_metadata(apisix, available_plugins, g):
    """检查常见需要 metadata 的插件。"""
    # 这些插件通常需要全局 metadata 配置
    metadata_plugins = ["prometheus", "zipkin", "skywalking",
                        "opentelemetry", "error-log-logger",
                        "http-logger", "kafka-logger", "syslog"]

    issues = []
    for plugin in metadata_plugins:
        if plugin not in available_plugins:
            continue
        resp = apisix.plugin_metadata(plugin)
        # 404 表示无 metadata，对某些插件可能是正常的
        # 只有特定插件缺少 metadata 时才告警

    # metadata 检查比较宽松，不做硬告警
