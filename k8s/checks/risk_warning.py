"""2.12 风险预警类检查 —— 不仅看"已故障"，还看"快故障"。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity
from ..client import safe_call


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.12 风险预警类检查")
    core: kclient.CoreV1Api = clients["core"]
    apps: kclient.AppsV1Api = clients["apps"]

    # ── 单副本关键服务 ──
    try:
        deploys = apps.list_deployment_for_all_namespaces()
        single_replica = []
        latest_tag = []

        for dep in deploys.items:
            fqn = f"{dep.metadata.namespace}/{dep.metadata.name}"
            replicas = dep.spec.replicas or 0
            if replicas == 1:
                single_replica.append(fqn)

            # 检查 latest tag
            for c in dep.spec.template.spec.containers:
                if c.image and (c.image.endswith(":latest") or ":" not in c.image.rsplit("/", 1)[-1]):
                    latest_tag.append(f"{fqn}: {c.image}")

        if single_replica:
            g.warn("单副本 Deployment", f"{len(single_replica)} 个 Deployment 仅 1 副本，存在单点风险",
                   detail="\n".join(single_replica[:20]))
        else:
            g.ok("单副本 Deployment", "无单副本 Deployment")

        if latest_tag:
            g.warn("latest 镜像标签", f"{len(latest_tag)} 个容器使用 latest 或无标签",
                   detail="\n".join(latest_tag[:20]))
        else:
            g.ok("镜像标签", "无容器使用 latest 标签")
    except Exception as e:
        g.warn("Deployment 风险", f"检查失败: {e}")

    # ── PodDisruptionBudget 检查 ──
    try:
        policy: kclient.PolicyV1Api = clients["policy"]
        pdbs = policy.list_pod_disruption_budget_for_all_namespaces()
        pdb_selectors = set()
        for pdb in pdbs.items:
            if pdb.spec.selector and pdb.spec.selector.match_labels:
                labels = ",".join(f"{k}={v}" for k, v in pdb.spec.selector.match_labels.items())
                pdb_selectors.add(f"{pdb.metadata.namespace}:{labels}")

        # 检查多副本 Deployment 是否有 PDB
        no_pdb = []
        for dep in deploys.items:
            replicas = dep.spec.replicas or 0
            if replicas <= 1:
                continue
            fqn = f"{dep.metadata.namespace}/{dep.metadata.name}"
            labels = dep.spec.selector.match_labels or {}
            dep_label_str = f"{dep.metadata.namespace}:" + ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            if dep_label_str not in pdb_selectors:
                no_pdb.append(fqn)

        if no_pdb:
            g.warn("PDB 缺失", f"{len(no_pdb)} 个多副本 Deployment 无 PDB",
                   detail="\n".join(no_pdb[:20]))
        else:
            g.ok("PDB", "所有多副本 Deployment 均有 PDB")
    except Exception as e:
        g.warn("PDB", f"检查失败: {e}")

    # ── Pod 重启次数持续上升 ──
    try:
        pods = core.list_pod_for_all_namespaces()
        high_restarts = []
        for pod in pods.items:
            for cs in (pod.status.container_statuses or []):
                if cs.restart_count >= 50:
                    high_restarts.append(
                        f"{pod.metadata.namespace}/{pod.metadata.name} [{cs.name}]: "
                        f"重启 {cs.restart_count} 次"
                    )

        if high_restarts:
            g.error("高频重启 Pod", f"{len(high_restarts)} 个容器重启超 50 次",
                    detail="\n".join(high_restarts[:20]))
        else:
            g.ok("Pod 重启趋势", "无高频重启容器")
    except Exception as e:
        g.warn("Pod 重启趋势", f"检查失败: {e}")

    # ── Endpoint 数量为 0 的核心 Service ──
    try:
        services = core.list_service_for_all_namespaces()
        zero_ep_svcs = []
        for svc in services.items:
            if not svc.spec.selector:
                continue
            fqn = f"{svc.metadata.namespace}/{svc.metadata.name}"
            try:
                ep = core.read_namespaced_endpoints(svc.metadata.name, svc.metadata.namespace)
                total_addrs = sum(
                    len(subset.addresses or []) for subset in (ep.subsets or [])
                )
                if total_addrs == 0:
                    zero_ep_svcs.append(fqn)
            except Exception:
                pass

        if zero_ep_svcs:
            g.error("Service 无后端", f"{len(zero_ep_svcs)} 个 Service Endpoint 为空",
                    detail="\n".join(zero_ep_svcs[:20]))
        else:
            g.ok("Service 后端", "所有 Service 均有后端 Endpoint")
    except Exception as e:
        g.warn("Service 后端", f"检查失败: {e}")

    # ── 反亲和策略检查 ──
    try:
        no_anti_affinity = []
        for dep in deploys.items:
            replicas = dep.spec.replicas or 0
            if replicas <= 1:
                continue
            fqn = f"{dep.metadata.namespace}/{dep.metadata.name}"
            affinity = dep.spec.template.spec.affinity
            has_pod_anti = False
            if affinity and affinity.pod_anti_affinity:
                paa = affinity.pod_anti_affinity
                if paa.required_during_scheduling_ignored_during_execution or \
                   paa.preferred_during_scheduling_ignored_during_execution:
                    has_pod_anti = True

            tsc = dep.spec.template.spec.topology_spread_constraints
            if tsc:
                has_pod_anti = True

            if not has_pod_anti:
                no_anti_affinity.append(fqn)

        if no_anti_affinity:
            g.warn("反亲和策略", f"{len(no_anti_affinity)} 个多副本 Deployment 无反亲和/拓扑分布",
                   detail="\n".join(no_anti_affinity[:20]))
        else:
            g.ok("反亲和策略", "多副本 Deployment 均已配置反亲和或拓扑分布")
    except Exception as e:
        g.warn("反亲和策略", f"检查失败: {e}")

    return g
