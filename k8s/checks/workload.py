"""2.5 工作负载资源检查 (Pod / Deployment / StatefulSet / DaemonSet / Job / CronJob)。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity

_BAD_PHASES = {"Pending", "Failed", "Unknown"}
_BAD_REASONS = {
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "CreateContainerConfigError", "CreateContainerError",
    "OOMKilled", "ContainerStatusUnknown",
}

_RESTART_THRESHOLD = 10


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.5 工作负载资源检查")
    core: kclient.CoreV1Api = clients["core"]
    apps: kclient.AppsV1Api = clients["apps"]
    batch: kclient.BatchV1Api = clients["batch"]

    # ━━━━━ 2.5.1 Pod 检查 ━━━━━
    try:
        pods = core.list_pod_for_all_namespaces()
    except Exception as e:
        g.fatal("Pod 列表", f"获取失败: {e}")
        return g

    bad_pods = []
    high_restart_pods = []
    total_pods = len(pods.items)

    for pod in pods.items:
        ns = pod.metadata.namespace
        name = pod.metadata.name
        fqn = f"{ns}/{name}"
        phase = pod.status.phase or "Unknown"

        # phase 异常
        if phase in _BAD_PHASES and phase != "Pending":
            bad_pods.append(f"{fqn}: phase={phase}")
            continue

        # container status 异常
        for cs in (pod.status.container_statuses or []):
            # 重启次数
            if cs.restart_count >= _RESTART_THRESHOLD:
                high_restart_pods.append(f"{fqn} [{cs.name}]: 重启 {cs.restart_count} 次")

            # waiting reason
            if cs.state and cs.state.waiting:
                reason = cs.state.waiting.reason or ""
                if reason in _BAD_REASONS:
                    bad_pods.append(f"{fqn} [{cs.name}]: {reason}")

            # terminated OOMKilled
            if cs.state and cs.state.terminated:
                reason = cs.state.terminated.reason or ""
                if reason == "OOMKilled":
                    bad_pods.append(f"{fqn} [{cs.name}]: OOMKilled")

        # init container 异常
        for cs in (pod.status.init_container_statuses or []):
            if cs.state and cs.state.waiting:
                reason = cs.state.waiting.reason or ""
                if reason in _BAD_REASONS:
                    bad_pods.append(f"{fqn} [init:{cs.name}]: {reason}")

    if not bad_pods:
        g.ok("Pod 状态", f"共 {total_pods} 个 Pod，无异常状态")
    else:
        g.error("Pod 状态", f"{len(bad_pods)} 个 Pod 异常",
                detail="\n".join(bad_pods[:30]))

    if not high_restart_pods:
        g.ok("Pod 重启", f"无容器重启超 {_RESTART_THRESHOLD} 次")
    else:
        g.warn("Pod 重启", f"{len(high_restart_pods)} 个容器重启次数过多",
               detail="\n".join(high_restart_pods[:20]))

    # ━━━━━ 2.5.2 Deployment 检查 ━━━━━
    try:
        deploys = apps.list_deployment_for_all_namespaces()
        deploy_issues = []
        for dep in deploys.items:
            fqn = f"{dep.metadata.namespace}/{dep.metadata.name}"
            desired = dep.spec.replicas or 0
            available = dep.status.available_replicas or 0
            ready = dep.status.ready_replicas or 0
            unavailable = dep.status.unavailable_replicas or 0

            if unavailable > 0 or ready < desired:
                deploy_issues.append(
                    f"{fqn}: desired={desired} ready={ready} available={available} unavailable={unavailable}"
                )

            # rollout 是否卡住
            for cond in (dep.status.conditions or []):
                if cond.type == "Progressing" and cond.status == "False":
                    deploy_issues.append(f"{fqn}: rollout 卡住 - {cond.message}")

        if not deploy_issues:
            g.ok("Deployment", f"共 {len(deploys.items)} 个，全部正常")
        else:
            g.error("Deployment", f"{len(deploy_issues)} 个异常",
                    detail="\n".join(deploy_issues[:20]))
    except Exception as e:
        g.error("Deployment", f"检查失败: {e}")

    # ━━━━━ 2.5.3 StatefulSet 检查 ━━━━━
    try:
        stss = apps.list_stateful_set_for_all_namespaces()
        sts_issues = []
        for sts in stss.items:
            fqn = f"{sts.metadata.namespace}/{sts.metadata.name}"
            desired = sts.spec.replicas or 0
            ready = sts.status.ready_replicas or 0
            if ready < desired:
                sts_issues.append(f"{fqn}: desired={desired} ready={ready}")

        if not sts_issues:
            g.ok("StatefulSet", f"共 {len(stss.items)} 个，全部正常")
        else:
            g.error("StatefulSet", f"{len(sts_issues)} 个副本不足",
                    detail="\n".join(sts_issues[:20]))
    except Exception as e:
        g.error("StatefulSet", f"检查失败: {e}")

    # ━━━━━ 2.5.4 DaemonSet 检查 ━━━━━
    try:
        dss = apps.list_daemon_set_for_all_namespaces()
        ds_issues = []
        for ds in dss.items:
            fqn = f"{ds.metadata.namespace}/{ds.metadata.name}"
            desired = ds.status.desired_number_scheduled or 0
            ready = ds.status.number_ready or 0
            if ready < desired:
                ds_issues.append(f"{fqn}: desired={desired} ready={ready}")

        if not ds_issues:
            g.ok("DaemonSet", f"共 {len(dss.items)} 个，全部就绪")
        else:
            g.error("DaemonSet", f"{len(ds_issues)} 个未全量就绪",
                    detail="\n".join(ds_issues[:20]))
    except Exception as e:
        g.error("DaemonSet", f"检查失败: {e}")

    # ━━━━━ 2.5.5 Job / CronJob 检查 ━━━━━
    try:
        jobs = batch.list_job_for_all_namespaces()
        failed_jobs = []
        for job in jobs.items:
            fqn = f"{job.metadata.namespace}/{job.metadata.name}"
            for cond in (job.status.conditions or []):
                if cond.type == "Failed" and cond.status == "True":
                    failed_jobs.append(f"{fqn}: {cond.reason} - {cond.message}")
                    break

        if not failed_jobs:
            g.ok("Job", f"共 {len(jobs.items)} 个，无失败")
        else:
            g.warn("Job", f"{len(failed_jobs)} 个失败",
                   detail="\n".join(failed_jobs[:20]))
    except Exception as e:
        g.error("Job", f"检查失败: {e}")

    try:
        cronjobs = batch.list_cron_job_for_all_namespaces()
        cj_issues = []
        for cj in cronjobs.items:
            fqn = f"{cj.metadata.namespace}/{cj.metadata.name}"
            if cj.spec.suspend:
                cj_issues.append(f"{fqn}: 已暂停")
            if cj.status.last_schedule_time is None and cj.status.last_successful_time is None:
                cj_issues.append(f"{fqn}: 从未调度")

        if not cj_issues:
            g.ok("CronJob", f"共 {len(cronjobs.items)} 个，正常")
        else:
            g.warn("CronJob", f"{len(cj_issues)} 个需关注",
                   detail="\n".join(cj_issues[:20]))
    except Exception as e:
        g.error("CronJob", f"检查失败: {e}")

    return g
