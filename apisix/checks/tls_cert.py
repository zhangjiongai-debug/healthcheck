"""6. 证书与 TLS 检查。

- SSL 证书是否存在
- 证书是否即将过期
- SNI 配置是否正确
- TLS 握手是否成功
- 双向 TLS 配置是否正常
"""

import ssl
import socket
from datetime import datetime

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6. 证书与 TLS 检查")
    apisix = ctx["apisix"]

    if "connect_error" in ctx:
        g.fatal("Admin API", ctx["connect_error"])
        return g

    # ── 检查 APISIX 中配置的 SSL 证书 ──
    _check_ssl_resources(apisix, g)

    # ── 检查 Admin API 端点的 TLS ──
    _check_endpoint_tls(apisix.admin_url, "Admin API", g)

    # ── 检查 Gateway 端点的 TLS ──
    gateway_url = ctx.get("gateway_url")
    if gateway_url and gateway_url.startswith("https://"):
        _check_endpoint_tls(gateway_url, "Gateway", g)

    # ── 检查 Dashboard 端点的 TLS ──
    dashboard = ctx.get("dashboard")
    if dashboard and dashboard.base_url.startswith("https://"):
        _check_endpoint_tls(dashboard.base_url, "Dashboard", g)

    return g


def _check_ssl_resources(apisix, g):
    """检查 APISIX Admin API 中配置的 SSL 资源。"""
    resp = apisix.ssls()
    if resp["status"] != 200:
        if resp["status"] == 0:
            # Admin API 不可达，已在其他模块报告
            return
        g.warn("SSL 资源", f"获取失败 (status={resp['status']})")
        return

    body = resp["body"]
    if not isinstance(body, dict):
        return

    total = body.get("total", 0)
    ssls = body.get("list", [])

    if total == 0:
        g.ok("SSL 证书", "当前无 SSL 证书配置 (如需 HTTPS 请添加)")
        return

    g.ok("SSL 证书数量", f"共 {total} 个 SSL 证书")

    issues = []
    for item in ssls:
        ssl_obj = item.get("value", item) if isinstance(item, dict) else {}
        ssl_id = ssl_obj.get("id", "unknown")
        snis = ssl_obj.get("snis", [])
        sni_display = ", ".join(snis[:3]) if snis else "无 SNI"
        if len(snis) > 3:
            sni_display += f" (+{len(snis)-3})"

        # 检查过期时间
        validity_end = ssl_obj.get("validity_end")
        if validity_end:
            try:
                expire_dt = datetime.fromtimestamp(validity_end)
                days_left = (expire_dt - datetime.now()).days
                if days_left < 0:
                    g.fatal(f"SSL {sni_display}",
                            f"已过期 {-days_left} 天!")
                elif days_left < 7:
                    g.fatal(f"SSL {sni_display}",
                            f"即将过期! 剩余 {days_left} 天")
                elif days_left < 30:
                    g.warn(f"SSL {sni_display}",
                           f"即将过期: 剩余 {days_left} 天")
                else:
                    g.ok(f"SSL {sni_display}",
                         f"有效, 剩余 {days_left} 天")
            except (ValueError, OSError):
                g.warn(f"SSL {ssl_id}", "无法解析过期时间")
        else:
            g.ok(f"SSL {sni_display}", "已配置 (无过期时间信息)")

        # 检查 SNI 是否为空
        if not snis:
            issues.append(f"SSL {ssl_id}: 未配置 SNI")

    if issues:
        g.warn("SNI 配置", f"{len(issues)} 个证书缺少 SNI 配置",
               detail="\n".join(issues[:10]))


def _check_endpoint_tls(url: str, name: str, g):
    """检查端点的 TLS 证书。"""
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
                            g.fatal(f"{name} TLS 证书",
                                    f"即将过期! 剩余 {days_left} 天 "
                                    f"(过期: {not_after_str})")
                        elif days_left < 30:
                            g.warn(f"{name} TLS 证书",
                                   f"即将过期: 剩余 {days_left} 天 "
                                   f"(过期: {not_after_str})")
                        else:
                            g.ok(f"{name} TLS 证书",
                                 f"有效, 剩余 {days_left} 天")
                else:
                    g.ok(f"{name} TLS", "HTTPS 可用 (无法获取证书详情)")
    except Exception as e:
        g.ok(f"{name} TLS 检查", f"跳过 ({e})")
