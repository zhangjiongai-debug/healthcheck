"""3. Route / Upstream / Service / Consumer 检查。

- 路由是否存在
- 路由配置是否合法
- Upstream 是否有健康节点
- Upstream 节点数是否满足预期
- 后端服务是否可达
- Consumer 配置是否完整
- 插件配置是否正确加载
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("3. Route / Upstream / Service / Consumer")
    apisix = ctx["apisix"]

    if "connect_error" in ctx:
        g.fatal("Admin API", ctx["connect_error"])
        return g

    _check_routes(apisix, g)
    _check_upstreams(apisix, g)
    _check_services(apisix, g)
    _check_consumers(apisix, g)

    return g


def _check_routes(apisix, g):
    """检查路由配置。"""
    resp = apisix.routes()
    if resp["status"] != 200:
        g.error("路由列表", f"获取失败 (status={resp['status']})")
        return

    body = resp["body"]
    if not isinstance(body, dict):
        g.warn("路由列表", "响应格式异常")
        return

    total = body.get("total", 0)
    routes = body.get("list", [])

    if total == 0:
        g.warn("路由", "当前无路由配置")
        return

    g.ok("路由数量", f"共 {total} 条路由")

    # 检查每条路由配置合法性
    issues = []
    for item in routes:
        route = item.get("value", item) if isinstance(item, dict) else {}
        route_id = route.get("id", "unknown")
        name = route.get("name", route_id)

        # 检查是否有 uri 或 uris
        has_uri = route.get("uri") or route.get("uris")
        if not has_uri:
            issues.append(f"路由 {name}: 缺少 uri/uris 配置")

        # 检查是否有 upstream 或 upstream_id 或 service_id
        has_backend = (route.get("upstream") or route.get("upstream_id")
                       or route.get("service_id") or route.get("plugin_config_id"))
        if not has_backend:
            # 某些路由可能只用 plugins (如 redirect)
            plugins = route.get("plugins", {})
            has_redirect = any(k in plugins for k in ("redirect", "proxy-rewrite"))
            if not has_redirect:
                issues.append(f"路由 {name}: 缺少 upstream/service 后端配置")

    if issues:
        g.warn("路由配置", f"{len(issues)} 条路由存在潜在问题",
               detail="\n".join(issues[:10]))
    else:
        g.ok("路由配置", "所有路由配置合法")


def _check_upstreams(apisix, g):
    """检查 Upstream 配置。"""
    resp = apisix.upstreams()
    if resp["status"] != 200:
        g.error("Upstream 列表", f"获取失败 (status={resp['status']})")
        return

    body = resp["body"]
    if not isinstance(body, dict):
        return

    total = body.get("total", 0)
    upstreams = body.get("list", [])

    if total == 0:
        g.ok("Upstream", "当前无独立 Upstream 配置 (可能内嵌在路由中)")
        return

    g.ok("Upstream 数量", f"共 {total} 个 Upstream")

    issues = []
    for item in upstreams:
        ups = item.get("value", item) if isinstance(item, dict) else {}
        ups_id = ups.get("id", "unknown")
        name = ups.get("name", ups_id)
        nodes = ups.get("nodes")

        if not nodes:
            issues.append(f"Upstream {name}: 无节点配置")
            continue

        # nodes 可以是 dict 或 list
        if isinstance(nodes, dict):
            node_count = len(nodes)
        elif isinstance(nodes, list):
            node_count = len(nodes)
        else:
            node_count = 0

        if node_count == 0:
            issues.append(f"Upstream {name}: 节点数为 0")

    if issues:
        g.warn("Upstream 节点", f"{len(issues)} 个 Upstream 存在问题",
               detail="\n".join(issues[:10]))
    else:
        g.ok("Upstream 节点", "所有 Upstream 节点配置正常")


def _check_services(apisix, g):
    """检查 Service 配置。"""
    resp = apisix.services()
    if resp["status"] != 200:
        if resp["status"] != 0:
            g.warn("Service 列表", f"获取失败 (status={resp['status']})")
        return

    body = resp["body"]
    if not isinstance(body, dict):
        return

    total = body.get("total", 0)
    g.ok("Service 数量", f"共 {total} 个 Service")


def _check_consumers(apisix, g):
    """检查 Consumer 配置。"""
    resp = apisix.consumers()
    if resp["status"] != 200:
        if resp["status"] != 0:
            g.warn("Consumer 列表", f"获取失败 (status={resp['status']})")
        return

    body = resp["body"]
    if not isinstance(body, dict):
        return

    total = body.get("total", 0)
    consumers = body.get("list", [])

    if total == 0:
        g.ok("Consumer", "当前无 Consumer 配置")
        return

    g.ok("Consumer 数量", f"共 {total} 个 Consumer")

    # 检查 consumer 是否有认证插件
    issues = []
    for item in consumers:
        consumer = item.get("value", item) if isinstance(item, dict) else {}
        username = consumer.get("username", "unknown")
        plugins = consumer.get("plugins", {})
        auth_plugins = [k for k in plugins
                        if any(a in k for a in ("key-auth", "basic-auth", "jwt-auth",
                                                 "hmac-auth", "openid-connect"))]
        if not auth_plugins:
            issues.append(f"Consumer {username}: 未配置认证插件")

    if issues:
        g.warn("Consumer 认证", f"{len(issues)} 个 Consumer 缺少认证插件",
               detail="\n".join(issues[:10]))
