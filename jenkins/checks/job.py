"""5. Job / Pipeline 检查。

- 最近构建是否成功
- 失败率是否升高
- 是否存在长期卡住的构建
- 构建队列是否堆积
- pipeline stage 是否异常中断
- SCM 拉取是否成功
- 构建产物上传是否正常
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5. Job / Pipeline 检查")
    jk = ctx["jk"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── 构建队列 ──
    _check_build_queue(jk, g)

    # ── Job 概览 ──
    _check_job_summary(jk, g)

    # ── 卡住的构建 ──
    _check_stuck_builds(jk, g)

    # ── 最近失败率 ──
    _check_recent_failures(jk, g)

    return g


def _check_build_queue(jk, g):
    """检查构建队列。"""
    resp = jk.api_json("/queue",
                       tree="items[id,task[name],why,buildableStartMilliseconds,stuck]")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        items = resp["body"].get("items", [])
        if not items:
            g.ok("构建队列", "队列为空")
            return

        stuck = [i for i in items if i.get("stuck")]
        g.ok("构建队列", f"{len(items)} 个任务排队中")

        if stuck:
            detail = "\n".join(
                f"{s.get('task', {}).get('name', '?')}: {s.get('why', '')[:100]}"
                for s in stuck[:5])
            g.error("卡住的排队任务", f"{len(stuck)} 个任务卡住", detail=detail)

        if len(items) > 20:
            g.warn("队列积压", f"队列有 {len(items)} 个任务，可能积压")
        return

    # 降级 Groovy
    result = jk.script_console("""
def queue = Jenkins.instance.queue
def items = queue.items
println("QUEUE_SIZE:${items.size()}")
def stuck = items.findAll { it.isStuck() }
println("STUCK:${stuck.size()}")
stuck.take(5).each { println("STUCK_ITEM:${it.task.name}: ${it.why?.take(100) ?: ''}") }
""")
    if result is None:
        g.warn("构建队列", "无法获取队列信息")
        return

    for line in result.strip().splitlines():
        if line.startswith("QUEUE_SIZE:"):
            size = int(line.split(":")[1])
            if size == 0:
                g.ok("构建队列", "队列为空")
            elif size > 20:
                g.warn("构建队列", f"{size} 个任务排队 (可能积压)")
            else:
                g.ok("构建队列", f"{size} 个任务排队中")
        elif line.startswith("STUCK:"):
            count = int(line.split(":")[1])
            if count > 0:
                g.error("卡住的排队任务", f"{count} 个任务卡住")


def _check_job_summary(jk, g):
    """Job 概览统计。"""
    result = jk.script_console("""
def jobs = Jenkins.instance.allItems(hudson.model.Job)
def total = jobs.size()
def disabled = jobs.findAll { it.hasProperty('disabled') && it.disabled }.size()
println("TOTAL_JOBS:${total}")
println("DISABLED:${disabled}")
""")
    if result is None:
        # 降级: API
        resp = jk.api_json(tree="jobs[name,color]")
        if resp["status"] == 200 and isinstance(resp["body"], dict):
            jobs = resp["body"].get("jobs", [])
            g.ok("Job 总数", f"{len(jobs)} 个顶层 Job")
            failed = [j for j in jobs if j.get("color", "").startswith("red")]
            if failed:
                g.warn("失败 Job", f"{len(failed)} 个 Job 最近构建失败",
                       detail=", ".join(j["name"] for j in failed[:10]))
        return

    for line in result.strip().splitlines():
        if line.startswith("TOTAL_JOBS:"):
            g.ok("Job 总数", f"{line.split(':')[1]} 个 Job (含所有层级)")
        elif line.startswith("DISABLED:"):
            count = int(line.split(":")[1])
            if count > 0:
                g.ok("停用 Job", f"{count} 个 Job 已停用")


def _check_stuck_builds(jk, g):
    """检查是否有长期运行的构建 (可能卡住)。"""
    result = jk.script_console("""
def running = []
Jenkins.instance.allItems(hudson.model.Job).each { job ->
    if (job.isBuilding()) {
        def build = job.lastBuild
        if (build) {
            def duration = System.currentTimeMillis() - build.startTimeInMillis
            def hours = duration / (1000 * 3600)
            if (hours > 2) {
                running << "${job.fullName}#${build.number}: ${String.format('%.1f', hours)}h"
            }
        }
    }
}
println("LONG_RUNNING:${running.size()}")
running.take(10).each { println(it) }
""")
    if result is None:
        return

    lines = result.strip().splitlines()
    for line in lines:
        if line.startswith("LONG_RUNNING:"):
            count = int(line.split(":")[1])
            if count > 0:
                detail = "\n".join(l for l in lines[1:] if l.strip())
                g.warn("长时间构建", f"{count} 个构建运行超过 2 小时 (可能卡住)",
                       detail=detail)
            else:
                g.ok("长时间构建", "无异常长时间构建")


def _check_recent_failures(jk, g):
    """检查最近的构建失败率。"""
    result = jk.script_console("""
def now = System.currentTimeMillis()
def dayAgo = now - 24 * 3600 * 1000
def total = 0
def failed = 0
Jenkins.instance.allItems(hudson.model.Job).each { job ->
    job.builds.each { build ->
        if (build.startTimeInMillis > dayAgo) {
            total++
            if (build.result?.toString() in ['FAILURE', 'ABORTED']) {
                failed++
            }
        }
        if (build.startTimeInMillis < dayAgo) return // 跳过更早的
    }
}
println("24H_TOTAL:${total}")
println("24H_FAILED:${failed}")
""")
    if result is None:
        return

    total = 0
    failed = 0
    for line in result.strip().splitlines():
        if line.startswith("24H_TOTAL:"):
            total = int(line.split(":")[1])
        elif line.startswith("24H_FAILED:"):
            failed = int(line.split(":")[1])

    if total > 0:
        fail_rate = (failed / total) * 100
        if fail_rate > 50:
            g.error("24h 构建失败率", f"{fail_rate:.0f}% ({failed}/{total})")
        elif fail_rate > 30:
            g.warn("24h 构建失败率", f"{fail_rate:.0f}% ({failed}/{total})")
        else:
            g.ok("24h 构建失败率", f"{fail_rate:.0f}% ({failed}/{total})")
    else:
        g.ok("24h 构建", "过去 24 小时无构建记录")
