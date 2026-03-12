"""GitLab 客户端初始化与部署模式检测。

支持三种部署模式:
  - k8s:    通过 kubernetes API 获取 Pod/容器状态
  - docker: 通过 docker SDK/CLI 获取容器状态
  - vm:     仅通过 HTTP 接口检查
"""

import json
import ssl
import subprocess
from enum import Enum
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class DeployMode(Enum):
    K8S = "k8s"
    DOCKER = "docker"
    VM = "vm"


class GitLabClient:
    """GitLab HTTP 客户端，封装 API 调用。"""

    def __init__(self, base_url: str, token: str = None,
                 verify_ssl: bool = True, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._ssl_ctx = None
        if not verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, url: str, data: bytes = None,
                 headers: dict = None, timeout: int = None) -> dict:
        """发起 HTTP 请求，返回 {status, body, headers}。"""
        hdrs = headers or {}
        if self.token:
            hdrs.setdefault("PRIVATE-TOKEN", self.token)
        req = Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = urlopen(req, timeout=timeout or self.timeout,
                           context=self._ssl_ctx)
            body = resp.read().decode("utf-8")
            resp_headers = dict(resp.headers)
            try:
                return {"status": resp.status, "body": json.loads(body),
                        "headers": resp_headers}
            except json.JSONDecodeError:
                return {"status": resp.status, "body": body,
                        "headers": resp_headers}
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return {"status": e.code, "body": json.loads(body), "headers": {}}
            except json.JSONDecodeError:
                return {"status": e.code, "body": body, "headers": {}}
        except URLError as e:
            return {"status": 0, "body": str(e.reason), "headers": {}}
        except Exception as e:
            return {"status": 0, "body": str(e), "headers": {}}

    def get(self, path: str, headers: dict = None, timeout: int = None) -> dict:
        url = self.base_url + path
        return self._request("GET", url, headers=headers, timeout=timeout)

    def api_v4(self, path: str, params: dict = None,
               timeout: int = None) -> dict:
        """调用 GitLab API v4。"""
        url = self.base_url + "/api/v4" + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        return self._request("GET", url, timeout=timeout)

    def health(self, timeout: int = None) -> dict:
        """/-/health 端点。"""
        return self.get("/-/health", timeout=timeout)

    def readiness(self, timeout: int = None) -> dict:
        """/-/readiness 端点。"""
        return self.get("/-/readiness", timeout=timeout)

    def liveness(self, timeout: int = None) -> dict:
        """/-/liveness 端点。"""
        return self.get("/-/liveness", timeout=timeout)


def init_context(base_url: str, token: str = None,
                 verify_ssl: bool = True, timeout: int = 15,
                 deploy_mode: str = "auto",
                 kubeconfig: str = None, kube_context: str = None,
                 namespace: str = "default",
                 label_selector: str = "app.kubernetes.io/name=gitlab",
                 docker_container: str = None,
                 docker_image: str = "gitlab/gitlab-ce") -> dict:
    """初始化检查上下文。"""

    gl = GitLabClient(base_url, token, verify_ssl, timeout)

    # 自动检测部署模式
    if deploy_mode == "auto":
        mode = _detect_deploy_mode(kubeconfig, kube_context, namespace,
                                   label_selector, docker_container, docker_image)
    else:
        mode = DeployMode(deploy_mode)

    ctx = {
        "gl": gl,
        "mode": mode,
        "namespace": namespace,
        "label_selector": label_selector,
        "docker_image": docker_image,
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

    # 检查连通性
    resp = gl.health()
    if resp["status"] == 0:
        ctx["connect_error"] = f"GitLab 不可达: {resp['body']}"
    elif resp["status"] >= 500:
        ctx["connect_error"] = f"GitLab 服务异常 (status={resp['status']})"
    elif resp["status"] == 200:
        body = resp["body"]
        if isinstance(body, str) and "GitLab OK" in body:
            pass  # 正常
        elif isinstance(body, dict):
            pass  # JSON 格式也算正常
        else:
            # 可能是其他页面
            pass
    # 401/403 表示能连通但需要认证，不算 connect_error

    return ctx


def _detect_deploy_mode(kubeconfig, kube_context, namespace, label_selector,
                        docker_container, docker_image) -> DeployMode:
    """自动检测部署模式: 优先 K8s → Docker → VM。"""
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
