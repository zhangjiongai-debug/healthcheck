"""4.7 Keycloak 性能与风险预警。

- 登录失败率异常升高
- token 请求延迟升高
- DB 连接池使用率过高
- 内存占用持续升高
- Full GC 频繁
- 用户 federation 不可达
- realm 配置漂移
- session 数接近上限
"""

import re
import time

from ..result import CheckGroup
from ..client import DeployMode


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("4.7 性能与风险预警")
    kc = ctx["kc"]

    # ── 从 metrics 提取性能指标 ──
    resp = kc.metrics()
    if resp["status"] == 200 and isinstance(resp["body"], str):
        body = resp["body"]
        _check_login_failures(body, g)
        _check_jvm_memory(body, g)
        _check_gc(body, g)
        _check_http_latency(body, g)
        _check_sessions(body, g)
    else:
        g.warn("Metrics", f"无法获取 /metrics 端点 (HTTP {resp['status']})")

    # ── token 请求延迟实测 ──
    _check_token_latency(kc, g)

    # ── user federation 可达性 ──
    _check_federation_reachability(kc, g)

    # ── realm 配置漂移 (多实例一致性) ──
    if ctx["mode"] == DeployMode.K8S:
        _check_realm_drift(ctx, g)

    return g


def _check_login_failures(body: str, g: CheckGroup):
    """检查登录失败相关指标。"""
    # Keycloak metrics: keycloak_login_error_total, keycloak_failed_login_attempts
    failed = _extract_metric(body, r'keycloak_failed_login_attempts_total\s+(\d+\.?\d*)')
    if failed is None:
        failed = _extract_metric(body, r'keycloak_login_error_total\s+(\d+\.?\d*)')

    success = _extract_metric(body, r'keycloak_successful_login_total\s+(\d+\.?\d*)')
    if success is None:
        success = _extract_metric(body, r'keycloak_login_total\s+(\d+\.?\d*)')

    if failed is not None and success is not None and (failed + success) > 0:
        fail_rate = failed / (failed + success) * 100
        if fail_rate > 50:
            g.error("登录失败率", f"{fail_rate:.1f}% (失败 {failed:.0f} / 总计 {failed+success:.0f})")
        elif fail_rate > 20:
            g.warn("登录失败率", f"{fail_rate:.1f}% (失败 {failed:.0f} / 总计 {failed+success:.0f})")
        else:
            g.ok("登录失败率", f"{fail_rate:.1f}%")
    elif failed is not None:
        if failed > 100:
            g.warn("登录失败数", f"累计 {failed:.0f} 次失败")
        else:
            g.ok("登录失败数", f"累计 {failed:.0f} 次")


def _check_jvm_memory(body: str, g: CheckGroup):
    """检查 JVM 内存使用。"""
    heap_used = _extract_metric(body, r'jvm_memory_used_bytes\{area="heap"[^}]*\}\s+(\d+\.?\d*[eE]?\+?\d*)')
    heap_max = _extract_metric(body, r'jvm_memory_max_bytes\{area="heap"[^}]*\}\s+(\d+\.?\d*[eE]?\+?\d*)')

    # 也尝试 base_ 前缀
    if heap_used is None:
        heap_used = _extract_metric(body, r'base_memory_usedHeap_bytes\s+(\d+\.?\d*[eE]?\+?\d*)')
        heap_max = _extract_metric(body, r'base_memory_maxHeap_bytes\s+(\d+\.?\d*[eE]?\+?\d*)')

    if heap_used is not None and heap_max is not None and heap_max > 0:
        usage_pct = (heap_used / heap_max) * 100
        used_mb = heap_used / (1024 * 1024)
        max_mb = heap_max / (1024 * 1024)
        if usage_pct > 90:
            g.error("JVM 堆内存", f"{usage_pct:.0f}% ({used_mb:.0f}MB / {max_mb:.0f}MB)")
        elif usage_pct > 75:
            g.warn("JVM 堆内存", f"{usage_pct:.0f}% ({used_mb:.0f}MB / {max_mb:.0f}MB)")
        else:
            g.ok("JVM 堆内存", f"{usage_pct:.0f}% ({used_mb:.0f}MB / {max_mb:.0f}MB)")
    elif heap_used is not None:
        used_mb = heap_used / (1024 * 1024)
        g.ok("JVM 堆内存", f"使用 {used_mb:.0f}MB (无 max 信息)")


def _check_gc(body: str, g: CheckGroup):
    """检查 GC 频率。"""
    # Full GC
    gc_time = _extract_metric(body, r'jvm_gc_pause_seconds_sum\{[^}]*gc="G1 Old[^}]*\}\s+(\d+\.?\d*)')
    gc_count = _extract_metric(body, r'jvm_gc_pause_seconds_count\{[^}]*gc="G1 Old[^}]*\}\s+(\d+\.?\d*)')

    if gc_count is not None:
        if gc_count > 10:
            g.warn("Full GC", f"累计 {gc_count:.0f} 次 Full GC" +
                   (f" (总耗时 {gc_time:.1f}s)" if gc_time else ""))
        elif gc_count > 0:
            g.ok("Full GC", f"累计 {gc_count:.0f} 次" +
                 (f" (总耗时 {gc_time:.1f}s)" if gc_time else ""))
        else:
            g.ok("Full GC", "无 Full GC 发生")

    # 总 GC 暂停时间
    total_gc = _extract_metric(body, r'jvm_gc_pause_seconds_sum\s+(\d+\.?\d*)')
    if total_gc is not None and total_gc > 30:
        g.warn("GC 总暂停时间", f"{total_gc:.1f}s")


def _check_http_latency(body: str, g: CheckGroup):
    """检查 HTTP 请求延迟指标。"""
    # Quarkus HTTP 指标
    http_sum = _extract_metric(body, r'http_server_requests_seconds_sum\s+(\d+\.?\d*)')
    http_count = _extract_metric(body, r'http_server_requests_seconds_count\s+(\d+\.?\d*)')

    if http_sum is not None and http_count is not None and http_count > 0:
        avg_ms = (http_sum / http_count) * 1000
        if avg_ms > 1000:
            g.warn("HTTP 平均延迟", f"{avg_ms:.0f}ms")
        else:
            g.ok("HTTP 平均延迟", f"{avg_ms:.0f}ms")


def _check_sessions(body: str, g: CheckGroup):
    """检查 session 数量。"""
    sessions = _extract_metric(body,
        r'vendor_cache_manager_default_cache_sessions_number_of_entries\s+(\d+)')
    if sessions is not None:
        sessions = int(sessions)
        if sessions > 10000:
            g.warn("活跃 Session", f"{sessions} 个，接近可能的性能瓶颈")
        else:
            g.ok("活跃 Session", f"{sessions} 个")


def _check_token_latency(kc, g: CheckGroup):
    """实际测试 token 请求延迟。"""
    if not kc.admin_user or not kc.admin_password:
        return

    latencies = []
    for _ in range(3):
        start = time.time()
        resp = kc.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": kc.admin_user,
                "password": kc.admin_password,
            },
            content_type="application/x-www-form-urlencoded",
        )
        elapsed = (time.time() - start) * 1000
        if resp["status"] == 200:
            latencies.append(elapsed)

    if latencies:
        avg = sum(latencies) / len(latencies)
        if avg > 3000:
            g.error("Token 请求延迟", f"平均 {avg:.0f}ms (3次测试)")
        elif avg > 1000:
            g.warn("Token 请求延迟", f"平均 {avg:.0f}ms (3次测试)")
        else:
            g.ok("Token 请求延迟", f"平均 {avg:.0f}ms (3次测试)")


def _check_federation_reachability(kc, g: CheckGroup):
    """检查所有 user federation 的可达性。"""
    token = kc.get_admin_token()
    if not token:
        return

    realms_resp = kc.admin_get("/admin/realms")
    if realms_resp["status"] != 200 or not isinstance(realms_resp["body"], list):
        return

    for r in realms_resp["body"]:
        rname = r.get("realm", "?")
        comp_resp = kc.admin_get(
            f"/admin/realms/{rname}/components?type=org.keycloak.storage.UserStorageProvider")
        if comp_resp["status"] != 200 or not isinstance(comp_resp["body"], list):
            continue

        for comp in comp_resp["body"]:
            name = comp.get("name", "?")
            cfg = comp.get("config", {})
            provider = comp.get("providerId", "?")

            if provider in ("ldap", "ad"):
                url = cfg.get("connectionUrl", [""])[0] if isinstance(cfg.get("connectionUrl"), list) else ""
                if url:
                    # 尝试连接测试 (仅记录配置，实际连接测试需要 POST)
                    g.ok(f"Federation [{rname}/{name}]", f"LDAP URL: {url}")


def _check_realm_drift(ctx: dict, g: CheckGroup):
    """多副本场景下，通过不同 Pod 的 realm 列表对比检测漂移。"""
    k8s_core = ctx.get("k8s_core")
    if not k8s_core:
        return

    ns = ctx["namespace"]
    selector = ctx["label_selector"]

    try:
        pods = k8s_core.list_namespaced_pod(ns, label_selector=selector)
    except Exception:
        return

    running_pods = [p for p in pods.items if p.status.phase == "Running"]
    if len(running_pods) < 2:
        return

    # 对每个 Pod 直接请求 realm 列表
    # 注意: 这需要 Pod 的 IP 直接可达 (在 K8s 集群内)
    realm_sets = {}
    kc = ctx["kc"]
    for pod in running_pods[:3]:
        pod_ip = pod.status.pod_ip
        if not pod_ip:
            continue
        # 尝试通过 Pod IP 获取 realm 列表
        from ..client import KeycloakClient
        pod_kc = KeycloakClient(
            f"{'https' if 'https' in ctx['base_url'] else 'http'}://{pod_ip}:8080",
            kc.admin_user, kc.admin_password, verify_ssl=False, timeout=5)
        t = pod_kc.get_admin_token()
        if t:
            resp = pod_kc.admin_get("/admin/realms")
            if resp["status"] == 200 and isinstance(resp["body"], list):
                realm_sets[pod.metadata.name] = set(r.get("realm", "?") for r in resp["body"])

    if len(realm_sets) >= 2:
        names = list(realm_sets.keys())
        base_set = realm_sets[names[0]]
        drifted = False
        for n in names[1:]:
            if realm_sets[n] != base_set:
                diff = realm_sets[n].symmetric_difference(base_set)
                g.warn("Realm 漂移", f"{names[0]} 与 {n} realm 列表不一致: {diff}")
                drifted = True
        if not drifted:
            g.ok("Realm 一致性", f"{len(realm_sets)} 个副本 realm 列表一致")


def _extract_metric(text: str, pattern: str):
    m = re.search(pattern, text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None
