"""PostgreSQL 客户端初始化与部署模式检测。

支持三种部署模式:
  - k8s:    通过 kubernetes API 获取 Pod/容器状态，通过 port-forward 或直连访问 PG
  - docker: 通过 docker SDK/CLI 获取容器状态，直连 PG
  - vm:     直连 PG (本地或远程)
"""

import subprocess
from enum import Enum
from typing import Optional


class DeployMode(Enum):
    K8S = "k8s"
    DOCKER = "docker"
    VM = "vm"


class PgClient:
    """PostgreSQL 客户端，封装 psycopg2 连接与常用查询。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 5432,
                 user: str = "postgres", password: str = None,
                 dbname: str = "postgres", connect_timeout: int = 10):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.dbname = dbname
        self.connect_timeout = connect_timeout
        self._conn = None

    def connect(self):
        """建立数据库连接。"""
        import psycopg2
        self._conn = psycopg2.connect(
            host=self.host, port=self.port,
            user=self.user, password=self.password,
            dbname=self.dbname,
            connect_timeout=self.connect_timeout,
        )
        self._conn.autocommit = True

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def conn(self):
        return self._conn

    def query(self, sql: str, params=None) -> list[dict]:
        """执行查询，返回字典列表。"""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_one(self, sql: str, params=None) -> Optional[dict]:
        """执行查询，返回单行字典或 None。"""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def query_scalar(self, sql: str, params=None):
        """执行查询，返回单个标量值。"""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

    def server_version(self) -> str:
        """返回 PG 版本字符串。"""
        return self.query_scalar("SHOW server_version") or "unknown"

    def is_in_recovery(self) -> bool:
        """判断是否为从库 (standby)。"""
        return self.query_scalar("SELECT pg_is_in_recovery()")

    def safe_query(self, sql: str, params=None, default=None):
        """安全执行查询，出错返回默认值。"""
        try:
            return self.query(sql, params)
        except Exception:
            return default


def init_context(host: str = "127.0.0.1", port: int = 5432,
                 user: str = "postgres", password: str = None,
                 dbname: str = "postgres", connect_timeout: int = 10,
                 deploy_mode: str = "auto",
                 kubeconfig: str = None, kube_context: str = None,
                 namespace: str = "default",
                 label_selector: str = "app=postgresql",
                 docker_container: str = None,
                 docker_image: str = "postgres") -> dict:
    """初始化检查上下文。"""

    pg = PgClient(host, port, user, password, dbname, connect_timeout)

    # 自动检测部署模式
    if deploy_mode == "auto":
        mode = _detect_deploy_mode(kubeconfig, kube_context, namespace,
                                   label_selector, docker_container, docker_image)
    else:
        mode = DeployMode(deploy_mode)

    ctx = {
        "pg": pg,
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

    # 尝试连接数据库
    try:
        pg.connect()
    except Exception as e:
        ctx["connect_error"] = str(e)

    return ctx


def _detect_deploy_mode(kubeconfig, kube_context, namespace, label_selector,
                        docker_container, docker_image) -> DeployMode:
    """自动检测部署模式: 优先 K8s -> Docker -> VM。"""
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
