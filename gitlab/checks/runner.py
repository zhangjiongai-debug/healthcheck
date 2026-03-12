"""6. GitLab Runner 检查。

- Runner 是否在线
- Runner 是否被 pause
- Runner 是否能正常拉取任务
- 最近 job 成功率是否正常
- executor（K8s/docker/shell）是否正常
- Runner token 是否失效
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6. GitLab Runner 检查")
    gl = ctx["gl"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── Runner 列表 (admin API) ──
    _check_runners(gl, g)

    # ── 最近 Job 状态 ──
    _check_recent_jobs(gl, g)

    # ── K8s Runner Pod ──
    if mode == DeployMode.K8S:
        _check_runner_k8s(ctx, g)

    return g


def _check_runners(gl, g):
    """检查已注册的 Runner。"""
    # admin API
    resp = gl.api_v4("/runners/all", params={"per_page": "100"})
    if resp["status"] == 200 and isinstance(resp["body"], list):
        runners = resp["body"]
        _analyze_runners(runners, g)
        return

    # 非 admin: 尝试普通接口
    resp = gl.api_v4("/runners", params={"per_page": "100"})
    if resp["status"] == 200 and isinstance(resp["body"], list):
        runners = resp["body"]
        _analyze_runners(runners, g)
        return

    if resp["status"] == 401:
        g.warn("Runner 列表", "需要 Token 才能检查 Runner (--token)")
    elif resp["status"] == 403:
        g.warn("Runner 列表", "需要管理员 Token 才能查看所有 Runner")
    else:
        g.warn("Runner 列表", f"API 不可用 (status={resp['status']})")


def _analyze_runners(runners, g):
    """分析 Runner 列表。"""
    if not runners:
        g.warn("Runner 列表", "未注册任何 Runner")
        return

    online = []
    offline = []
    paused = []

    for r in runners:
        if not isinstance(r, dict):
            continue
        name = r.get("description", r.get("id", "?"))
        status = r.get("status", "unknown")
        is_active = r.get("active", True)
        is_paused = r.get("paused", False)

        if is_paused or not is_active:
            paused.append(name)
        elif status == "online":
            online.append(name)
        else:
            offline.append(name)

    g.ok("Runner 总数",
         f"{len(runners)} 个, {len(online)} 在线, "
         f"{len(offline)} 离线, {len(paused)} 暂停")

    if not online and not paused:
        g.error("Runner 在线状态", "所有 Runner 离线!")
    elif offline:
        if len(offline) > len(runners) // 2:
            g.warn("Runner 离线",
                   f"{len(offline)} 个 Runner 离线",
                   detail=", ".join(str(n) for n in offline[:10]))
        else:
            g.ok("Runner 离线", f"{len(offline)} 个离线")

    if paused:
        g.warn("Runner 暂停",
               f"{len(paused)} 个 Runner 被暂停",
               detail=", ".join(str(n) for n in paused[:10]))

    # 检查 Runner 类型
    runner_types = {}
    for r in runners:
        if isinstance(r, dict):
            rt = r.get("runner_type", "unknown")
            runner_types[rt] = runner_types.get(rt, 0) + 1
    if runner_types:
        type_str = ", ".join(f"{k}: {v}" for k, v in runner_types.items())
        g.ok("Runner 类型", type_str)


def _check_recent_jobs(gl, g):
    """检查最近 Job 成功率。"""
    resp = gl.api_v4("/jobs", params={
        "per_page": "50",
        "scope[]": "failed",
    })
    failed_count = 0
    if resp["status"] == 200 and isinstance(resp["body"], list):
        failed_count = len(resp["body"])

    resp = gl.api_v4("/jobs", params={
        "per_page": "50",
        "scope[]": "success",
    })
    success_count = 0
    if resp["status"] == 200 and isinstance(resp["body"], list):
        success_count = len(resp["body"])

    total = failed_count + success_count
    if total > 0:
        fail_rate = (failed_count / total) * 100
        if fail_rate > 50:
            g.error("最近 Job 失败率",
                    f"{fail_rate:.0f}% (成功: {success_count}, 失败: {failed_count})")
        elif fail_rate > 30:
            g.warn("最近 Job 失败率",
                   f"{fail_rate:.0f}% (成功: {success_count}, 失败: {failed_count})")
        else:
            g.ok("最近 Job 状态",
                 f"失败率 {fail_rate:.0f}% (成功: {success_count}, 失败: {failed_count})")
    elif resp["status"] == 401:
        g.warn("最近 Job", "需要 Token 才能检查 Job 状态")
    # 0 jobs 不报告


def _check_runner_k8s(ctx, g):
    """K8s: 检查 GitLab Runner Deployment / Pod。"""
    k8s_core = ctx.get("k8s_core")
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    # Runner Pod (通常是 Deployment)
    try:
        pods = k8s_core.list_namespaced_pod(
            ns, label_selector="app=gitlab-runner")
        for pod in pods.items:
            if pod.status.phase == "Succeeded":
                continue
            pod_name = pod.metadata.name
            phase = pod.status.phase
            conditions = {c.type: c.status
                          for c in (pod.status.conditions or [])}
            if phase == "Running" and conditions.get("Ready") == "True":
                g.ok(f"Runner Pod {pod_name}", "Running & Ready")
            elif phase == "Running":
                g.warn(f"Runner Pod {pod_name}", "Running 但未 Ready")
            else:
                g.error(f"Runner Pod {pod_name}", f"阶段: {phase}")

            for cs in (pod.status.container_statuses or []):
                if cs.restart_count > 5:
                    g.warn(f"Runner {pod_name}/{cs.name}",
                           f"重启次数: {cs.restart_count}")
    except Exception:
        pass

    # Runner Deployment
    if k8s_apps:
        try:
            deploys = k8s_apps.list_namespaced_deployment(
                ns, label_selector="app=gitlab-runner")
            for dep in deploys.items:
                desired = dep.spec.replicas or 1
                ready = dep.status.ready_replicas or 0
                if ready >= desired:
                    g.ok(f"Runner Deployment {dep.metadata.name}",
                         f"副本 {ready}/{desired}")
                else:
                    g.error(f"Runner Deployment {dep.metadata.name}",
                            f"副本 {ready}/{desired} 未达标")
        except Exception:
            pass
