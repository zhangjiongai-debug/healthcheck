"""4.6 证书与安全检查。

- TLS 是否正常
- 证书是否即将过期
- 管理端口是否暴露过多
- 默认管理员密码是否未改
- 高危配置是否开启
"""

import ssl
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4.6 证书与安全检查")
    kc = ctx["kc"]
    mode = ctx["mode"]
    base_url = ctx["base_url"]

    # ── TLS 证书检查 ──
    _check_tls(base_url, g)

    # ── 默认管理员密码检查 ──
    _check_default_password(kc, g)

    # ── 高危配置检查 ──
    _check_dangerous_config(kc, g)

    # ── K8s 环境: 检查 Service 暴露 ──
    if mode == DeployMode.K8S:
        _check_k8s_exposure(ctx, g)

    return g


def _check_tls(base_url: str, g: CheckGroup):
    """检查 TLS 证书状态及过期时间。"""
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        g.warn("TLS", f"使用 HTTP 而非 HTTPS ({base_url})")
        return

    hostname = parsed.hostname
    port = parsed.port or 443

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()

        if not cert:
            g.error("TLS 证书", "无法获取证书信息")
            return

        # 过期时间
        not_after_str = cert.get("notAfter", "")
        if not_after_str:
            not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z")
            not_after = not_after.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_left = (not_after - now).days

            if days_left < 0:
                g.fatal("TLS 证书", f"已过期 ({abs(days_left)} 天前)")
            elif days_left < 7:
                g.error("TLS 证书", f"即将过期 (剩余 {days_left} 天)")
            elif days_left < 30:
                g.warn("TLS 证书", f"将在 {days_left} 天后过期")
            else:
                g.ok("TLS 证书", f"有效 (剩余 {days_left} 天)")
        else:
            g.warn("TLS 证书", "无法解析过期时间")

        # 颁发者
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        subject = dict(x[0] for x in cert.get("subject", ()))
        cn = subject.get("commonName", "?")
        issuer_cn = issuer.get("commonName", "?")
        g.ok("TLS 证书信息", f"CN={cn}, Issuer={issuer_cn}")

    except ssl.SSLCertVerificationError as e:
        g.error("TLS 证书", f"验证失败: {e}")
    except ssl.SSLError as e:
        g.error("TLS", f"SSL 错误: {e}")
    except socket.timeout:
        g.error("TLS", f"连接超时 ({hostname}:{port})")
    except Exception as e:
        g.warn("TLS", f"检查失败: {e}")


def _check_default_password(kc, g: CheckGroup):
    """检测是否使用默认管理员密码。"""
    if not kc.admin_user or not kc.admin_password:
        return

    default_passwords = ["admin", "password", "keycloak", "changeme", "Pa55w0rd", "123456"]
    if kc.admin_password in default_passwords:
        g.error("默认密码", f"管理员 [{kc.admin_user}] 使用了常见默认密码，请立即修改!")
    else:
        g.ok("密码安全", "管理员未使用常见默认密码")


def _check_dangerous_config(kc, g: CheckGroup):
    """通过 Admin API 检查高危配置。"""
    token = kc.get_admin_token()
    if not token:
        g.warn("安全配置", "无法获取 admin token，跳过配置检查")
        return

    # 检查 master realm 的安全设置
    resp = kc.admin_get("/admin/realms/master")
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    realm = resp["body"]

    # 暴力破解保护
    brute_force = realm.get("bruteForceProtected", False)
    if not brute_force:
        g.warn("暴力破解保护", "master realm 未启用暴力破解保护")
    else:
        g.ok("暴力破解保护", "master realm 已启用")

    # SSL 要求
    ssl_required = realm.get("sslRequired", "none")
    if ssl_required == "none":
        g.warn("SSL 要求", f"master realm sslRequired={ssl_required}，建议设置为 external 或 all")
    else:
        g.ok("SSL 要求", f"master realm sslRequired={ssl_required}")

    # 注册是否开放
    reg_allowed = realm.get("registrationAllowed", False)
    if reg_allowed:
        g.warn("开放注册", "master realm 允许用户自行注册，存在安全风险")
    else:
        g.ok("开放注册", "master realm 未开放自行注册")

    # 检查所有 realm 的通配符 redirect URI
    realms_resp = kc.admin_get("/admin/realms")
    if realms_resp["status"] == 200 and isinstance(realms_resp["body"], list):
        for r in realms_resp["body"]:
            rname = r.get("realm", "?")
            clients_resp = kc.admin_get(f"/admin/realms/{rname}/clients?max=500")
            if clients_resp["status"] == 200 and isinstance(clients_resp["body"], list):
                for c in clients_resp["body"]:
                    cid = c.get("clientId", "?")
                    uris = c.get("redirectUris", [])
                    if "*" in uris:
                        g.warn(f"通配符 URI [{rname}/{cid}]",
                               "redirect URI 包含 '*'，存在开放重定向风险")
                    web_origins = c.get("webOrigins", [])
                    if "*" in web_origins:
                        g.warn(f"通配符 Origin [{rname}/{cid}]",
                               "webOrigins 包含 '*'，存在 CORS 风险")


def _check_k8s_exposure(ctx: dict, g: CheckGroup):
    """检查 K8s 中 Keycloak 的网络暴露情况。"""
    k8s_core = ctx.get("k8s_core")
    if not k8s_core:
        return

    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        services = k8s_core.list_namespaced_service(ns, label_selector=selector)
    except Exception:
        return

    for svc in services.items:
        svc_name = svc.metadata.name
        svc_type = svc.spec.type

        if svc_type == "NodePort":
            ports = [f"{p.port}→{p.node_port}" for p in (svc.spec.ports or []) if p.node_port]
            g.warn(f"Service {svc_name}", f"NodePort 类型，端口暴露: {', '.join(ports)}")
        elif svc_type == "LoadBalancer":
            g.warn(f"Service {svc_name}", "LoadBalancer 类型，管理端口可能直接暴露到公网")
        else:
            g.ok(f"Service {svc_name}", f"类型: {svc_type}")

        # 检查管理端口是否额外暴露
        for port in (svc.spec.ports or []):
            if port.port in (9990, 8443, 9000) and svc_type in ("NodePort", "LoadBalancer"):
                g.warn(f"管理端口 {port.port}", f"管理端口通过 {svc_type} 暴露")
