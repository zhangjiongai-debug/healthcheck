"""5. 数据依赖检查。

- GitLab 到 PostgreSQL 是否正常
- GitLab 到 Redis 是否正常
- GitLab 到对象存储（MinIO/S3）是否正常
- artifacts/uploads/packages/lfs 是否可访问
- 数据库 migration 是否完成

注: PostgreSQL 深度检查请使用 postgresql 模块。
    MinIO 深度检查请使用 minio 模块。
    这里只做 GitLab 视角的简化连通性检查。
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5. 数据依赖检查")
    gl = ctx["gl"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        if mode == DeployMode.K8S:
            _check_deps_k8s(ctx, g)
        return g

    # ── 通过 readiness 检查数据库连接 ──
    _check_readiness_deps(gl, g)

    # ── 数据库 migration ──
    _check_migrations(gl, g)

    # ── 对象存储 (通过上传接口间接测试) ──
    _check_object_storage(gl, g)

    # ── K8s 基础设施层依赖 ──
    if mode == DeployMode.K8S:
        _check_deps_k8s(ctx, g)

    return g


def _check_readiness_deps(gl, g):
    """通过 readiness 端点检查核心依赖。"""
    resp = gl.readiness()
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        body = resp["body"]
        status = body.get("status", "unknown")

        # readiness OK 意味着 DB、Redis、Gitaly 都通
        if status == "ok":
            g.ok("核心依赖 (DB/Redis/Gitaly)", "readiness 检查全部通过")
        else:
            # 解析具体哪个失败
            for key in ("db_check", "redis_check", "gitaly_check",
                        "cache_check", "queues_check", "shared_state_check"):
                checks = body.get(key, [])
                if isinstance(checks, list):
                    for c in checks:
                        if isinstance(c, dict) and c.get("status") != "ok":
                            g.error(f"依赖 [{key}]",
                                    f"检查失败: {c.get('message', 'unknown')}")
            if status != "failed":
                g.warn("核心依赖", f"readiness status={status}")
    else:
        g.warn("核心依赖", "无法通过 readiness 端点检查")


def _check_migrations(gl, g):
    """检查数据库 migration 状态。"""
    # 通过 Sidekiq API 的 job_stats 间接判断
    # 或者通过 /admin/background_migrations (需要 admin)
    resp = gl.api_v4("/sidekiq/job_stats")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        jobs = resp["body"].get("jobs", {})
        if isinstance(jobs, dict):
            enqueued = jobs.get("enqueued", 0)
            # 如果有大量 enqueued 可能包含 migration
            if enqueued > 500:
                g.warn("后台任务积压",
                       f"enqueued: {enqueued} (可能包含未完成的 migration)")
            else:
                g.ok("后台任务", f"enqueued: {enqueued}")

    # 通过 API 检查 background migrations (admin only)
    resp = gl.api_v4("/admin/batched_background_migrations",
                     params={"per_page": "100"})
    if resp["status"] == 200 and isinstance(resp["body"], list):
        migrations = resp["body"]
        running = [m for m in migrations
                   if isinstance(m, dict) and m.get("status") in ("active", "paused")]
        failed = [m for m in migrations
                  if isinstance(m, dict) and m.get("status") == "failed"]

        if failed:
            detail = "\n".join(
                f"{m.get('job_class_name', '?')}: {m.get('status')}"
                for m in failed[:5])
            g.error("后台 Migration", f"{len(failed)} 个失败",
                    detail=detail)
        elif running:
            g.ok("后台 Migration", f"{len(running)} 个进行中")
        else:
            g.ok("后台 Migration", f"全部完成 (共 {len(migrations)} 个)")
    elif resp["status"] == 401:
        g.warn("后台 Migration", "需要管理员 Token 检查 migration 状态")


def _check_object_storage(gl, g):
    """检查对象存储可用性 (间接检查)。"""
    # 通过 uploads API 间接验证
    # 如果能获取到项目的头像/uploads，说明对象存储正常
    resp = gl.api_v4("/projects", params={"per_page": "1"})
    if resp["status"] == 200 and isinstance(resp["body"], list):
        if resp["body"]:
            proj = resp["body"][0]
            avatar = proj.get("avatar_url", "")
            if avatar:
                g.ok("对象存储", "项目有头像 URL，对象存储可用")
            else:
                g.ok("对象存储", "API 可用 (无法通过 API 深度验证对象存储)")
    elif resp["status"] == 401:
        pass  # 已在其他模块报告


def _check_deps_k8s(ctx, g):
    """K8s: 检查 PostgreSQL / Redis / MinIO Pod 状态 (简化检查)。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]

    if not k8s_core:
        return

    deps = [
        ("app.kubernetes.io/name=postgresql", "PostgreSQL"),
        ("app.kubernetes.io/name=redis", "Redis"),
        ("app.kubernetes.io/name=minio", "MinIO (对象存储)"),
    ]

    # 也尝试 Helm chart 典型 label
    deps_alt = [
        ("app=postgresql", "PostgreSQL"),
        ("app=redis", "Redis"),
        ("app=minio", "MinIO (对象存储)"),
    ]

    for selectors in [deps, deps_alt]:
        found_any = False
        for selector, name in selectors:
            try:
                pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
                running = [p for p in pods.items
                           if p.status.phase not in ("Succeeded", "Failed")]
                if not running:
                    continue

                found_any = True
                healthy = 0
                for pod in running:
                    conditions = {c.type: c.status
                                  for c in (pod.status.conditions or [])}
                    if (pod.status.phase == "Running"
                            and conditions.get("Ready") == "True"):
                        healthy += 1

                if healthy == len(running):
                    g.ok(f"{name} Pod", f"{healthy}/{len(running)} 健康")
                elif healthy > 0:
                    g.warn(f"{name} Pod",
                           f"{healthy}/{len(running)} 健康")
                else:
                    g.error(f"{name} Pod",
                            f"0/{len(running)} 健康")
            except Exception:
                continue

        if found_any:
            break

    # 检查 PostgreSQL/Redis Service 可达
    svcs_to_check = [
        ("gitlab-postgresql", 5432, "PostgreSQL Service"),
        ("gitlab-redis-master", 6379, "Redis Service"),
    ]
    for svc_name, port, display in svcs_to_check:
        try:
            svc = k8s_core.read_namespaced_service(svc_name, ns)
            cluster_ip = svc.spec.cluster_ip
            if cluster_ip and cluster_ip != "None":
                g.ok(display, f"{svc_name}:{port} (ClusterIP: {cluster_ip})")
        except Exception:
            pass  # Service 可能名称不同
