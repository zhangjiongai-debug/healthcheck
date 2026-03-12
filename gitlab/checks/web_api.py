"""2. GitLab 页面与 API 可用性检查。

- GitLab Web 首页是否可访问
- 登录页是否正常
- API 基础接口是否正常
- 健康检查端点是否正常
- Nginx/Workhorse 是否正常
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("2. 页面与 API 可用性")
    gl = ctx["gl"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 健康端点 ──
    _check_health_endpoints(gl, g)

    # ── Web 首页 ──
    _check_web_homepage(gl, g)

    # ── API 可用性 ──
    _check_api_availability(gl, g)

    # ── 版本信息 ──
    _check_version(gl, g)

    return g


def _check_health_endpoints(gl, g):
    """检查 GitLab 内置健康端点。"""
    # /-/health
    resp = gl.health()
    if resp["status"] == 200:
        body = resp["body"]
        if isinstance(body, str) and "GitLab OK" in body:
            g.ok("/-/health", "GitLab OK")
        elif isinstance(body, dict):
            g.ok("/-/health", str(body))
        else:
            g.ok("/-/health", f"status=200")
    else:
        g.error("/-/health", f"异常 (status={resp['status']})")

    # /-/readiness
    resp = gl.readiness()
    if resp["status"] == 200:
        body = resp["body"]
        if isinstance(body, dict):
            status = body.get("status", "unknown")
            checks = body.get("master_check", [])
            if status == "ok":
                g.ok("/-/readiness", f"status=ok, {len(checks)} 个子检查通过")
            else:
                failed = [c for c in checks if c.get("status") != "ok"]
                g.error("/-/readiness", f"status={status}",
                        detail=str(failed)[:200] if failed else None)
        else:
            g.ok("/-/readiness", f"status=200")
    else:
        g.warn("/-/readiness", f"异常 (status={resp['status']})")

    # /-/liveness
    resp = gl.liveness()
    if resp["status"] == 200:
        g.ok("/-/liveness", "正常")
    else:
        g.error("/-/liveness", f"异常 (status={resp['status']})")


def _check_web_homepage(gl, g):
    """检查 Web 首页与登录页。"""
    resp = gl.get("/")
    if resp["status"] in (200, 301, 302, 303):
        g.ok("Web 首页", f"可访问 (status={resp['status']})")
    elif resp["status"] == 503:
        g.error("Web 首页", "服务不可用 (503)")
    else:
        g.warn("Web 首页", f"status={resp['status']}")

    resp = gl.get("/users/sign_in")
    if resp["status"] in (200, 301, 302):
        g.ok("登录页", f"可访问 (status={resp['status']})")
    else:
        g.warn("登录页", f"status={resp['status']}")


def _check_api_availability(gl, g):
    """检查 API v4 基础可用性。"""
    # /api/v4/metadata (不需要认证)
    resp = gl.api_v4("/metadata")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        version = resp["body"].get("version", "?")
        revision = resp["body"].get("revision", "?")
        kas = resp["body"].get("kas", {})
        kas_enabled = kas.get("enabled", False) if isinstance(kas, dict) else False
        g.ok("API /metadata", f"GitLab {version} (revision: {revision}), KAS: {kas_enabled}")
    elif resp["status"] == 401:
        g.ok("API 端点", "可达 (需要认证)")
    elif resp["status"] == 404:
        # 可能是旧版本
        g.ok("API 端点", "可达 (metadata 接口不可用，可能为旧版)")
    else:
        g.warn("API /metadata", f"status={resp['status']}")


def _check_version(gl, g):
    """检查 GitLab 版本 (需要 token)。"""
    resp = gl.api_v4("/version")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        version = resp["body"].get("version", "?")
        revision = resp["body"].get("revision", "?")
        g.ok("GitLab 版本", f"{version} (revision: {revision})")
    elif resp["status"] == 401:
        g.warn("GitLab 版本", "需要 Token 才能获取版本信息 (--token)")
    # 如果 metadata 已获取，不重复报告
