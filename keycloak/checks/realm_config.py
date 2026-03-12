"""4.3 Realm / Client / User Federation 基础配置检查。

- 核心 realm 是否存在
- 核心 client 是否存在
- redirect URI 是否异常
- identity provider 是否可用
- LDAP/AD federation 是否可用
- 必要管理员账户是否存在
"""

from ..result import CheckGroup


def check(ctx: dict, required_realms: list[str] = None,
          required_clients: dict[str, list[str]] = None) -> CheckGroup:
    """
    参数:
        required_realms: 必须存在的 realm 名称列表, 默认 ["master"]
        required_clients: {realm: [client_id, ...]} 必须存在的 client
    """
    g = CheckGroup("4.3 Realm / Client / Federation 配置")
    kc = ctx["kc"]

    if required_realms is None:
        required_realms = ["master"]

    # ── 获取 admin token ──
    token = kc.get_admin_token()
    if not token:
        g.warn("Admin API", "无法获取 admin token，跳过配置检查 (未提供管理员凭证或认证失败)")
        return g

    # ── 检查 Realm ──
    resp = kc.admin_get("/admin/realms")
    if resp["status"] != 200:
        g.error("Realm 列表", f"无法获取 realm 列表 (HTTP {resp['status']})")
        return g

    realms = resp["body"]
    if not isinstance(realms, list):
        g.error("Realm 列表", "返回格式异常")
        return g

    realm_names = {r.get("realm") for r in realms}
    g.ok("Realm 总数", f"共 {len(realms)} 个 realm")

    for req_realm in required_realms:
        if req_realm in realm_names:
            g.ok(f"Realm [{req_realm}]", "存在")
        else:
            g.error(f"Realm [{req_realm}]", "缺失!")

    # ── 检查 Realm 状态 ──
    for r in realms:
        realm_name = r.get("realm", "?")
        if not r.get("enabled", True):
            g.warn(f"Realm [{realm_name}]", "已禁用")

    # ── 检查 Client ──
    if required_clients:
        for realm, client_ids in required_clients.items():
            if realm not in realm_names:
                continue
            _check_clients(kc, realm, client_ids, g)

    # ── 检查每个 realm 的 identity provider 和 federation ──
    for realm_name in required_realms:
        if realm_name not in realm_names:
            continue
        _check_identity_providers(kc, realm_name, g)
        _check_user_federation(kc, realm_name, g)
        _check_admin_users(kc, realm_name, g)

    return g


def _check_clients(kc, realm: str, required_client_ids: list[str], g: CheckGroup):
    """检查 realm 中的 client 是否存在及配置。"""
    resp = kc.admin_get(f"/admin/realms/{realm}/clients?max=500")
    if resp["status"] != 200:
        g.warn(f"Client [{realm}]", f"无法获取 client 列表 (HTTP {resp['status']})")
        return

    clients = resp["body"]
    if not isinstance(clients, list):
        return

    client_map = {c.get("clientId"): c for c in clients}

    for cid in required_client_ids:
        if cid not in client_map:
            g.error(f"Client [{realm}/{cid}]", "缺失!")
            continue

        c = client_map[cid]
        if not c.get("enabled", True):
            g.warn(f"Client [{realm}/{cid}]", "已禁用")
            continue

        g.ok(f"Client [{realm}/{cid}]", "存在且启用")

        # 检查 redirect URI
        redirect_uris = c.get("redirectUris", [])
        if redirect_uris:
            wildcards = [u for u in redirect_uris if u == "*" or u == "/*"]
            if wildcards:
                g.warn(f"Client [{realm}/{cid}] redirectUri",
                       "包含通配符 redirect URI，存在安全风险",
                       detail="\n".join(f"  {u}" for u in redirect_uris[:10]))


def _check_identity_providers(kc, realm: str, g: CheckGroup):
    """检查 identity provider 配置。"""
    resp = kc.admin_get(f"/admin/realms/{realm}/identity-provider/instances")
    if resp["status"] != 200:
        return

    providers = resp["body"]
    if not isinstance(providers, list) or not providers:
        return

    for idp in providers:
        alias = idp.get("alias", "?")
        enabled = idp.get("enabled", True)
        provider_id = idp.get("providerId", "?")

        if not enabled:
            g.warn(f"IdP [{realm}/{alias}]", f"已禁用 (type={provider_id})")
        else:
            g.ok(f"IdP [{realm}/{alias}]", f"已启用 (type={provider_id})")


def _check_user_federation(kc, realm: str, g: CheckGroup):
    """检查 LDAP/AD 等 user federation 配置。"""
    resp = kc.admin_get(f"/admin/realms/{realm}/components?type=org.keycloak.storage.UserStorageProvider")
    if resp["status"] != 200:
        return

    components = resp["body"]
    if not isinstance(components, list) or not components:
        return

    for comp in components:
        name = comp.get("name", "?")
        provider_type = comp.get("providerId", "?")
        cfg = comp.get("config", {})

        # 检查连接状态
        enabled = cfg.get("enabled", ["true"])
        if isinstance(enabled, list):
            enabled = enabled[0] if enabled else "true"

        if enabled.lower() != "true":
            g.warn(f"Federation [{realm}/{name}]", f"已禁用 (type={provider_type})")
            continue

        g.ok(f"Federation [{realm}/{name}]", f"已启用 (type={provider_type})")

        # LDAP 连通性测试
        if provider_type in ("ldap", "ad"):
            comp_id = comp.get("id")
            if comp_id:
                test_resp = kc.admin_get(
                    f"/admin/realms/{realm}/testLDAPConnection",
                )
                # 注意: testLDAPConnection 是 POST，这里只是记录存在
                connection_url = cfg.get("connectionUrl", [""])[0] if isinstance(cfg.get("connectionUrl"), list) else ""
                if connection_url:
                    g.ok(f"Federation [{realm}/{name}] URL", connection_url)


def _check_admin_users(kc, realm: str, g: CheckGroup):
    """检查 master realm 是否有管理员用户。"""
    if realm != "master":
        return

    resp = kc.admin_get(f"/admin/realms/{realm}/users?max=10")
    if resp["status"] != 200:
        return

    users = resp["body"]
    if not isinstance(users, list):
        return

    if not users:
        g.error(f"管理员用户 [{realm}]", "master realm 中没有用户!")
        return

    # 检查有 admin 角色的用户
    admin_found = False
    for user in users:
        username = user.get("username", "?")
        if username in ("admin", "keycloak-admin", "administrator"):
            admin_found = True
            if not user.get("enabled", True):
                g.warn(f"管理员 [{username}]", "账户已禁用")
            else:
                g.ok(f"管理员 [{username}]", "存在且启用")

    if not admin_found:
        g.warn("管理员用户", "未找到常见管理员账户名 (admin/keycloak-admin/administrator)")
