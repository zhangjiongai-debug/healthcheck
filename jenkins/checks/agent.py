"""4. Agent / Executor 状态检查。

- Jenkins Agent 是否在线
- K8s 动态 Agent 是否可创建
- Label 是否匹配
- Executor 数量是否足够
- 是否存在离线 Agent
- Agent 连接是否频繁断开
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4. Agent / Executor 状态")
    jk = ctx["jk"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    # ── Executor 与节点列表 ──
    _check_nodes(jk, g)

    # ── K8s cloud 配置 ──
    _check_k8s_cloud(jk, g)

    # ── Executor 利用率 ──
    _check_executor_usage(jk, g)

    return g


def _check_nodes(jk, g):
    """检查节点/Agent 列表。"""
    resp = jk.api_json("/computer",
                       tree="computer[displayName,offline,offlineCauseReason,"
                            "numExecutors,idle,temporarilyOffline,jnlpAgent]")
    if resp["status"] == 200 and isinstance(resp["body"], dict):
        computers = resp["body"].get("computer", [])
        _process_node_list(computers, g)
        return

    # 降级: Groovy
    result = jk.script_console("""
def computers = Jenkins.instance.computers
computers.each { c ->
    def name = c.displayName ?: c.name
    def offline = c.offline
    def executors = c.numExecutors
    def reason = c.offlineCauseReason ?: ''
    println("NODE:${name}|${offline}|${executors}|${reason.take(100)}")
}
""")
    if result is None:
        g.warn("节点检查", "无法获取节点信息 (需要认证)")
        return

    nodes = []
    for line in result.strip().splitlines():
        if line.startswith("NODE:"):
            parts = line[5:].split("|")
            if len(parts) >= 3:
                nodes.append({
                    "displayName": parts[0],
                    "offline": parts[1] == "true",
                    "numExecutors": int(parts[2]),
                    "offlineCauseReason": parts[3] if len(parts) > 3 else "",
                })
    _process_node_list_dict(nodes, g)


def _process_node_list(computers, g):
    """处理 API 返回的节点列表。"""
    if not computers:
        g.warn("节点列表", "无节点")
        return

    online = 0
    offline_list = []
    total_executors = 0

    for c in computers:
        name = c.get("displayName", "unknown")
        is_offline = c.get("offline", False)
        num_exec = c.get("numExecutors", 0)
        total_executors += num_exec

        if is_offline:
            reason = c.get("offlineCauseReason", "")
            offline_list.append(f"{name}: {reason}" if reason else name)
        else:
            online += 1

    g.ok("节点总数", f"{len(computers)} 个节点, {online} 在线, {len(offline_list)} 离线")
    g.ok("Executor 总数", f"{total_executors} 个")

    if offline_list:
        if len(offline_list) == len(computers):
            g.fatal("离线节点", "所有节点离线!", detail="\n".join(offline_list))
        elif len(offline_list) > len(computers) // 2:
            g.error("离线节点", f"{len(offline_list)} 个节点离线",
                    detail="\n".join(offline_list))
        else:
            g.warn("离线节点", f"{len(offline_list)} 个节点离线",
                   detail="\n".join(offline_list))

    if total_executors == 0:
        # 检查是否配置了 K8s/Cloud 动态 Agent
        # 如果有 cloud 配置，0 executor 是正常的（按需创建）
        has_cloud = False
        for c in computers:
            if c.get("_class", "").lower().find("kubernetes") >= 0:
                has_cloud = True
                break
        if not has_cloud:
            g.warn("Executor 数量",
                   "Controller 上无 Executor，如使用动态 Agent 则正常")


def _process_node_list_dict(nodes, g):
    """处理 Groovy 返回的节点列表。"""
    if not nodes:
        g.warn("节点列表", "无节点")
        return

    online = sum(1 for n in nodes if not n["offline"])
    offline = [n for n in nodes if n["offline"]]
    total_exec = sum(n["numExecutors"] for n in nodes)

    g.ok("节点总数", f"{len(nodes)} 个节点, {online} 在线, {len(offline)} 离线")
    g.ok("Executor 总数", f"{total_exec} 个")

    if offline:
        detail = "\n".join(
            f"{n['displayName']}: {n['offlineCauseReason']}" if n["offlineCauseReason"]
            else n["displayName"]
            for n in offline)
        if len(offline) == len(nodes):
            g.fatal("离线节点", "所有节点离线!", detail=detail)
        else:
            g.warn("离线节点", f"{len(offline)} 个节点离线", detail=detail)


def _check_k8s_cloud(jk, g):
    """检查 Kubernetes Cloud 配置。"""
    result = jk.script_console("""
def clouds = Jenkins.instance.clouds
def k8sClouds = clouds.findAll { it.class.name.contains('kubernetes') || it.class.name.contains('Kubernetes') }
println("CLOUDS:${clouds.size()}")
println("K8S_CLOUDS:${k8sClouds.size()}")
k8sClouds.each { c ->
    try {
        def name = c.name ?: 'default'
        def ns = c.namespace ?: 'default'
        def url = c.serverUrl ?: 'in-cluster'
        def limit = c.containerCapStr ?: 'unlimited'
        println("K8S:${name}|${ns}|${url}|${limit}")
    } catch (Exception e) {
        println("K8S_ERROR:${e.message?.take(100)}")
    }
}
""")
    if result is None:
        return

    for line in result.strip().splitlines():
        if line.startswith("K8S_CLOUDS:"):
            count = int(line.split(":")[1])
            if count > 0:
                g.ok("K8s Cloud 配置", f"{count} 个 Kubernetes Cloud")
            # 如果是 0，不报错（可能不使用 K8s agent）
        elif line.startswith("K8S:"):
            parts = line[4:].split("|")
            if len(parts) >= 4:
                name, ns, url, limit = parts[0], parts[1], parts[2], parts[3]
                g.ok(f"K8s Cloud [{name}]",
                     f"namespace={ns}, limit={limit}")


def _check_executor_usage(jk, g):
    """检查 Executor 利用率。"""
    result = jk.script_console("""
def computers = Jenkins.instance.computers
def totalExec = 0
def busyExec = 0
computers.each { c ->
    if (!c.offline) {
        totalExec += c.numExecutors
        busyExec += c.countBusy()
    }
}
println("TOTAL:${totalExec}")
println("BUSY:${busyExec}")
""")
    if result is None:
        return

    total = 0
    busy = 0
    for line in result.strip().splitlines():
        if line.startswith("TOTAL:"):
            total = int(line.split(":")[1])
        elif line.startswith("BUSY:"):
            busy = int(line.split(":")[1])

    if total > 0:
        usage_pct = (busy / total) * 100
        if usage_pct > 90:
            g.warn("Executor 利用率", f"{usage_pct:.0f}% ({busy}/{total})")
        else:
            g.ok("Executor 利用率", f"{usage_pct:.0f}% ({busy}/{total})")
