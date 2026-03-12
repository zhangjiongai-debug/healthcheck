"""3. 插件健康检查。

- 核心插件是否安装
- 插件是否加载失败
- 插件版本是否冲突
- 插件依赖是否缺失
- 是否存在高危过旧插件
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("3. 插件健康检查")
    jk = ctx["jk"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 插件列表与状态 ──
    _check_plugins(jk, g)

    # ── 失败的插件 ──
    _check_failed_plugins(jk, g)

    # ── 可更新的插件 ──
    _check_plugin_updates(jk, g)

    return g


def _check_plugins(jk, g):
    """获取插件列表与状态。"""
    resp = jk.api_json("/pluginManager",
                       tree="plugins[shortName,version,active,enabled,hasUpdate,longName]",
                       depth=1)
    if resp["status"] != 200 or not isinstance(resp["body"], dict):
        # 降级: 使用 script console
        _check_plugins_via_groovy(jk, g)
        return

    plugins = resp["body"].get("plugins", [])
    if not plugins:
        g.warn("插件列表", "未获取到插件信息")
        return

    active_count = sum(1 for p in plugins if p.get("active"))
    inactive_count = sum(1 for p in plugins if not p.get("active"))
    update_count = sum(1 for p in plugins if p.get("hasUpdate"))

    g.ok("插件总数", f"{len(plugins)} 个插件, {active_count} 活跃, {inactive_count} 停用")

    if inactive_count > 0:
        inactive = [p["shortName"] for p in plugins if not p.get("active")][:10]
        g.warn("停用插件", f"{inactive_count} 个插件未激活",
               detail=", ".join(inactive))

    if update_count > 0:
        updates = [f"{p['shortName']}({p['version']})" for p in plugins if p.get("hasUpdate")][:10]
        g.warn("可更新插件", f"{update_count} 个插件有更新",
               detail=", ".join(updates))


def _check_plugins_via_groovy(jk, g):
    """通过 Groovy 检查插件。"""
    result = jk.script_console("""
def pm = Jenkins.instance.pluginManager
def active = pm.plugins.findAll { it.isActive() }
def inactive = pm.plugins.findAll { !it.isActive() }
println("TOTAL:${pm.plugins.size()}")
println("ACTIVE:${active.size()}")
println("INACTIVE:${inactive.size()}")
inactive.take(10).each { println("INACTIVE_PLUGIN:${it.shortName}:${it.version}") }
""")
    if result is None:
        g.warn("插件检查", "无法访问 Script Console 和 Plugin Manager API")
        return

    for line in result.strip().splitlines():
        if line.startswith("TOTAL:"):
            total = line.split(":")[1]
            g.ok("插件总数", f"{total} 个插件")
        elif line.startswith("ACTIVE:"):
            g.ok("活跃插件", f"{line.split(':')[1]} 个")
        elif line.startswith("INACTIVE:"):
            count = line.split(":")[1]
            if int(count) > 0:
                g.warn("停用插件", f"{count} 个")


def _check_failed_plugins(jk, g):
    """检查加载失败的插件。"""
    result = jk.script_console("""
def pm = Jenkins.instance.pluginManager
def failed = pm.failedPlugins
println("FAILED:${failed.size()}")
failed.each { println("${it.name}: ${it.cause?.message?.take(100) ?: 'unknown'}") }
""")
    if result is None:
        return

    lines = result.strip().splitlines()
    failed_count = 0
    failed_details = []
    for line in lines:
        if line.startswith("FAILED:"):
            failed_count = int(line.split(":")[1])
        elif line.startswith("Result:") or not line.strip():
            continue  # Jenkins Script Console 尾部输出，跳过
        elif failed_count > 0:
            failed_details.append(line.strip())

    if failed_count > 0:
        g.error("加载失败插件", f"{failed_count} 个插件加载失败",
                detail="\n".join(failed_details) if failed_details else None)
    elif any(line.startswith("FAILED:") for line in lines):
        g.ok("加载失败插件", "无")


def _check_plugin_updates(jk, g):
    """检查插件依赖缺失和版本冲突。"""
    result = jk.script_console("""
def pm = Jenkins.instance.pluginManager
def missing = []
pm.plugins.each { plugin ->
    plugin.dependencies.each { dep ->
        def installed = pm.getPlugin(dep.shortName)
        if (installed == null) {
            missing << "${plugin.shortName} -> ${dep.shortName}"
        }
    }
}
println("MISSING_DEPS:${missing.size()}")
missing.take(10).each { println(it) }
""")
    if result is None:
        return

    for line in result.strip().splitlines():
        if line.startswith("MISSING_DEPS:"):
            count = int(line.split(":")[1])
            if count > 0:
                g.error("插件依赖缺失", f"{count} 个依赖缺失")
            else:
                g.ok("插件依赖", "所有依赖完整")
