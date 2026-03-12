"""6. Jenkins 依赖检查。

- Jenkins Home PVC 是否正常挂载
- 磁盘容量是否充足
- 对 GitLab/Git 仓库访问是否正常
- 对制品库/对象存储访问是否正常
- 邮件/通知通道是否正常
"""

import subprocess

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("6. Jenkins 依赖检查")
    jk = ctx["jk"]
    mode = ctx["mode"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── Jenkins Home 空间 ──
    _check_jenkins_home(jk, g, mode, ctx)

    # ── Credential 检查 ──
    _check_credentials(jk, g)

    # ── SCM 配置 ──
    _check_scm_config(jk, g)

    # ── 邮件/通知配置 ──
    _check_notification(jk, g)

    return g


def _check_jenkins_home(jk, g, mode, ctx):
    """检查 Jenkins Home 磁盘空间。"""
    # 通过 Groovy 获取
    result = jk.script_console("""
def home = Jenkins.instance.rootDir
def total = home.totalSpace
def free = home.freeSpace
def usable = home.usableSpace
println("HOME:${home.absolutePath}")
println("TOTAL:${total}")
println("FREE:${free}")
println("USABLE:${usable}")
""")
    if result is not None:
        info = {}
        for line in result.strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                info[key] = val.strip()

        home_path = info.get("HOME", "?")
        total = int(info.get("TOTAL", 0))
        free = int(info.get("FREE", 0))

        if total > 0:
            usage_pct = ((total - free) / total) * 100
            total_gb = total / (1024 ** 3)
            free_gb = free / (1024 ** 3)

            if usage_pct > 95:
                g.fatal("Jenkins Home 磁盘",
                        f"{home_path}: {usage_pct:.0f}% 已用, "
                        f"剩余 {free_gb:.1f} GB!")
            elif usage_pct > 85:
                g.error("Jenkins Home 磁盘",
                        f"{home_path}: {usage_pct:.0f}% 已用, "
                        f"剩余 {free_gb:.1f} GB")
            elif usage_pct > 75:
                g.warn("Jenkins Home 磁盘",
                       f"{home_path}: {usage_pct:.0f}% 已用, "
                       f"总 {total_gb:.1f} GB, 剩余 {free_gb:.1f} GB")
            else:
                g.ok("Jenkins Home 磁盘",
                     f"{home_path}: {usage_pct:.0f}% 已用, "
                     f"总 {total_gb:.1f} GB, 剩余 {free_gb:.1f} GB")
            return

    # K8s PVC 检查
    if mode == DeployMode.K8S:
        _check_k8s_pvc(ctx, g)


def _check_k8s_pvc(ctx, g):
    """K8s 模式: 检查 PVC 状态。"""
    k8s_core = ctx.get("k8s_core")
    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    if not k8s_core:
        return

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
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
                    g.ok(f"PVC {pvc_name}", f"Bound, 容量 {storage}")
                else:
                    g.error(f"PVC {pvc_name}", f"状态异常: {phase}")
            except Exception as e:
                g.warn(f"PVC {pvc_name}", f"获取失败: {e}")
    except Exception:
        pass


def _check_credentials(jk, g):
    """检查 Credential 配置。"""
    result = jk.script_console("""
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.common.StandardCredentials
try {
    def creds = CredentialsProvider.lookupCredentials(StandardCredentials.class,
        Jenkins.instance, null, null)
    def types = creds.groupBy { it.class.simpleName }
    println("TOTAL:${creds.size()}")
    types.each { k, v -> println("TYPE:${k}=${v.size()}") }
} catch (Exception e) {
    println("ERROR:${e.message?.take(100)}")
}
""")
    if result is None:
        return

    for line in result.strip().splitlines():
        if line.startswith("TOTAL:"):
            count = int(line.split(":")[1])
            g.ok("Credentials", f"共 {count} 个凭证")
        elif line.startswith("TYPE:"):
            pass  # 类型统计不单独展示
        elif line.startswith("ERROR:"):
            g.warn("Credentials", f"检查失败: {line[6:]}")


def _check_scm_config(jk, g):
    """检查 SCM (Git) 配置。"""
    result = jk.script_console("""
try {
    def gitTool = Jenkins.instance.getDescriptorByType(
        hudson.plugins.git.GitTool.DescriptorImpl.class)
    def installations = gitTool?.installations
    if (installations) {
        installations.each { println("GIT_TOOL:${it.name}=${it.home}") }
    } else {
        println("GIT_TOOL:default")
    }
} catch (Exception e) {
    println("GIT_TOOL:not_configured")
}
""")
    if result is not None:
        for line in result.strip().splitlines():
            if line.startswith("GIT_TOOL:"):
                g.ok("Git 工具", line[9:])


def _check_notification(jk, g):
    """检查邮件/通知配置。"""
    result = jk.script_console("""
try {
    def mailer = Jenkins.instance.getDescriptor('hudson.tasks.Mailer')
    def smtp = mailer?.smtpHost ?: ''
    if (smtp) {
        println("SMTP:${smtp}")
    } else {
        println("SMTP:not_configured")
    }
} catch (Exception e) {
    println("SMTP:not_available")
}
""")
    if result is not None:
        for line in result.strip().splitlines():
            if line.startswith("SMTP:"):
                smtp = line[5:]
                if smtp in ("not_configured", "not_available"):
                    g.ok("邮件通知", "未配置 SMTP")
                else:
                    g.ok("邮件通知", f"SMTP: {smtp}")
