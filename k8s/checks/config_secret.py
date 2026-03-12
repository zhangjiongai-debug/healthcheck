"""2.7 配置与密钥检查。"""

from kubernetes import client as kclient
from ..result import CheckGroup, Severity


def check(clients: dict) -> CheckGroup:
    g = CheckGroup("2.7 配置与密钥检查")
    core: kclient.CoreV1Api = clients["core"]

    # ━━━━━ 检查 Pod 引用的 ConfigMap / Secret 是否存在 ━━━━━
    try:
        pods = core.list_pod_for_all_namespaces()
    except Exception as e:
        g.error("Pod 列表", f"获取失败: {e}")
        return g

    missing_cms = set()
    missing_secrets = set()

    for pod in pods.items:
        ns = pod.metadata.namespace
        spec = pod.spec

        # 从 volumes 中收集引用
        for vol in (spec.volumes or []):
            if vol.config_map:
                cm_name = vol.config_map.name
                if not _resource_exists(core.read_namespaced_config_map, cm_name, ns):
                    if not vol.config_map.optional:
                        missing_cms.add(f"{ns}/{cm_name}")

            if vol.secret:
                sec_name = vol.secret.secret_name
                if not _resource_exists(core.read_namespaced_secret, sec_name, ns):
                    if not vol.secret.optional:
                        missing_secrets.add(f"{ns}/{sec_name}")

            if vol.projected and vol.projected.sources:
                for src in vol.projected.sources:
                    if src.config_map and not src.config_map.optional:
                        if not _resource_exists(core.read_namespaced_config_map, src.config_map.name, ns):
                            missing_cms.add(f"{ns}/{src.config_map.name}")
                    if src.secret and not src.secret.optional:
                        if not _resource_exists(core.read_namespaced_secret, src.secret.name, ns):
                            missing_secrets.add(f"{ns}/{src.secret.name}")

        # 从 envFrom 和 env valueFrom 中收集引用
        for container in (spec.containers or []) + (spec.init_containers or []):
            for env_from in (container.env_from or []):
                if env_from.config_map_ref:
                    ref = env_from.config_map_ref
                    if not ref.optional and not _resource_exists(core.read_namespaced_config_map, ref.name, ns):
                        missing_cms.add(f"{ns}/{ref.name}")
                if env_from.secret_ref:
                    ref = env_from.secret_ref
                    if not ref.optional and not _resource_exists(core.read_namespaced_secret, ref.name, ns):
                        missing_secrets.add(f"{ns}/{ref.name}")

            for env in (container.env or []):
                if env.value_from:
                    if env.value_from.config_map_key_ref:
                        ref = env.value_from.config_map_key_ref
                        if not ref.optional and not _resource_exists(core.read_namespaced_config_map, ref.name, ns):
                            missing_cms.add(f"{ns}/{ref.name}")
                    if env.value_from.secret_key_ref:
                        ref = env.value_from.secret_key_ref
                        if not ref.optional and not _resource_exists(core.read_namespaced_secret, ref.name, ns):
                            missing_secrets.add(f"{ns}/{ref.name}")

    if not missing_cms:
        g.ok("ConfigMap 引用", "所有引用的 ConfigMap 均存在")
    else:
        g.error("ConfigMap 缺失", f"{len(missing_cms)} 个被引用的 ConfigMap 不存在",
                detail="\n".join(sorted(missing_cms)[:20]))

    if not missing_secrets:
        g.ok("Secret 引用", "所有引用的 Secret 均存在")
    else:
        g.error("Secret 缺失", f"{len(missing_secrets)} 个被引用的 Secret 不存在",
                detail="\n".join(sorted(missing_secrets)[:20]))

    # ━━━━━ TLS Secret 格式检查 ━━━━━
    try:
        secrets = core.list_secret_for_all_namespaces(field_selector="type=kubernetes.io/tls")
        bad_tls = []
        for sec in secrets.items:
            data = sec.data or {}
            if "tls.crt" not in data or "tls.key" not in data:
                bad_tls.append(f"{sec.metadata.namespace}/{sec.metadata.name}: 缺少 tls.crt 或 tls.key")

        if not bad_tls:
            g.ok("TLS Secret", f"共 {len(secrets.items)} 个，格式正确")
        else:
            g.error("TLS Secret 格式", f"{len(bad_tls)} 个格式异常",
                    detail="\n".join(bad_tls[:20]))
    except Exception as e:
        g.warn("TLS Secret", f"检查失败: {e}")

    return g


# 缓存已检查过的资源存在性
_existence_cache: dict[str, bool] = {}


def _resource_exists(read_func, name: str, namespace: str) -> bool:
    key = f"{read_func.__name__}:{namespace}/{name}"
    if key in _existence_cache:
        return _existence_cache[key]
    try:
        read_func(name, namespace)
        _existence_cache[key] = True
        return True
    except kclient.ApiException as e:
        if e.status == 404:
            _existence_cache[key] = False
            return False
        # 其他错误当作存在(权限不足等)
        _existence_cache[key] = True
        return True
    except Exception:
        _existence_cache[key] = True
        return True
