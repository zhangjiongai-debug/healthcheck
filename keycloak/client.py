"""Keycloak 客户端初始化与部署模式检测。

支持三种部署模式:
  - k8s:    通过 kubernetes API 获取 Pod/容器状态
  - docker: 通过 docker SDK 获取容器状态
  - vm:     仅通过 HTTP 接口检查 (systemd/进程级检查通过 SSH 或本地命令)
"""

import json
import ssl
import subprocess
from enum import Enum
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urljoin


class DeployMode(Enum):
    K8S = "k8s"
    DOCKER = "docker"
    VM = "vm"


class KeycloakClient:
    """Keycloak HTTP 客户端，封装 Admin REST API 和健康端点调用。"""

    def __init__(self, base_url: str, admin_user: str = None, admin_password: str = None,
                 verify_ssl: bool = True, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._token: Optional[str] = None
        self._ssl_ctx = None
        if not verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, url: str, data: bytes = None,
                 headers: dict = None, timeout: int = None) -> dict:
        """发起 HTTP 请求，返回 (status_code, body_dict_or_text)。"""
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
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        return self._request("GET", url, headers=headers, timeout=timeout)

    def post(self, path: str, data: dict = None, headers: dict = None,
             content_type: str = "application/json", timeout: int = None) -> dict:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        hdrs = headers or {}
        if data is not None:
            if content_type == "application/x-www-form-urlencoded":
                from urllib.parse import urlencode
                body = urlencode(data).encode("utf-8")
            else:
                body = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = content_type
        else:
            body = None
        return self._request("POST", url, data=body, headers=hdrs, timeout=timeout)

    def get_admin_token(self, realm: str = "master") -> Optional[str]:
        """通过 password grant 获取 admin token。"""
        if not self.admin_user or not self.admin_password:
            return None
        resp = self.post(
            f"/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin_user,
                "password": self.admin_password,
            },
            content_type="application/x-www-form-urlencoded",
        )
        if resp["status"] == 200 and isinstance(resp["body"], dict):
            self._token = resp["body"].get("access_token")
            return self._token
        return None

    def admin_get(self, path: str, timeout: int = None) -> dict:
        """带 admin token 的 GET 请求。"""
        if not self._token:
            self.get_admin_token()
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return self.get(path, headers=headers, timeout=timeout)

    def health(self) -> dict:
        """调用 Keycloak 健康检查端点 (Quarkus /health)。"""
        return self.get("/health")

    def health_ready(self) -> dict:
        return self.get("/health/ready")

    def health_live(self) -> dict:
        return self.get("/health/live")

    def metrics(self) -> dict:
        """获取 Prometheus metrics 端点。"""
        return self.get("/metrics")


def init_context(base_url: str, deploy_mode: str = "auto",
                 admin_user: str = None, admin_password: str = None,
                 verify_ssl: bool = True, timeout: int = 10,
                 kubeconfig: str = None, kube_context: str = None,
                 namespace: str = "default", label_selector: str = "app=keycloak",
                 docker_container: str = None) -> dict:
    """初始化检查上下文，包含 Keycloak HTTP 客户端和可选的基础设施客户端。"""

    kc = KeycloakClient(base_url, admin_user, admin_password, verify_ssl, timeout)

    # 自动检测部署模式
    if deploy_mode == "auto":
        mode = _detect_deploy_mode(kubeconfig, kube_context, namespace, label_selector,
                                   docker_container)
    else:
        mode = DeployMode(deploy_mode)

    ctx = {
        "kc": kc,
        "mode": mode,
        "base_url": base_url,
        "namespace": namespace,
        "label_selector": label_selector,
        "verify_ssl": verify_ssl,
    }

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

    return ctx


def _detect_deploy_mode(kubeconfig, kube_context, namespace, label_selector,
                        docker_container) -> DeployMode:
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
            ["docker", "ps", "--filter", "ancestor=quay.io/keycloak/keycloak",
             "--filter", "status=running", "-q"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return DeployMode.DOCKER
    except Exception:
        pass

    return DeployMode.VM
