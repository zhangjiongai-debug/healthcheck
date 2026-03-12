"""4.4 认证能力检查。

- 登录页是否可访问
- Token 获取接口是否正常
- OIDC 发现文档是否正常
- JWKS endpoint 是否正常
- token 签发是否成功
- refresh token 是否正常
- logout 流程是否正常
"""

import time

from ..result import CheckGroup


def check(ctx: dict, test_realm: str = "master") -> CheckGroup:
    g = CheckGroup("4.4 认证能力检查")
    kc = ctx["kc"]

    # ── 登录页可访问性 ──
    resp = kc.get(f"/realms/{test_realm}/account")
    if resp["status"] in (200, 302, 303):
        g.ok("登录页", f"realm [{test_realm}] 登录页可访问")
    else:
        g.error("登录页", f"realm [{test_realm}] 登录页不可访问 (HTTP {resp['status']})")

    # ── OIDC 发现文档 ──
    well_known = kc.get(f"/realms/{test_realm}/.well-known/openid-configuration")
    if well_known["status"] == 200 and isinstance(well_known["body"], dict):
        oidc = well_known["body"]
        issuer = oidc.get("issuer", "?")
        g.ok("OIDC 发现文档", f"正常 (issuer={issuer})")

        # 验证关键端点存在
        required_endpoints = [
            "authorization_endpoint",
            "token_endpoint",
            "userinfo_endpoint",
            "jwks_uri",
            "end_session_endpoint",
        ]
        missing = [ep for ep in required_endpoints if ep not in oidc]
        if missing:
            g.warn("OIDC 端点", f"发现文档缺少: {', '.join(missing)}")
        else:
            g.ok("OIDC 端点", "所有关键端点均已声明")

        # ── JWKS 端点 ──
        jwks_uri = oidc.get("jwks_uri")
        if jwks_uri:
            _check_jwks(kc, jwks_uri, test_realm, g)
    else:
        g.error("OIDC 发现文档", f"无法获取 (HTTP {well_known['status']})")

    # ── Token 签发测试 ──
    _check_token_flow(kc, test_realm, g)

    return g


def _check_jwks(kc, jwks_uri: str, realm: str, g: CheckGroup):
    """检查 JWKS 端点是否返回有效密钥。"""
    # 使用相对路径
    resp = kc.get(f"/realms/{realm}/protocol/openid-connect/certs")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        keys = resp["body"].get("keys", [])
        if keys:
            g.ok("JWKS 端点", f"返回 {len(keys)} 个密钥")
            # 检查密钥类型
            algs = set(k.get("alg", "?") for k in keys)
            g.ok("JWKS 算法", f"算法: {', '.join(algs)}")
        else:
            g.error("JWKS 端点", "返回空密钥集")
    else:
        g.error("JWKS 端点", f"获取失败 (HTTP {resp['status']})")


def _check_token_flow(kc, realm: str, g: CheckGroup):
    """使用 admin 凭证测试完整 token 流程。"""
    if not kc.admin_user or not kc.admin_password:
        g.warn("Token 签发", "未提供管理员凭证，跳过 token 流程测试")
        return

    # ── 获取 token ──
    start = time.time()
    resp = kc.post(
        f"/realms/{realm}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": kc.admin_user,
            "password": kc.admin_password,
        },
        content_type="application/x-www-form-urlencoded",
    )
    token_latency = (time.time() - start) * 1000

    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        g.error("Token 签发", f"失败 (HTTP {resp['status']})")
        return

    token_data = resp["body"]
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token:
        g.error("Token 签发", "响应中无 access_token")
        return

    g.ok("Token 签发", f"成功 (耗时 {token_latency:.0f}ms)")

    if token_latency > 2000:
        g.warn("Token 签发延迟", f"{token_latency:.0f}ms 较高")

    # ── Refresh token 测试 ──
    if refresh_token:
        start = time.time()
        refresh_resp = kc.post(
            f"/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "refresh_token",
                "client_id": "admin-cli",
                "refresh_token": refresh_token,
            },
            content_type="application/x-www-form-urlencoded",
        )
        refresh_latency = (time.time() - start) * 1000

        if refresh_resp["status"] == 200:
            g.ok("Refresh Token", f"刷新成功 (耗时 {refresh_latency:.0f}ms)")
            # 用新的 token 做 logout
            new_token_data = refresh_resp["body"] if isinstance(refresh_resp["body"], dict) else {}
            new_refresh = new_token_data.get("refresh_token", refresh_token)
        else:
            g.error("Refresh Token", f"刷新失败 (HTTP {refresh_resp['status']})")
            new_refresh = refresh_token
    else:
        g.warn("Refresh Token", "响应中无 refresh_token")
        new_refresh = None

    # ── Logout 测试 ──
    if new_refresh:
        logout_resp = kc.post(
            f"/realms/{realm}/protocol/openid-connect/logout",
            data={
                "client_id": "admin-cli",
                "refresh_token": new_refresh,
            },
            content_type="application/x-www-form-urlencoded",
        )
        if logout_resp["status"] in (200, 204):
            g.ok("Logout", "登出成功")
        else:
            g.warn("Logout", f"登出返回 HTTP {logout_resp['status']}")

    # ── UserInfo 端点测试 ──
    userinfo_resp = kc.get(
        f"/realms/{realm}/protocol/openid-connect/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_resp["status"] == 200:
        g.ok("UserInfo", "端点正常")
    else:
        # token 可能已 logout，可接受
        g.warn("UserInfo", f"HTTP {userinfo_resp['status']} (token 可能已失效)")
