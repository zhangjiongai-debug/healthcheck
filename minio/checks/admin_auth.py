"""5.4 管理与认证检查。

- 管理接口是否正常
- Access Key / Secret Key 是否有效
- 用户/策略是否完整
- 外部 IAM / OIDC 对接是否正常
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5.4 管理与认证")
    mc = ctx["mc"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 管理控制台可达性 ──
    _check_console(mc, g)

    # ── 认证有效性 ──
    _check_credentials(mc, g)

    # ── 用户与策略 (mc admin) ──
    _check_users_policies(mc, g)

    # ── IAM / OIDC 配置 ──
    _check_iam_oidc(mc, g)

    return g


def _check_console(mc, g):
    """检查管理控制台是否可达。"""
    # MinIO Console 通常在同端口 (浏览器访问自动跳转)
    resp = mc.get("/")
    if resp["status"] in (200, 301, 302, 307, 403):
        g.ok("管理控制台", f"可达 (status={resp['status']})")
    elif resp["status"] == 0:
        g.error("管理控制台", f"不可达: {resp['body']}")
    else:
        g.warn("管理控制台", f"响应异常 (status={resp['status']})")


def _check_credentials(mc, g):
    """验证 Access Key / Secret Key 是否有效。"""
    if not mc.access_key:
        g.warn("认证信息", "未提供 access_key，无法验证凭证")
        return

    # 通过列 bucket 来验证凭证
    buckets = mc.list_buckets_sdk()
    if buckets is not None:
        g.ok("凭证验证", "Access Key / Secret Key 认证成功")
        return

    # SDK 不可用，尝试 mc CLI
    if mc.mc_available():
        output = mc.mc_command(["ls", "_healthcheck"], timeout=10)
        if output is not None:
            g.ok("凭证验证", "Access Key / Secret Key 认证成功 (via mc)")
        else:
            g.error("凭证验证", "认证失败，请检查 Access Key / Secret Key")
    else:
        g.warn("凭证验证", "minio SDK 和 mc CLI 均不可用，无法验证凭证")


def _check_users_policies(mc, g):
    """检查用户和策略列表 (需要 mc admin)。"""
    if not mc.mc_available():
        return

    # 用户列表
    output = mc.mc_command(["admin", "user", "list", "_healthcheck"], timeout=10)
    if output is not None:
        users = [l.strip() for l in output.strip().splitlines() if l.strip()]
        if users:
            enabled = sum(1 for u in users if "enable" in u.lower())
            g.ok("用户列表", f"共 {len(users)} 个用户",
                 detail=output.strip()[:500])
        else:
            g.ok("用户列表", "无额外用户 (仅 root)")
    else:
        # mc admin 可能无权限
        pass

    # 策略列表
    output = mc.mc_command(["admin", "policy", "list", "_healthcheck"], timeout=10)
    if output is not None:
        policies = [l.strip() for l in output.strip().splitlines() if l.strip()]
        if policies:
            g.ok("策略列表", f"共 {len(policies)} 个策略",
                 detail=", ".join(policies[:20]))


def _check_iam_oidc(mc, g):
    """检查外部 IAM / OIDC 配置。"""
    if not mc.mc_available():
        return

    # 检查 OIDC 配置
    output = mc.mc_command(
        ["admin", "config", "get", "_healthcheck", "identity_openid"],
        timeout=10)
    if output and "config_url" in output.lower():
        # 有 OIDC 配置
        if "error" in output.lower():
            g.warn("OIDC 对接", "OIDC 已配置但可能存在错误",
                   detail=output.strip()[:300])
        else:
            g.ok("OIDC 对接", "已配置 OIDC")

    # 检查 LDAP 配置
    output = mc.mc_command(
        ["admin", "config", "get", "_healthcheck", "identity_ldap"],
        timeout=10)
    if output and "server_addr" in output.lower():
        if "error" in output.lower():
            g.warn("LDAP 对接", "LDAP 已配置但可能存在错误")
        else:
            g.ok("LDAP 对接", "已配置 LDAP")
