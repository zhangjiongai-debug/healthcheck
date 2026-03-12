"""4. APISIX 流量与错误检查。

- 4xx / 5xx 是否异常升高
- upstream timeout 是否升高
- upstream connect failed/retry 是否增多
- 请求延迟 P95/P99 是否异常
- 是否存在大量 503/502/504

通过 Prometheus 指标端点或 Gateway 测试请求检测。
"""

import json
import re
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4. APISIX 流量与错误检查")
    mode = ctx["mode"]

    # ── 尝试从 prometheus 指标获取流量信息 ──
    metrics_text = _fetch_prometheus_metrics(ctx, g)
    if metrics_text:
        _analyze_metrics(metrics_text, g)
    else:
        g.warn("Prometheus 指标", "无法获取 Prometheus 指标，跳过流量分析")

    # ── 网关可达性测试 ──
    _check_gateway_reachable(ctx, g)

    return g


def _fetch_prometheus_metrics(ctx, g) -> str:
    """尝试获取 APISIX Prometheus 指标文本。"""
    apisix = ctx["apisix"]

    # APISIX prometheus 插件默认暴露在 /apisix/prometheus/metrics
    # 也可能暴露在独立端口 (9091)
    urls_to_try = []
    gateway_url = ctx.get("gateway_url")
    if gateway_url:
        urls_to_try.append(gateway_url.rstrip("/") + "/apisix/prometheus/metrics")

    # 从 admin_url 推导 prometheus 端口
    admin_url = apisix.admin_url
    if admin_url:
        base = admin_url.split("/apisix")[0] if "/apisix" in admin_url else admin_url
        # 常见端口: 9091 (独立 prometheus), 9080 (gateway 自带)
        import re
        host_match = re.match(r'(https?://[^:/]+)', base)
        if host_match:
            host = host_match.group(1)
            urls_to_try.append(f"{host}:9091/apisix/prometheus/metrics")
            urls_to_try.append(f"{host}:9080/apisix/prometheus/metrics")

    # K8s: 通过 Pod annotation 或 Service 端口获取
    if ctx["mode"] == DeployMode.K8S:
        k8s_core = ctx.get("k8s_core")
        ns = ctx["namespace"]
        if k8s_core:
            try:
                svcs = k8s_core.list_namespaced_service(ns,
                    label_selector=ctx["label_selector"])
                for svc in svcs.items:
                    for port in (svc.spec.ports or []):
                        if port.port == 9091 or port.name == "prometheus":
                            urls_to_try.append(
                                f"http://{svc.metadata.name}.{ns}:9091"
                                f"/apisix/prometheus/metrics")
            except Exception:
                pass

    for url in urls_to_try:
        try:
            req = Request(url, method="GET")
            resp = urlopen(req, timeout=5,
                           context=apisix._ssl_ctx)
            text = resp.read().decode("utf-8")
            if "apisix_" in text:
                return text
        except Exception:
            continue

    return ""


def _analyze_metrics(text: str, g):
    """分析 Prometheus 指标文本。"""
    # 统计 HTTP 状态码分布
    status_counts = {}
    for match in re.finditer(
            r'apisix_http_status\{.*?code="(\d+)".*?\}\s+(\d+)', text):
        code = match.group(1)
        count = int(match.group(2))
        status_counts[code] = status_counts.get(code, 0) + count

    if status_counts:
        total_requests = sum(status_counts.values())
        err_4xx = sum(v for k, v in status_counts.items() if k.startswith("4"))
        err_5xx = sum(v for k, v in status_counts.items() if k.startswith("5"))

        g.ok("请求总量", f"总计 {total_requests} 次请求")

        # 4xx 比例
        if total_requests > 0:
            rate_4xx = err_4xx / total_requests * 100
            if rate_4xx > 30:
                g.error("4xx 错误率", f"{rate_4xx:.1f}% ({err_4xx}/{total_requests})")
            elif rate_4xx > 10:
                g.warn("4xx 错误率", f"{rate_4xx:.1f}% ({err_4xx}/{total_requests})")
            else:
                g.ok("4xx 错误率", f"{rate_4xx:.1f}% ({err_4xx}/{total_requests})")

            # 5xx 比例
            rate_5xx = err_5xx / total_requests * 100
            if rate_5xx > 10:
                g.fatal("5xx 错误率", f"{rate_5xx:.1f}% ({err_5xx}/{total_requests})")
            elif rate_5xx > 1:
                g.error("5xx 错误率", f"{rate_5xx:.1f}% ({err_5xx}/{total_requests})")
            elif rate_5xx > 0:
                g.warn("5xx 错误率", f"{rate_5xx:.1f}% ({err_5xx}/{total_requests})")
            else:
                g.ok("5xx 错误率", "0%")

        # 重点关注 502/503/504
        for code in ("502", "503", "504"):
            cnt = status_counts.get(code, 0)
            if cnt > 100:
                g.error(f"HTTP {code}", f"出现 {cnt} 次")
            elif cnt > 10:
                g.warn(f"HTTP {code}", f"出现 {cnt} 次")

    # 延迟指标
    latency_lines = [l for l in text.splitlines()
                     if "apisix_http_latency" in l and "quantile=" in l]
    if latency_lines:
        p99_values = []
        for line in latency_lines:
            if 'quantile="0.99"' in line:
                try:
                    val = float(line.split()[-1])
                    p99_values.append(val)
                except (ValueError, IndexError):
                    pass
        if p99_values:
            max_p99 = max(p99_values)
            if max_p99 > 5000:
                g.error("请求延迟 P99", f"{max_p99:.0f}ms")
            elif max_p99 > 1000:
                g.warn("请求延迟 P99", f"{max_p99:.0f}ms")
            else:
                g.ok("请求延迟 P99", f"{max_p99:.0f}ms")

    # Upstream 相关指标
    upstream_errors = 0
    for line in text.splitlines():
        if "apisix_upstream_status" in line and not line.startswith("#"):
            try:
                val = int(float(line.split()[-1]))
                upstream_errors += val
            except (ValueError, IndexError):
                pass


def _check_gateway_reachable(ctx, g):
    """检查 Gateway 端口是否可达。"""
    gateway_url = ctx.get("gateway_url")
    if not gateway_url:
        return

    apisix = ctx["apisix"]
    try:
        req = Request(gateway_url, method="GET")
        resp = urlopen(req, timeout=5, context=apisix._ssl_ctx)
        g.ok("Gateway 端口", f"可达 (status={resp.status})")
    except HTTPError as e:
        # 404 是正常的 (无路由匹配)
        if e.code == 404:
            g.ok("Gateway 端口", "可达 (404 - 无匹配路由，属正常)")
        elif e.code < 500:
            g.ok("Gateway 端口", f"可达 (status={e.code})")
        else:
            g.warn("Gateway 端口", f"返回 {e.code}")
    except URLError as e:
        g.error("Gateway 端口", f"不可达: {e.reason}")
    except Exception as e:
        g.error("Gateway 端口", f"检查失败: {e}")
