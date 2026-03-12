"""MinIO 客户端初始化与部署模式检测。

支持三种部署模式:
  - k8s:    通过 kubernetes API 获取 Pod/容器状态
  - docker: 通过 docker SDK/CLI 获取容器状态
  - vm:     仅通过 HTTP/S3 接口检查
"""

import importlib
import json
import ssl
import subprocess
import sys
from enum import Enum
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


_minio_sdk_class = None


def _import_minio_sdk():
    """导入 minio SDK 的 Minio 类，避免被本地 minio 包遮蔽。"""
    global _minio_sdk_class
    if _minio_sdk_class is not None:
        return _minio_sdk_class

    import os

    # 保存当前 sys.modules 中本地 minio 相关的模块
    saved = {}
    for key in list(sys.modules.keys()):
        if key == "minio" or key.startswith("minio."):
            saved[key] = sys.modules.pop(key)

    # 临时修改 sys.path: 移除包含本地 minio 的路径
    cwd = os.getcwd()
    orig_path = sys.path[:]
    sys.path = [p for p in sys.path
                if not os.path.isfile(os.path.join(p, "minio", "client.py"))
                or "site-packages" in p]

    try:
        import minio as sdk  # noqa: now imports the real SDK
        _minio_sdk_class = getattr(sdk, "Minio", None)
    except (ImportError, AttributeError):
        _minio_sdk_class = None
    finally:
        # 恢复
        sys.path = orig_path
        # 清掉 SDK 模块引用，恢复本地模块
        for key in list(sys.modules.keys()):
            if key == "minio" or key.startswith("minio."):
                if key not in saved:
                    del sys.modules[key]
        sys.modules.update(saved)

    return _minio_sdk_class


class DeployMode(Enum):
    K8S = "k8s"
    DOCKER = "docker"
    VM = "vm"


class MinioClient:
    """MinIO HTTP 客户端，封装健康端点、S3 基础操作和管理 API 调用。"""

    def __init__(self, endpoint: str, access_key: str = None, secret_key: str = None,
                 secure: bool = False, verify_ssl: bool = True, timeout: int = 10):
        self.endpoint = endpoint.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.secure = secure
        self.timeout = timeout
        self._ssl_ctx = None
        if not verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

        # 构建 base URL
        if self.endpoint.startswith("http://") or self.endpoint.startswith("https://"):
            self.base_url = self.endpoint
        else:
            scheme = "https" if secure else "http"
            self.base_url = f"{scheme}://{self.endpoint}"

        # mc (MinIO Client CLI) 可用性
        self._mc_available = None

    # ── HTTP 工具 ──

    def _request(self, method: str, url: str, data: bytes = None,
                 headers: dict = None, timeout: int = None) -> dict:
        """发起 HTTP 请求，返回 {status, body}。"""
        hdrs = headers or {}
        req = Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = urlopen(req, timeout=timeout or self.timeout, context=self._ssl_ctx)
            body = resp.read().decode("utf-8")
            try:
                return {"status": resp.status, "body": json.loads(body)}
            except json.JSONDecodeError:
                return {"status": resp.status, "body": body}
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return {"status": e.code, "body": json.loads(body)}
            except json.JSONDecodeError:
                return {"status": e.code, "body": body}
        except URLError as e:
            return {"status": 0, "body": str(e.reason)}
        except Exception as e:
            return {"status": 0, "body": str(e)}

    def get(self, path: str, headers: dict = None, timeout: int = None) -> dict:
        url = self.base_url + path
        return self._request("GET", url, headers=headers, timeout=timeout)

    # ── 健康端点 ──

    def health_live(self) -> dict:
        return self.get("/minio/health/live")

    def health_ready(self) -> dict:
        """MinIO readiness (需要 cluster 写可用)。"""
        return self.get("/minio/health/cluster")

    def health_cluster(self) -> dict:
        """集群健康 (带 quorum 信息)。"""
        return self.get("/minio/health/cluster?verify")

    # ── Prometheus Metrics ──

    def metrics_cluster(self) -> dict:
        """获取 Prometheus metrics。"""
        return self.get("/minio/v2/metrics/cluster")

    # ── mc CLI 封装 ──

    def mc_available(self) -> bool:
        """检查 mc (MinIO Client) CLI 是否可用。"""
        if self._mc_available is not None:
            return self._mc_available
        try:
            result = subprocess.run(
                ["mc", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            self._mc_available = result.returncode == 0
        except Exception:
            self._mc_available = False
        return self._mc_available

    def mc_alias_set(self, alias: str = "_healthcheck") -> bool:
        """设置 mc alias 以便后续命令使用。"""
        if not self.mc_available() or not self.access_key:
            return False
        try:
            result = subprocess.run(
                ["mc", "alias", "set", alias, self.base_url,
                 self.access_key, self.secret_key],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def mc_command(self, args: list[str], timeout: int = 15) -> Optional[str]:
        """执行 mc 命令，返回 stdout 或 None。"""
        if not self.mc_available():
            return None
        try:
            result = subprocess.run(
                ["mc"] + args,
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception:
            return None

    def mc_admin_info(self, alias: str = "_healthcheck") -> Optional[dict]:
        """执行 mc admin info --json。"""
        output = self.mc_command(["admin", "info", "--json", alias], timeout=15)
        if output:
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                pass
        return None

    # ── S3 兼容操作 (使用 minio SDK 或 urllib) ──

    def _get_s3_client(self):
        """获取 minio SDK 客户端实例。"""
        Minio = _import_minio_sdk()
        if Minio is None:
            return None
        host = self.base_url.replace("http://", "").replace("https://", "")
        return Minio(
            host,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    def list_buckets_sdk(self) -> Optional[list]:
        """使用 minio SDK 列出 bucket。"""
        try:
            client = self._get_s3_client()
            if client is None:
                return None
            return [b.name for b in client.list_buckets()]
        except Exception:
            return None

    def s3_test_operations(self, bucket: str) -> dict:
        """使用 minio SDK 对指定 bucket 进行 CRUD 测试。"""
        results = {}
        try:
            from io import BytesIO

            client = self._get_s3_client()
            if client is None:
                results["error"] = "minio SDK 未安装"
                return results

            test_key = "_healthcheck_test_object"
            test_data = b"healthcheck-test"

            # PUT
            try:
                client.put_object(bucket, test_key, BytesIO(test_data), len(test_data))
                results["put"] = True
            except Exception as e:
                results["put"] = str(e)

            # GET
            try:
                resp = client.get_object(bucket, test_key)
                data = resp.read()
                resp.close()
                resp.release_conn()
                results["get"] = data == test_data
            except Exception as e:
                results["get"] = str(e)

            # LIST
            try:
                objs = list(client.list_objects(bucket, prefix="_healthcheck_"))
                results["list"] = True
            except Exception as e:
                results["list"] = str(e)

            # PRESIGNED
            try:
                import datetime
                url = client.presigned_get_object(bucket, test_key, expires=datetime.timedelta(minutes=5))
                results["presigned"] = bool(url)
            except Exception as e:
                results["presigned"] = str(e)

            # DELETE
            try:
                client.remove_object(bucket, test_key)
                results["delete"] = True
            except Exception as e:
                results["delete"] = str(e)

        except Exception as e:
            results["error"] = str(e)

        return results


def init_context(endpoint: str, access_key: str = None, secret_key: str = None,
                 secure: bool = False, verify_ssl: bool = True, timeout: int = 10,
                 deploy_mode: str = "auto",
                 kubeconfig: str = None, kube_context: str = None,
                 namespace: str = "default", label_selector: str = "app=minio",
                 docker_container: str = None, docker_image: str = "minio/minio",
                 required_buckets: list = None) -> dict:
    """初始化检查上下文。"""

    mc = MinioClient(endpoint, access_key, secret_key, secure, verify_ssl, timeout)

    # 自动检测部署模式
    if deploy_mode == "auto":
        mode = _detect_deploy_mode(kubeconfig, kube_context, namespace,
                                   label_selector, docker_container, docker_image)
    else:
        mode = DeployMode(deploy_mode)

    ctx = {
        "mc": mc,
        "mode": mode,
        "namespace": namespace,
        "label_selector": label_selector,
        "docker_image": docker_image,
        "required_buckets": required_buckets or [],
    }

    # 设置 mc alias (如果 mc CLI 可用)
    if access_key and secret_key:
        mc.mc_alias_set()

    # K8s 客户端 (可选)
    if mode == DeployMode.K8S:
        try:
            from kubernetes import client, config as k8s_config
            if kubeconfig:
                k8s_config.load_kube_config(config_file=kubeconfig, context=kube_context)
            else:
                try:
                    k8s_config.load_incluster_config()
                except k8s_config.ConfigException:
                    k8s_config.load_kube_config(context=kube_context)
            ctx["k8s_core"] = client.CoreV1Api()
            ctx["k8s_apps"] = client.AppsV1Api()
        except ImportError:
            pass

    # Docker 客户端 (可选)
    if mode == DeployMode.DOCKER:
        ctx["docker_container"] = docker_container
        try:
            import docker
            ctx["docker_client"] = docker.from_env()
        except (ImportError, Exception):
            ctx["docker_client"] = None

    # 检查连通性: 优先 health 端点，失败则尝试 S3 list-buckets
    resp = mc.health_live()
    if resp["status"] == 200:
        ctx["health_endpoints"] = True
    else:
        ctx["health_endpoints"] = False
        # 旧版 MinIO 可能没有 /minio/health/* 端点，尝试 S3 API
        if access_key and secret_key:
            buckets = mc.list_buckets_sdk()
            if buckets is None:
                # SDK 也失败，再试 HTTP 根路径
                root_resp = mc.get("/")
                if root_resp["status"] == 0:
                    ctx["connect_error"] = (
                        f"MinIO 不可达: {root_resp['body']}")
        else:
            # 无凭证，检查端口可达
            root_resp = mc.get("/")
            if root_resp["status"] == 0:
                ctx["connect_error"] = (
                    f"MinIO 不可达: {root_resp['body']}")

    return ctx


def _detect_deploy_mode(kubeconfig, kube_context, namespace, label_selector,
                        docker_container, docker_image) -> DeployMode:
    """自动检测部署模式: 优先 K8s → Docker → VM。"""
    # 尝试 K8s
    try:
        from kubernetes import client, config as k8s_config
        if kubeconfig:
            k8s_config.load_kube_config(config_file=kubeconfig, context=kube_context)
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config(context=kube_context)
        core = client.CoreV1Api()
        pods = core.list_namespaced_pod(namespace, label_selector=label_selector, limit=1)
        if pods.items:
            return DeployMode.K8S
    except Exception:
        pass

    # 尝试 Docker
    if docker_container:
        return DeployMode.DOCKER
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={docker_image}",
             "--filter", "status=running", "-q"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return DeployMode.DOCKER
    except Exception:
        pass

    return DeployMode.VM
