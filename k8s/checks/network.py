"""2.10 网络与访问检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.10 网络与访问检查")
    core: kclient.CoreV1Api = clients["core"]
    networking: kclient.NetworkingV1Api = clients["networking"]

    # ━━━━━ DNS 服务检查 ━━━━━
    try:
        dns_svc = core.read_namespaced_service("kube-dns", "kube-system")
        ep = core.read_namespaced_endpoints("kube-dns", "kube-system")
        addresses = []
        for subset in (ep.subsets or []):
            addresses.extend(subset.addresses or [])
        if addresses:
            g.ok("DNS 服务", f"kube-dns 有 {len(addresses)} 个后端")
        else:
            g.error("DNS 服务", "kube-dns Endpoint 为空，DNS 不可用")
    except kclient.ApiException as e:
        if e.status == 404:
            g.warn("DNS 服务", "未找到 kube-dns Service")
        else:
            g.error("DNS 服务", f"检查失败: {e}")
    except Exception as e:
        g.error("DNS 服务", f"检查失败: {e}")

    # ━━━━━ NetworkPolicy 检查 ━━━━━
    try:
        np_list = networking.list_network_policy_for_all_namespaces()
        if not np_list.items:
            g.ok("NetworkPolicy", "集群中无 NetworkPolicy")
        else:
            # 检查是否有 deny-all 策略
            deny_all_ns = []
            for np in np_list.items:
                # 空 podSelector + 无 ingress/egress 规则 = deny all
                selector = np.spec.pod_selector
                if (selector and not selector.match_labels and not selector.match_expressions):
                    policy_types = np.spec.policy_types or []
                    if "Ingress" in policy_types and not np.spec.ingress:
                        deny_all_ns.append(f"{np.metadata.namespace}/{np.metadata.name} (deny-all ingress)")
                    if "Egress" in policy_types and not np.spec.egress:
                        deny_all_ns.append(f"{np.metadata.namespace}/{np.metadata.name} (deny-all egress)")

            if deny_all_ns:
                g.warn("NetworkPolicy", f"存在 deny-all 策略，注意是否影响业务",
                       detail="\n".join(deny_all_ns[:20]))
            else:
                g.ok("NetworkPolicy", f"共 {len(np_list.items)} 条策略")
    except Exception as e:
        g.warn("NetworkPolicy", f"检查失败: {e}")

    # ━━━━━ LoadBalancer Service 检查 ━━━━━
    try:
        services = core.list_service_for_all_namespaces()
        lb_issues = []
        for svc in services.items:
            if svc.spec.type == "LoadBalancer":
                fqn = f"{svc.metadata.namespace}/{svc.metadata.name}"
                ingress_list = svc.status.load_balancer.ingress if svc.status.load_balancer else None
                if not ingress_list:
                    lb_issues.append(f"{fqn}: 无外部 IP/Hostname")

        if not lb_issues:
            lb_count = sum(1 for s in services.items if s.spec.type == "LoadBalancer")
            if lb_count > 0:
                g.ok("LoadBalancer", f"共 {lb_count} 个 LoadBalancer Service，均已分配地址")
            else:
                g.ok("LoadBalancer", "集群中无 LoadBalancer Service")
        else:
            g.warn("LoadBalancer", f"{len(lb_issues)} 个 Service 未分配外部地址",
                   detail="\n".join(lb_issues[:20]))
    except Exception as e:
        g.warn("LoadBalancer", f"检查失败: {e}")

    return g
