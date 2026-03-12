"""Jenkins 客户端初始化与部署模式检测。

支持三种部署模式:
  - k8s:    通过 kubernetes API 获取 Pod/容器状态
  - docker: 通过 docker SDK/CLI 获取容器状态
  - vm:     仅通过 HTTP 接口检查
"""

import base64
import http.cookiejar
import json
import ssl
import subprocess
from enum import Enum
from typing import Optional
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from urllib.error import URLError, HTTPError


class DeployMode(Enum):
    K8S = "k8s"
    DOCKER = "docker"
    VM = "vm"


class JenkinsClient:
    """Jenkins HTTP 客户端，封装 JSON API 调用。"""

    def __init__(self, base_url: str, user: str = None, password: str = None,
                 verify_ssl: bool = True, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.timeout = timeout
        self._ssl_ctx = None
        if not verify_ssl:
            self._ssl_ctx = ssl.create_default_context()
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

        # 构建 Basic Auth header
        self._auth_header = None
        if user and password:
            cred = base64.b64encode(f"{user}:{password}".encode()).decode()
            self._auth_header = f"Basic {cred}"

        # crumb (CSRF)
        self._crumb = None

        # Cookie jar — Jenkins CRUMB 绑定 session
        self._cookie_jar = http.cookiejar.CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))

    def _request(self, method: str, url: str, data: bytes = None,
                 headers: dict = None, timeout: int = None) -> dict:
        """发起 HTTP 请求，返回 {status, body}。"""
        hdrs = headers or {}
        if self._auth_header:
            hdrs.setdefault("Authorization", self._auth_header)
        req = Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = self._opener.open(req, timeout=timeout or self.timeout)
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

    def api_json(self, path: str = "", tree: str = None, depth: int = None,
                 timeout: int = None) -> dict:
        """调用 Jenkins JSON API。"""
        url = self.base_url + path + "/api/json"
        params = []
        if tree:
            params.append(f"tree={tree}")
        if depth is not None:
            params.append(f"depth={depth}")
        if params:
            url += "?" + "&".join(params)
        return self._request("GET", url, timeout=timeout)

    def get_crumb(self) -> Optional[dict]:
        """获取 Jenkins CSRF crumb。"""
        resp = self.get("/crumbIssuer/api/json")
        if resp["status"] == 200 and isinstance(resp["body"], dict):
            self._crumb = resp["body"]
            return self._crumb
        return None

    def script_console(self, script: str, timeout: int = None) -> Optional[str]:
        """通过 Script Console 执行 Groovy 脚本。"""
        if not self._auth_header:
            return None
        if self._crumb is None:
            self.get_crumb()

        url = self.base_url + "/scriptText"
        from urllib.parse import urlencode
        data = urlencode({"script": script}).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self._crumb:
            headers[self._crumb.get("crumbRequestField", "Jenkins-Crumb")] = \
                self._crumb.get("crumb", "")

        resp = self._request("POST", url, data=data, headers=headers,
                             timeout=timeout or self.timeout)
        if resp["status"] == 200:
            return resp["body"] if isinstance(resp["body"], str) else json.dumps(resp["body"])
        return None


def init_context(base_url: str, user: str = None, password: str = None,
                 verify_ssl: bool = True, timeout: int = 15,
                 deploy_mode: str = "auto",
                 kubeconfig: str = None, kube_context: str = None,
                 namespace: str = "default",
                 label_selector: str = "app.kubernetes.io/name=jenkins",
                 docker_container: str = None, docker_image: str = "jenkins/jenkins") -> dict:
    """初始化检查上下文。"""

    jk = JenkinsClient(base_url, user, password, verify_ssl, timeout)

    # 自动检测部署模式
    if deploy_mode == "auto":
        mode = _detect_deploy_mode(kubeconfig, kube_context, namespace,
                                   label_selector, docker_container, docker_image)
    else:
        mode = DeployMode(deploy_mode)

    ctx = {
        "jk": jk,
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
    resp = jk.get("/login")
    if resp["status"] == 0:
        ctx["connect_error"] = f"Jenkins 不可达: {resp['body']}"
    elif resp["status"] >= 500:
        ctx["connect_error"] = f"Jenkins 服务异常 (status={resp['status']})"

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
