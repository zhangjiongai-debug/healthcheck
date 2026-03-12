"""4. Sidekiq / 后台任务检查。

- Sidekiq 队列是否堆积
- 失败任务是否增多
- 任务处理延迟是否异常
- 邮件/通知/导入导出等后台任务是否正常
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4. Sidekiq / 后台任务")
    gl = ctx["gl"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── Sidekiq 综合指标 ──
    _check_sidekiq_metrics(gl, g)

    # ── 队列详情 ──
    _check_queue_metrics(gl, g)

    # ── 进程信息 ──
    _check_process_metrics(gl, g)

    # ── K8s Pod 检查 ──
    if mode == DeployMode.K8S:
        _check_sidekiq_k8s(ctx, g)

    return g


def _check_sidekiq_metrics(gl, g):
    """检查 Sidekiq 综合指标。"""
    resp = gl.api_v4("/sidekiq/compound_metrics")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        body = resp["body"]

        # 队列
        queues = body.get("queues", {})
        if isinstance(queues, dict):
            q_info = queues.get("queues", [])
            if isinstance(q_info, list):
                total_size = sum(q.get("size", 0) for q in q_info)
                total_latency = max((q.get("latency", 0) for q in q_info), default=0)

                if total_size > 1000:
                    g.error("队列积压", f"总计 {total_size} 个任务排队")
                elif total_size > 100:
                    g.warn("队列积压", f"总计 {total_size} 个任务排队")
                else:
                    g.ok("队列状态", f"{total_size} 个任务排队")

                if total_latency > 300:  # 5 分钟
                    g.error("队列延迟", f"最大延迟 {total_latency:.0f}s")
                elif total_latency > 60:
                    g.warn("队列延迟", f"最大延迟 {total_latency:.0f}s")
                elif total_latency > 0:
                    g.ok("队列延迟", f"最大延迟 {total_latency:.1f}s")

        # 作业
        jobs = body.get("jobs", {})
        if isinstance(jobs, dict):
            processed = jobs.get("processed", 0)
            failed = jobs.get("failed", 0)
            if processed > 0:
                fail_rate = (failed / processed) * 100 if processed > 0 else 0
                if fail_rate > 10:
                    g.warn("任务失败率",
                           f"{fail_rate:.1f}% (处理: {processed}, 失败: {failed})")
                else:
                    g.ok("任务统计",
                         f"已处理: {processed}, 失败: {failed}")
            elif failed > 0:
                g.warn("任务统计", f"失败: {failed}, 已处理: {processed}")

        # 进程
        processes = body.get("processes", {})
        if isinstance(processes, dict):
            proc_list = processes.get("processes", [])
            if isinstance(proc_list, list) and proc_list:
                total_busy = sum(p.get("busy", 0) for p in proc_list)
                total_concurrency = sum(p.get("concurrency", 0) for p in proc_list)
                g.ok("Sidekiq 进程",
                     f"{len(proc_list)} 个进程, "
                     f"繁忙: {total_busy}/{total_concurrency}")

        return

    if resp["status"] == 401:
        g.warn("Sidekiq 指标", "需要 Token 才能访问 Sidekiq API (--token)")
    elif resp["status"] == 403:
        g.warn("Sidekiq 指标", "需要管理员 Token 才能访问 Sidekiq API")
    else:
        g.warn("Sidekiq 指标", f"API 不可用 (status={resp['status']})")


def _check_queue_metrics(gl, g):
    """检查各队列详细信息。"""
    resp = gl.api_v4("/sidekiq/queue_metrics")
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    queues = resp["body"].get("queues", {})
    if not isinstance(queues, dict):
        return

    # 找出有积压的队列
    backlog_queues = []
    for name, info in queues.items():
        if not isinstance(info, dict):
            continue
        size = info.get("size", 0)
        latency = info.get("latency", 0)
        if size > 50 or latency > 60:
            backlog_queues.append(f"{name}: size={size}, latency={latency:.0f}s")

    if backlog_queues:
        g.warn("积压队列", f"{len(backlog_queues)} 个队列有积压",
               detail="\n".join(backlog_queues[:10]))


def _check_process_metrics(gl, g):
    """检查 Sidekiq 进程详情。"""
    resp = gl.api_v4("/sidekiq/process_metrics")
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        return

    processes = resp["body"].get("processes", [])
    if not isinstance(processes, list) or not processes:
        return

    for proc in processes:
        if not isinstance(proc, dict):
            continue
        hostname = proc.get("hostname", "?")
        busy = proc.get("busy", 0)
        concurrency = proc.get("concurrency", 0)
        queues = proc.get("queues", [])

        if concurrency > 0 and busy >= concurrency:
            g.warn(f"Sidekiq [{hostname}]",
                   f"满负荷: {busy}/{concurrency}, 队列: {len(queues)}")


def _check_sidekiq_k8s(ctx, g):
    """K8s: 检查 Sidekiq Deployment / Pod。"""
    k8s_core = ctx.get("k8s_core")
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    try:
        pods = k8s_core.list_namespaced_pod(
            ns, label_selector="app=sidekiq")
        for pod in pods.items:
            if pod.status.phase == "Succeeded":
                continue
            pod_name = pod.metadata.name
            phase = pod.status.phase
            conditions = {c.type: c.status for c in (pod.status.conditions or [])}
            if phase == "Running" and conditions.get("Ready") == "True":
                # 检查重启
                for cs in (pod.status.container_statuses or []):
                    if cs.restart_count > 5:
                        g.warn(f"Sidekiq {pod_name}/{cs.name}",
                               f"重启次数: {cs.restart_count}")
            elif phase == "Running":
                g.warn(f"Sidekiq Pod {pod_name}", "Running 但未 Ready")
            else:
                g.error(f"Sidekiq Pod {pod_name}", f"阶段: {phase}")
    except Exception:
        pass
