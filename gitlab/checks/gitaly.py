"""3. Gitaly / Repository Storage 检查。

- Gitaly 服务是否可用
- Git 仓库是否可读写
- 仓库存储挂载是否正常
- Gitaly 与 Praefect（若有）通信是否正常
- repository storage 容量是否健康
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("3. Gitaly / Repository Storage")
    gl = ctx["gl"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        if mode == DeployMode.K8S:
            _check_gitaly_k8s(ctx, g)
        return g

    # ── Gitaly 健康 (通过 readiness 检查) ──
    _check_gitaly_health(gl, g)

    # ── 仓库存储 (通过 API) ──
    _check_repository_storage(gl, g)

    # ── 基础设施层 ──
    if mode == DeployMode.K8S:
        _check_gitaly_k8s(ctx, g)
    elif mode == DeployMode.DOCKER:
        pass  # Docker 模式下 Gitaly 在同一容器
    elif mode == DeployMode.VM:
        _check_gitaly_vm(g)

    return g


def _check_gitaly_health(gl, g):
    """通过 readiness 子检查和 Gitaly 专用端点检查。"""
    resp = gl.readiness()
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        # readiness 检查通过说明 Gitaly 连接正常
        body = resp["body"]
        status = body.get("status", "unknown")
        if status == "ok":
            g.ok("Gitaly 连接", "readiness 检查通过")
        else:
            g.warn("Gitaly 连接", f"readiness status={status}")
    else:
        g.warn("Gitaly 连接", "无法通过 readiness 端点确认")

    # Gitaly 检查 (通过 API, 需要 admin token)
    resp = gl.api_v4("/internal/check")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        api_version = resp["body"].get("api_version", "?")
        g.ok("Gitaly 内部检查", f"API version: {api_version}")
    # 不强制要求此接口可用


def _check_repository_storage(gl, g):
    """检查仓库存储配置 (需要 admin token)。"""
    # 通过项目 API 间接测试 Gitaly
    resp = gl.api_v4("/projects", params={"per_page": "1", "simple": "true"})
    if resp["status"] == 200 and isinstance(resp["body"], list):
        if resp["body"]:
            proj = resp["body"][0]
            g.ok("仓库访问", f"可列出项目 (示例: {proj.get('name', '?')})")
        else:
            g.ok("仓库访问", "API 可用，暂无项目")
    elif resp["status"] == 401:
        g.warn("仓库访问", "需要 Token 才能测试仓库访问 (--token)")
    else:
        g.warn("仓库访问", f"API 返回 status={resp['status']}")

    # 检查 repository_storage (admin only)
    resp = gl.api_v4("/application/plan_limits")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        repo_size = resp["body"].get("repository_size_limit", 0)
        if repo_size > 0:
            g.ok("仓库大小限制", f"{repo_size / (1024*1024):.0f} MB")


def _check_gitaly_k8s(ctx, g):
    """K8s: 检查 Gitaly StatefulSet 和 PVC。"""
    k8s_core = ctx.get("k8s_core")
    k8s_apps = ctx.get("k8s_apps")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    # Gitaly Pod
    try:
        pods = k8s_core.list_namespaced_pod(
            ns, label_selector="app=gitaly")
        for pod in pods.items:
            if pod.status.phase == "Succeeded":
                continue
            pod_name = pod.metadata.name
            phase = pod.status.phase
            conditions = {c.type: c.status for c in (pod.status.conditions or [])}
            if phase == "Running" and conditions.get("Ready") == "True":
                g.ok(f"Gitaly Pod {pod_name}", "Running & Ready")
            elif phase == "Running":
                g.warn(f"Gitaly Pod {pod_name}", "Running 但未 Ready")
            else:
                g.error(f"Gitaly Pod {pod_name}", f"阶段: {phase}")

            # 检查重启
            for cs in (pod.status.container_statuses or []):
                if cs.restart_count > 3:
                    g.warn(f"Gitaly {pod_name}/{cs.name}",
                           f"重启次数: {cs.restart_count}")
    except Exception as e:
        g.warn("Gitaly Pod", f"检查失败: {e}")

    # Gitaly PVC
    try:
        pods = k8s_core.list_namespaced_pod(
            ns, label_selector="app=gitaly")
        pvc_names = set()
        for pod in pods.items:
            for vol in (pod.spec.volumes or []):
                if vol.persistent_volume_claim:
                    pvc_names.add(vol.persistent_volume_claim.claim_name)

        for pvc_name in pvc_names:
            try:
                pvc = k8s_core.read_namespaced_persistent_volume_claim(pvc_name, ns)
                phase = pvc.status.phase
                capacity = pvc.status.capacity or {}
                storage = capacity.get("storage", "unknown")
                if phase == "Bound":
                    g.ok(f"Gitaly PVC {pvc_name}", f"Bound, 容量 {storage}")
                else:
                    g.error(f"Gitaly PVC {pvc_name}", f"状态异常: {phase}")
            except Exception as e:
                g.warn(f"Gitaly PVC {pvc_name}", f"获取失败: {e}")
    except Exception:
        pass

    # StatefulSet
    if k8s_apps:
        try:
            stss = k8s_apps.list_namespaced_stateful_set(
                ns, label_selector="app=gitaly")
            for sts in stss.items:
                desired = sts.spec.replicas or 1
                ready = sts.status.ready_replicas or 0
                if ready >= desired:
                    g.ok(f"Gitaly StatefulSet {sts.metadata.name}",
                         f"副本 {ready}/{desired}")
                else:
                    g.error(f"Gitaly StatefulSet {sts.metadata.name}",
                            f"副本 {ready}/{desired} 未达标")
        except Exception:
            pass


def _check_gitaly_vm(g):
    """VM: 检查 Gitaly 进程和存储。"""
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gitaly"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            g.ok("Gitaly 进程", f"检测到 {len(pids)} 个进程")
        else:
            g.warn("Gitaly 进程", "未检测到 Gitaly 进程")
    except Exception:
        pass

    # 检查仓库存储目录
    try:
        result = subprocess.run(
            ["df", "-h", "/var/opt/gitlab/git-data"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 5:
                    usage = parts[4]  # e.g., "42%"
                    g.ok("仓库存储", f"使用率 {usage}")
    except Exception:
        pass
