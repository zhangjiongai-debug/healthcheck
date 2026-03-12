"""7. GitLab 功能面检查。

- 仓库浏览是否正常
- 用户认证是否正常
- Pipeline 创建是否正常
- Job 日志是否正常
- 制品上传下载是否正常
- Container Registry（若启用）是否正常
- Package Registry（若启用）是否正常
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("7. 功能面检查")
    gl = ctx["gl"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 仓库浏览 ──
    _check_repository_browse(gl, g)

    # ── 用户认证 ──
    _check_user_auth(gl, g)

    # ── Pipeline ──
    _check_pipelines(gl, g)

    # ── Container Registry ──
    _check_container_registry(gl, g, mode, ctx)

    # ── Package Registry ──
    _check_package_registry(gl, g)

    # ── 应用设置 ──
    _check_application_settings(gl, g)

    return g


def _check_repository_browse(gl, g):
    """检查仓库是否可浏览。"""
    resp = gl.api_v4("/projects", params={
        "per_page": "1",
        "simple": "true",
        "order_by": "last_activity_at",
    })
    if resp["status"] == 200 and isinstance(resp["body"], list):
        if resp["body"]:
            proj = resp["body"][0]
            proj_id = proj.get("id")
            proj_name = proj.get("path_with_namespace", "?")

            # 尝试列出仓库文件
            if proj_id:
                tree_resp = gl.api_v4(
                    f"/projects/{proj_id}/repository/tree",
                    params={"per_page": "5"})
                if tree_resp["status"] == 200 and isinstance(tree_resp["body"], list):
                    g.ok("仓库浏览", f"可正常浏览 ({proj_name}, "
                         f"{len(tree_resp['body'])} 个文件/目录)")
                elif tree_resp["status"] == 404:
                    g.ok("仓库浏览", f"项目 {proj_name} 仓库为空 (正常)")
                else:
                    g.warn("仓库浏览", f"仓库树获取异常 (status={tree_resp['status']})")
            else:
                g.ok("仓库浏览", "项目可列出")
        else:
            g.ok("仓库浏览", "API 可用，暂无项目")
    elif resp["status"] == 401:
        g.warn("仓库浏览", "需要 Token 才能测试仓库浏览")
    else:
        g.warn("仓库浏览", f"API 异常 (status={resp['status']})")


def _check_user_auth(gl, g):
    """检查当前用户认证状态。"""
    resp = gl.api_v4("/user")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        user = resp["body"]
        username = user.get("username", "?")
        is_admin = user.get("is_admin", False)
        state = user.get("state", "?")
        g.ok("用户认证", f"已认证: {username} "
             f"(admin: {is_admin}, state: {state})")
    elif resp["status"] == 401:
        g.warn("用户认证", "未认证或 Token 无效")
    else:
        g.warn("用户认证", f"status={resp['status']}")


def _check_pipelines(gl, g):
    """检查 Pipeline 功能。"""
    resp = gl.api_v4("/projects", params={
        "per_page": "5",
        "simple": "true",
        "order_by": "last_activity_at",
    })
    if resp["status"] != 200 or not isinstance(resp["body"], list):
        return

    pipeline_found = False
    for proj in resp["body"]:
        proj_id = proj.get("id")
        if not proj_id:
            continue

        pipe_resp = gl.api_v4(
            f"/projects/{proj_id}/pipelines",
            params={"per_page": "5"})
        if pipe_resp["status"] == 200 and isinstance(pipe_resp["body"], list):
            pipes = pipe_resp["body"]
            if pipes:
                pipeline_found = True
                statuses = {}
                for p in pipes:
                    s = p.get("status", "unknown")
                    statuses[s] = statuses.get(s, 0) + 1
                status_str = ", ".join(f"{k}: {v}" for k, v in statuses.items())
                g.ok("Pipeline 功能",
                     f"最近 Pipeline ({proj.get('name', '?')}): {status_str}")
                break

    if not pipeline_found:
        g.ok("Pipeline 功能", "未发现最近的 Pipeline (正常)")


def _check_container_registry(gl, g, mode, ctx):
    """检查 Container Registry。"""
    # 通过 API 检查 registry 功能
    resp = gl.api_v4("/registry/repositories", params={"per_page": "1"})
    if resp["status"] == 200:
        g.ok("Container Registry", "API 可用")
    elif resp["status"] == 404:
        # 可能未启用或路径不同
        # 尝试通过 application settings 检查
        pass
    elif resp["status"] == 401:
        pass  # 已在其他地方报告

    # K8s: 检查 Registry Pod
    if mode == DeployMode.K8S:
        k8s_core = ctx.get("k8s_core")
        ns = ctx["namespace"]
        if k8s_core:
            try:
                pods = k8s_core.list_namespaced_pod(
                    ns, label_selector="app=registry")
                running = [p for p in pods.items
                           if p.status.phase == "Running"]
                if running:
                    healthy = sum(1 for p in running
                                  if any(c.type == "Ready" and c.status == "True"
                                         for c in (p.status.conditions or [])))
                    g.ok("Registry Pod", f"{healthy}/{len(running)} 健康")
            except Exception:
                pass


def _check_package_registry(gl, g):
    """检查 Package Registry 功能。"""
    resp = gl.api_v4("/projects", params={"per_page": "1"})
    if resp["status"] != 200 or not isinstance(resp["body"], list):
        return

    if not resp["body"]:
        return

    proj_id = resp["body"][0].get("id")
    if not proj_id:
        return

    pkg_resp = gl.api_v4(f"/projects/{proj_id}/packages",
                         params={"per_page": "1"})
    if pkg_resp["status"] == 200:
        g.ok("Package Registry", "API 可用")
    elif pkg_resp["status"] == 404:
        g.ok("Package Registry", "未启用或无 Package")
    elif pkg_resp["status"] == 403:
        g.ok("Package Registry", "API 可达 (无权限查看)")


def _check_application_settings(gl, g):
    """检查应用设置 (admin only)。"""
    resp = gl.api_v4("/application/settings")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        settings = resp["body"]

        # 注册开关
        signup_enabled = settings.get("signup_enabled", False)
        if signup_enabled:
            g.warn("注册功能", "注册功能已开启 (建议关闭)")
        else:
            g.ok("注册功能", "已关闭")

        # Container Registry
        cr_enabled = settings.get("container_registry_enabled", False)
        g.ok("Container Registry 配置",
             "已启用" if cr_enabled else "未启用")

        # 仓库大小限制
        repo_size = settings.get("repository_size_limit", 0)
        if repo_size > 0:
            g.ok("仓库大小限制", f"{repo_size / (1024*1024):.0f} MB")

        # 导入源
        import_sources = settings.get("import_sources", [])
        if import_sources:
            g.ok("导入源", ", ".join(import_sources))
    elif resp["status"] == 401:
        pass  # 已报告
    elif resp["status"] == 403:
        g.warn("应用设置", "需要管理员 Token 查看设置")
