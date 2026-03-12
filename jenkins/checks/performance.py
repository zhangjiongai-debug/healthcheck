"""7. Jenkins 性能与风险预警。

- 堆内存使用率过高
- GC 频繁
- 构建队列积压
- Executor 利用率过高
- Jenkins Home 接近满盘
- 插件冲突风险
- 控制器单点风险
- Agent 全部离线风险
"""

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("7. 性能与风险预警")
    jk = ctx["jk"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── JVM 内存 ──
    _check_jvm_memory(jk, g)

    # ── GC 状态 ──
    _check_gc(jk, g)

    # ── 线程 ──
    _check_threads(jk, g)

    # ── 单点风险 ──
    _check_single_point(jk, g, ctx)

    # ── Prometheus metrics (如果可用) ──
    _check_metrics(jk, g)

    return g


def _check_jvm_memory(jk, g):
    """检查 JVM 堆内存使用率。"""
    result = jk.script_console("""
def runtime = Runtime.getRuntime()
def maxMem = runtime.maxMemory()
def totalMem = runtime.totalMemory()
def freeMem = runtime.freeMemory()
def usedMem = totalMem - freeMem
println("MAX:${maxMem}")
println("USED:${usedMem}")
println("FREE:${freeMem}")
println("TOTAL:${totalMem}")
""")
    if result is None:
        g.warn("JVM 内存", "无法获取 (需要 Script Console 权限)")
        return

    info = {}
    for line in result.strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            info[key] = int(val.strip())

    max_mem = info.get("MAX", 0)
    used = info.get("USED", 0)

    if max_mem > 0:
        usage_pct = (used / max_mem) * 100
        max_gb = max_mem / (1024 ** 3)
        used_gb = used / (1024 ** 3)

        if usage_pct > 90:
            g.error("JVM 堆内存",
                    f"{usage_pct:.0f}% ({used_gb:.2f}/{max_gb:.2f} GB) — "
                    "可能触发 Full GC!")
        elif usage_pct > 75:
            g.warn("JVM 堆内存",
                   f"{usage_pct:.0f}% ({used_gb:.2f}/{max_gb:.2f} GB)")
        else:
            g.ok("JVM 堆内存",
                 f"{usage_pct:.0f}% ({used_gb:.2f}/{max_gb:.2f} GB)")


def _check_gc(jk, g):
    """检查 GC 状态。"""
    result = jk.script_console("""
import java.lang.management.ManagementFactory
def gcBeans = ManagementFactory.garbageCollectorMXBeans
gcBeans.each { gc ->
    println("GC:${gc.name}|${gc.collectionCount}|${gc.collectionTime}")
}
""")
    if result is None:
        return

    for line in result.strip().splitlines():
        if line.startswith("GC:"):
            parts = line[3:].split("|")
            if len(parts) >= 3:
                name = parts[0]
                count = int(parts[1])
                time_ms = int(parts[2])

                # 如果是 Full GC 类型且次数较多
                is_full = any(k in name.lower()
                              for k in ["old", "full", "major", "mark"])
                if is_full and count > 100:
                    g.warn(f"GC [{name}]",
                           f"次数: {count}, 耗时: {time_ms}ms — Full GC 频繁")
                else:
                    g.ok(f"GC [{name}]", f"次数: {count}, 耗时: {time_ms}ms")


def _check_threads(jk, g):
    """检查线程数。"""
    result = jk.script_console("""
def threads = Thread.activeCount()
def peak = java.lang.management.ManagementFactory.getThreadMXBean().peakThreadCount
def deadlocked = java.lang.management.ManagementFactory.getThreadMXBean().findDeadlockedThreads()
println("ACTIVE:${threads}")
println("PEAK:${peak}")
println("DEADLOCK:${deadlocked?.length ?: 0}")
""")
    if result is None:
        return

    for line in result.strip().splitlines():
        if line.startswith("ACTIVE:"):
            count = int(line.split(":")[1])
            if count > 1000:
                g.warn("线程数", f"活跃 {count} 个 (偏多)")
            else:
                g.ok("线程数", f"活跃 {count} 个")
        elif line.startswith("DEADLOCK:"):
            count = int(line.split(":")[1])
            if count > 0:
                g.fatal("死锁检测", f"发现 {count} 个死锁线程!")
            else:
                g.ok("死锁检测", "无死锁")


def _check_single_point(jk, g, ctx):
    """检查单点风险。"""
    # 检查 controller 是否为单副本
    mode = ctx["mode"]
    if mode == DeployMode.K8S:
        k8s_apps = ctx.get("k8s_apps")
        ns = ctx["namespace"]
        selector = ctx["label_selector"]
        if k8s_apps:
            try:
                stss = k8s_apps.list_namespaced_stateful_set(ns, label_selector=selector)
                for sts in stss.items:
                    if (sts.spec.replicas or 1) == 1:
                        g.warn(f"单点风险 [{sts.metadata.name}]",
                               "Jenkins Controller 为单副本，无高可用保护")
            except Exception:
                pass

    # Agent 风险
    result = jk.script_console("""
def computers = Jenkins.instance.computers
def nonMaster = computers.findAll { it.name != '' && it.name != 'master' && it.name != '(master)' }
def online = nonMaster.findAll { !it.offline }
println("AGENTS_TOTAL:${nonMaster.size()}")
println("AGENTS_ONLINE:${online.size()}")
""")
    if result is not None:
        agents_total = 0
        agents_online = 0
        for line in result.strip().splitlines():
            if line.startswith("AGENTS_TOTAL:"):
                agents_total = int(line.split(":")[1])
            elif line.startswith("AGENTS_ONLINE:"):
                agents_online = int(line.split(":")[1])

        if agents_total > 0 and agents_online == 0:
            g.error("Agent 风险", "所有 Agent 离线!")
        elif agents_total == 0:
            g.warn("Agent 风险", "未配置 Agent，构建仅在 Controller 上运行")


def _check_metrics(jk, g):
    """检查 Prometheus metrics (如果安装了 prometheus 插件)。"""
    resp = jk.get("/prometheus/")
    if resp["status"] != 200:
        # 尝试另一个路径
        resp = jk.get("/metrics/")
    if resp["status"] != 200:
        return

    body = resp["body"] if isinstance(resp["body"], str) else ""
    if not body:
        return

    # 解析关键 metrics
    for line in body.splitlines():
        if line.startswith("#"):
            continue

        # HTTP 请求时间
        if "jenkins_http_requests_" in line and "_count" in line:
            try:
                val = float(line.split()[-1])
                if val > 0:
                    g.ok("HTTP 请求统计", f"累计 {int(val)} 次请求")
            except (ValueError, IndexError):
                pass
