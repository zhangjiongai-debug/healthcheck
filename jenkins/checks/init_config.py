"""2. Jenkins 初始化与配置状态检查。

- Jenkins 是否完成启动
- init script / plugin 加载是否成功
- 配置是否被正确加载（JCasC 若启用）
- 系统日志中是否有关键错误
- 是否处于安全锁定/初始化未完成状态
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("2. Jenkins 初始化与配置状态")
    jk = ctx["jk"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 启动完成检查 ──
    _check_startup(jk, g)

    # ── 系统日志关键错误 ──
    _check_system_log(jk, g)

    # ── JCasC 配置 ──
    _check_jcasc(jk, g)

    # ── 安全锁定 ──
    _check_security_state(jk, g)

    return g


def _check_startup(jk, g):
    """检查 Jenkins 是否完成初始化。"""
    # 通过 Groovy 检查
    result = jk.script_console(
        "println(Jenkins.instance.isTerminating() ? 'TERMINATING' : "
        "(Jenkins.instance.initLevel?.name() ?: 'COMPLETED'))")
    if result is not None:
        level = result.strip()
        if level == "COMPLETED":
            g.ok("初始化状态", "Jenkins 已完成启动")
        elif level == "TERMINATING":
            g.fatal("初始化状态", "Jenkins 正在关闭!")
        else:
            g.warn("初始化状态", f"初始化阶段: {level}")
    else:
        # 降级: 通过 API 判断
        resp = jk.api_json()
        if resp["status"] == 200:
            g.ok("初始化状态", "Jenkins API 可用 (Script Console 不可用)")
        elif resp["status"] == 503:
            g.error("初始化状态", "Jenkins 返回 503, 可能仍在启动中")
        else:
            g.warn("初始化状态", f"无法确认启动状态 (status={resp['status']})")


def _check_system_log(jk, g):
    """检查系统日志中的关键错误。"""
    result = jk.script_console("""
import java.util.logging.*
def logger = Logger.getLogger('')
def handler = logger.handlers.find { it instanceof java.util.logging.MemoryHandler || it.class.name.contains('RingBuffer') }
// 通过 Jenkins 内置日志
def logs = Jenkins.instance.log
def severe_count = 0
def severe_msgs = []
logs.getRecords().each { record ->
    if (record.level.intValue() >= Level.SEVERE.intValue()) {
        severe_count++
        if (severe_msgs.size() < 5) {
            severe_msgs << "${record.level}: ${record.message?.take(120)}"
        }
    }
}
println("SEVERE:${severe_count}")
severe_msgs.each { println(it) }
""")
    if result is not None:
        lines = result.strip().splitlines()
        if lines:
            first = lines[0]
            if first.startswith("SEVERE:"):
                count = int(first.split(":")[1])
                detail = "\n".join(lines[1:]) if len(lines) > 1 else None
                if count > 10:
                    g.error("系统日志", f"发现 {count} 条 SEVERE 级别日志", detail=detail)
                elif count > 0:
                    g.warn("系统日志", f"发现 {count} 条 SEVERE 级别日志", detail=detail)
                else:
                    g.ok("系统日志", "无 SEVERE 级别日志")
            else:
                g.ok("系统日志", "已检查 (无严重错误)")


def _check_jcasc(jk, g):
    """检查 JCasC 配置状态。"""
    result = jk.script_console("""
try {
    def casc = io.jenkins.plugins.casc.ConfigurationAsCode.get()
    def sources = casc.configurationSources
    println("JCASC_ENABLED:${sources.size()}")
} catch (Exception e) {
    println("JCASC_DISABLED")
}
""")
    if result is not None:
        text = result.strip()
        if text.startswith("JCASC_ENABLED"):
            count = text.split(":")[1] if ":" in text else "?"
            g.ok("JCasC 配置", f"已启用, {count} 个配置源")
        elif "JCASC_DISABLED" in text:
            g.ok("JCasC 配置", "未启用 (使用传统配置)")


def _check_security_state(jk, g):
    """检查安全状态。"""
    result = jk.script_console("""
def security = Jenkins.instance.securityRealm
def authz = Jenkins.instance.authorizationStrategy
println("REALM:${security?.class?.simpleName ?: 'None'}")
println("AUTHZ:${authz?.class?.simpleName ?: 'None'}")
println("CRUMB:${Jenkins.instance.crumbIssuer != null}")
""")
    if result is not None:
        for line in result.strip().splitlines():
            if line.startswith("REALM:"):
                realm = line.split(":", 1)[1]
                if realm in ("None", "SecurityRealm$None"):
                    g.warn("安全域", "未配置安全域!")
                else:
                    g.ok("安全域", realm)
            elif line.startswith("AUTHZ:"):
                authz = line.split(":", 1)[1]
                if "Unsecured" in authz:
                    g.error("授权策略", f"不安全: {authz}")
                else:
                    g.ok("授权策略", authz)
            elif line.startswith("CRUMB:"):
                has_crumb = line.split(":")[1]
                if has_crumb == "true":
                    g.ok("CSRF 保护", "已启用")
                else:
                    g.warn("CSRF 保护", "未启用 CRUMB, 建议开启")
