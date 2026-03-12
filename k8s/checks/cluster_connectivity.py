"""2.1 集群基础连通性与 API 健康检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity
from ..client import measure_api_latency


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.1 集群基础连通性与 API 健康")
    core: kclient.CoreV1Api = clients["core"]
    version_api: kclient.VersionApi = clients["version"]
    api_client: kclient.ApiClient = clients["api_client"]

    # ── API Server 可达性 ──
    # /readyz
    try:
        resp = api_client.call_api(
            "/readyz", "GET", response_type="str",
            auth_settings=["BearerToken"], _return_http_data_only=True,
        )
        if resp == "ok":
            g.ok("API /readyz", "返回 ok")
        else:
            g.warn("API /readyz", f"返回: {resp}")
    except Exception as e:
        g.error("API /readyz", f"请求失败: {e}")

    # /livez
    try:
        resp = api_client.call_api(
            "/livez", "GET", response_type="str",
            auth_settings=["BearerToken"], _return_http_data_only=True,
        )
        if resp == "ok":
            g.ok("API /livez", "返回 ok")
        else:
            g.warn("API /livez", f"返回: {resp}")
    except Exception as e:
        g.error("API /livez", f"请求失败: {e}")

    # ── API Server 响应延迟 ──
    try:
        latency = measure_api_latency(core)
        if latency < 200:
            g.ok("API 响应延迟", f"平均 {latency:.0f}ms")
        elif latency < 1000:
            g.warn("API 响应延迟", f"平均 {latency:.0f}ms，偏高")
        else:
            g.error("API 响应延迟", f"平均 {latency:.0f}ms，异常")
    except Exception as e:
        g.error("API 响应延迟", f"测量失败: {e}")

    # ── 集群版本 ──
    try:
        ver = version_api.get_code()
        g.ok("Kubernetes 版本", f"{ver.git_version}")
    except Exception as e:
        g.error("Kubernetes 版本", f"获取失败: {e}")

    # ── 节点 kubelet 版本一致性 ──
    try:
        nodes = core.list_node()
        versions = set()
        for n in nodes.items:
            versions.add(n.status.node_info.kubelet_version)
        if len(versions) == 1:
            g.ok("kubelet 版本一致性", f"所有节点: {versions.pop()}")
        elif len(versions) <= 2:
            g.warn("kubelet 版本一致性", f"存在 {len(versions)} 个版本", detail="\n".join(sorted(versions)))
        else:
            g.error("kubelet 版本一致性", f"版本偏差过大 ({len(versions)} 个版本)", detail="\n".join(sorted(versions)))
    except Exception as e:
        g.error("kubelet 版本一致性", f"检查失败: {e}")

    return g
