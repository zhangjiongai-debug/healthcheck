"""2.6 Service / Endpoint / Ingress 检查。"""

from datetime import datetime, timezone
from kubernetes import client as kclient
from ..result import CheckGroup, Severity


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.6 Service / Endpoint / Ingress 检查")
    core: kclient.CoreV1Api = clients["core"]
    networking: kclient.NetworkingV1Api = clients["networking"]

    # ━━━━━ Service & Endpoints ━━━━━
    try:
        services = core.list_service_for_all_namespaces()
        empty_ep_svcs = []
        notready_ep_svcs = []

        for svc in services.items:
            fqn = f"{svc.metadata.namespace}/{svc.metadata.name}"
            # 跳过无 selector 的 Service (如 ExternalName 或手动管理)
            if not svc.spec.selector:
                continue
            # 跳过 headless without selector
            if svc.spec.cluster_ip == "None":
                continue

            try:
                ep = core.read_namespaced_endpoints(svc.metadata.name, svc.metadata.namespace)
                addresses = []
                not_ready = []
                for subset in (ep.subsets or []):
                    addresses.extend(subset.addresses or [])
                    not_ready.extend(subset.not_ready_addresses or [])

                if not addresses and not not_ready:
                    empty_ep_svcs.append(fqn)
                elif not_ready:
                    notready_ep_svcs.append(f"{fqn}: {len(not_ready)} NotReady")
            except Exception:
                empty_ep_svcs.append(f"{fqn} (无法获取 Endpoint)")

        if not empty_ep_svcs:
            g.ok("Service Endpoints", f"共 {len(services.items)} 个 Service，无空 Endpoint")
        else:
            g.error("Service Endpoints 为空", f"{len(empty_ep_svcs)} 个 Service 后端为空",
                    detail="\n".join(empty_ep_svcs[:20]))

        if notready_ep_svcs:
            g.warn("Endpoint NotReady", f"{len(notready_ep_svcs)} 个 Service 有 NotReady 后端",
                   detail="\n".join(notready_ep_svcs[:20]))

    except Exception as e:
        g.error("Service/Endpoint", f"检查失败: {e}")

    # ━━━━━ Ingress ━━━━━
    try:
        ingresses = networking.list_ingress_for_all_namespaces()
        ingress_issues = []

        for ing in ingresses.items:
            fqn = f"{ing.metadata.namespace}/{ing.metadata.name}"

            # 检查 TLS Secret
            for tls in (ing.spec.tls or []):
                if tls.secret_name:
                    try:
                        secret = core.read_namespaced_secret(tls.secret_name, ing.metadata.namespace)
                        # 检查证书过期
                        cert_data = secret.data.get("tls.crt")
                        if cert_data:
                            _check_cert_expiry(g, fqn, tls.secret_name, cert_data)
                    except kclient.ApiException as e:
                        if e.status == 404:
                            ingress_issues.append(f"{fqn}: TLS Secret '{tls.secret_name}' 不存在")

            # 检查后端 Service 是否存在
            for rule in (ing.spec.rules or []):
                if rule.http:
                    for path in rule.http.paths:
                        backend_svc = path.backend.service
                        if backend_svc:
                            try:
                                core.read_namespaced_service(
                                    backend_svc.name, ing.metadata.namespace)
                            except kclient.ApiException as e:
                                if e.status == 404:
                                    ingress_issues.append(
                                        f"{fqn}: 后端 Service '{backend_svc.name}' 不存在")

        if not ingress_issues:
            g.ok("Ingress", f"共 {len(ingresses.items)} 个 Ingress，配置正常")
        else:
            g.error("Ingress", f"{len(ingress_issues)} 个问题",
                    detail="\n".join(ingress_issues[:20]))

    except Exception as e:
        g.error("Ingress", f"检查失败: {e}")

    return g


def _check_cert_expiry(g: CheckGroup, ingress_fqn: str, secret_name: str, cert_b64: str):
    """检查 TLS 证书过期时间。"""
    try:
        import base64
        from datetime import datetime, timezone

        # 尝试用 cryptography 库解析
        try:
            from cryptography import x509
            cert_pem = base64.b64decode(cert_b64)
            cert = x509.load_pem_x509_certificate(cert_pem)
            expiry = cert.not_valid_after_utc
            days_left = (expiry - datetime.now(timezone.utc)).days
            if days_left < 0:
                g.error("TLS 证书过期", f"{ingress_fqn} ({secret_name}): 已过期 {-days_left} 天")
            elif days_left < 30:
                g.warn("TLS 证书即将过期", f"{ingress_fqn} ({secret_name}): 剩余 {days_left} 天")
        except ImportError:
            pass  # 没有 cryptography 库就跳过
    except Exception:
        pass
