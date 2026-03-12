"""APISIX 客户端初始化与部署模式检测。

支持三种部署模式:
  - k8s:    通过 kubernetes API 获取 Pod/容器状态
  - docker: 通过 docker SDK/CLI 获取容器状态
  - vm:     仅通过 HTTP 接口检查

本模块仅关注 APISIX 网关与 APISIX Dashboard 自身。
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


class ApisixClient:
    """APISIX Admin API 客户端。"""

    def __init__(self, admin_url: str, admin_key: str = None,
                 verify_ssl: bool = True, timeout: int = 15):
        self.admin_url = admin_url.rstrip("/")
        self.admin_key = admin_key
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
        if self.admin_key:
            hdrs.setdefault("X-API-KEY", self.admin_key)
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
        url = self.admin_url + path
        return self._request("GET", url, headers=headers, timeout=timeout)

    def admin(self, path: str, timeout: int = None) -> dict:
        """调用 APISIX Admin API。"""
        url = self.admin_url + "/apisix/admin" + path
        return self._request("GET", url, timeout=timeout)

    def routes(self, timeout: int = None) -> dict:
        return self.admin("/routes", timeout=timeout)

    def upstreams(self, timeout: int = None) -> dict:
        return self.admin("/upstreams", timeout=timeout)

    def services(self, timeout: int = None) -> dict:
        return self.admin("/services", timeout=timeout)

    def consumers(self, timeout: int = None) -> dict:
        return self.admin("/consumers", timeout=timeout)

    def ssls(self, timeout: int = None) -> dict:
        return self.admin("/ssls", timeout=timeout)

    def plugins_list(self, timeout: int = None) -> dict:
        return self.admin("/plugins/list", timeout=timeout)

    def plugin_metadata(self, plugin_name: str, timeout: int = None) -> dict:
        return self.admin(f"/plugin_metadata/{plugin_name}", timeout=timeout)


class DashboardClient:
    """APISIX Dashboard HTTP 客户端。"""

    def __init__(self, base_url: str, username: str = None,
                 password: str = None, verify_ssl: bool = True,
                 timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.token = None
        self._ssl_ctx = None
        if not verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, url: str, data: bytes = None,
                 headers: dict = None, timeout: int = None) -> dict:
        hdrs = headers or {}
        if self.token:
            hdrs.setdefault("Authorization", self.token)
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

    def login(self) -> dict:
        """登录 Dashboard 获取 Token。"""
        if not self.username or not self.password:
            return {"status": 0, "body": "未提供用户名/密码", "headers": {}}
        url = self.base_url + "/apisix/admin/user/login"
        payload = json.dumps({"username": self.username, "password": self.password}).encode()
        return self._request("POST", url, data=payload,
                             headers={"Content-Type": "application/json"})

    def version(self, timeout: int = None) -> dict:
        return self.get("/apisix/admin/tool/version", timeout=timeout)


def init_context(admin_url: str, admin_key: str = None,
                 dashboard_url: str = None,
                 dashboard_user: str = None, dashboard_pass: str = None,
                 gateway_url: str = None,
                 verify_ssl: bool = True, timeout: int = 15,
                 deploy_mode: str = "auto",
                 kubeconfig: str = None, kube_context: str = None,
                 namespace: str = "apisix",
                 label_selector: str = "app.kubernetes.io/name=apisix",
                 dashboard_label_selector: str = "app.kubernetes.io/name=apisix-dashboard",
                 docker_container: str = None,
                 docker_image: str = "apache/apisix",
                 dashboard_docker_container: str = None,
                 dashboard_docker_image: str = "apache/apisix-dashboard") -> dict:
    """初始化检查上下文。"""

    apisix = ApisixClient(admin_url, admin_key, verify_ssl, timeout)

    # Dashboard 客户端 (可选)
    dashboard = None
    if dashboard_url:
        dashboard = DashboardClient(dashboard_url, dashboard_user,
                                    dashboard_pass, verify_ssl, timeout)

    # 自动检测部署模式
    if deploy_mode == "auto":
        mode = _detect_deploy_mode(kubeconfig, kube_context, namespace,
                                   label_selector, docker_container, docker_image)
    else:
        mode = DeployMode(deploy_mode)

    ctx = {
        "apisix": apisix,
        "dashboard": dashboard,
        "gateway_url": gateway_url,
        "mode": mode,
        "namespace": namespace,
        "label_selector": label_selector,
        "dashboard_label_selector": dashboard_label_selector,
        "docker_image": docker_image,
        "dashboard_docker_image": dashboard_docker_image,
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
        ctx["dashboard_docker_container"] = dashboard_docker_container
        try:
            import docker
            ctx["docker_client"] = docker.from_env()
        except (ImportError, Exception):
            ctx["docker_client"] = None

    # 检查 Admin API 连通性
    resp = apisix.admin("/routes")
    if resp["status"] == 0:
        ctx["connect_error"] = f"APISIX Admin API 不可达: {resp['body']}"
    elif resp["status"] == 401:
        ctx["connect_error"] = "APISIX Admin API 认证失败 (API Key 无效)"
    elif resp["status"] >= 500:
        ctx["connect_error"] = f"APISIX Admin API 异常 (status={resp['status']})"

    # 检查 Dashboard 连通性
    if dashboard:
        resp = dashboard.version()
        if resp["status"] == 0:
            ctx["dashboard_connect_error"] = f"Dashboard 不可达: {resp['body']}"
        elif resp["status"] >= 500:
            ctx["dashboard_connect_error"] = f"Dashboard 异常 (status={resp['status']})"
        else:
            body = resp.get("body", {})
            if isinstance(body, dict) and body.get("code") == 0:
                ctx["dashboard_version"] = body.get("data", {})

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
