#!/usr/bin/env python3
"""
Keycloak 专项健康检查 —— 一键检查脚本

用法:
    python -m keycloak.main --url http://localhost:8080                      # 最简用法
    python -m keycloak.main --url https://keycloak.example.com \\
        --admin-user admin --admin-password secret                           # 带 admin 凭证
    python -m keycloak.main --url http://localhost:8080 --mode k8s \\
        --namespace keycloak --label-selector app=keycloak                   # K8s 模式
    python -m keycloak.main --url http://localhost:8080 --mode docker \\
        --docker-container my-keycloak                                       # Docker 模式
    python -m keycloak.main --url http://localhost:8080 --mode vm            # VM 模式
    python -m keycloak.main --url http://localhost:8080 --check instance,auth
    python -m keycloak.main --url http://localhost:8080 --verbose

部署模式:
    auto   - 自动检测 (默认): 按 K8s → Docker → VM 顺序尝试
    k8s    - Kubernetes 部署
    docker - Docker 容器部署
    vm     - 虚拟机 / 裸机部署
"""

import argparse
import sys
import time

from .client import init_context
from .result import CheckGroup, print_report
from .checks import (
    instance,
    database,
    realm_config,
    auth,
    cluster,
    security,
    performance,
)

_ALL_CHECKS = [
    ("instance",    instance,     "4.1 实例状态"),
    ("database",    database,     "4.2 数据库连接"),
    ("realm",       realm_config, "4.3 Realm/Client/Federation"),
    ("auth",        auth,         "4.4 认证能力"),
    ("cluster",     cluster,      "4.5 集群/缓存"),
    ("security",    security,     "4.6 证书与安全"),
    ("performance", performance,  "4.7 性能与风险"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Keycloak 专项健康检查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Keycloak 连接
    parser.add_argument("--url", required=True, help="Keycloak 基础 URL (如 http://localhost:8080)")
    parser.add_argument("--admin-user", help="Keycloak 管理员用户名")
    parser.add_argument("--admin-password", help="Keycloak 管理员密码")
    parser.add_argument("--no-verify-ssl", action="store_true", help="跳过 SSL 证书验证")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP 请求超时时间(秒)")

    # 部署模式
    parser.add_argument("--mode", choices=["auto", "k8s", "docker", "vm"], default="auto",
                        help="部署模式 (默认: auto)")

    # K8s 参数
    parser.add_argument("--kubeconfig", help="kubeconfig 文件路径")
    parser.add_argument("--kube-context", help="kubeconfig context 名称")
    parser.add_argument("--namespace", "-n", default="default",
                        help="Keycloak 所在的 K8s namespace (默认: default)")
    parser.add_argument("--label-selector", "-l", default="app=keycloak",
                        help="K8s label selector (默认: app=keycloak)")

    # Docker 参数
    parser.add_argument("--docker-container", help="Docker 容器名称或 ID")

    # 检查控制
    parser.add_argument("--check", "-c",
                        help="只运行指定模块 (逗号分隔), 可选: " +
                             ", ".join(n for n, _, _ in _ALL_CHECKS))
    parser.add_argument("--verbose", "-v", action="store_true", help="显示所有详细信息")

    # Realm/Client 自定义检查
    parser.add_argument("--required-realms", help="必须存在的 realm (逗号分隔, 默认: master)")
    parser.add_argument("--required-clients",
                        help="必须存在的 client (格式: realm:client1,client2;realm2:client3)")

    return parser.parse_args()


def _parse_required_clients(s: str) -> dict:
    """解析 --required-clients 参数。"""
    result = {}
    for part in s.split(";"):
        part = part.strip()
        if ":" not in part:
            continue
        realm, clients_str = part.split(":", 1)
        result[realm.strip()] = [c.strip() for c in clients_str.split(",")]
    return result


def main():
    args = parse_args()

    print("\033[1m")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          Keycloak 专项健康检查                           ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\033[0m")

    # 初始化上下文
    try:
        print(f"⏳ 连接 Keycloak ({args.url}) ...")
        ctx = init_context(
            base_url=args.url,
            deploy_mode=args.mode,
            admin_user=args.admin_user,
            admin_password=args.admin_password,
            verify_ssl=not args.no_verify_ssl,
            timeout=args.timeout,
            kubeconfig=args.kubeconfig,
            kube_context=args.kube_context,
            namespace=args.namespace,
            label_selector=args.label_selector,
            docker_container=args.docker_container,
        )
        print(f"✅ 部署模式: {ctx['mode'].value}\n")
    except Exception as e:
        print(f"\033[31m❌ 初始化失败: {e}\033[0m")
        sys.exit(1)

    # 确定运行哪些检查
    if args.check:
        selected = set(args.check.split(","))
        checks_to_run = [(n, m, d) for n, m, d in _ALL_CHECKS if n in selected]
        unknown = selected - {n for n, _, _ in _ALL_CHECKS}
        if unknown:
            print(f"\033[33m⚠️  未知模块: {', '.join(unknown)}\033[0m\n")
    else:
        checks_to_run = _ALL_CHECKS

    # 解析自定义参数
    required_realms = args.required_realms.split(",") if args.required_realms else None
    required_clients = _parse_required_clients(args.required_clients) if args.required_clients else None

    # 执行检查
    results: list[CheckGroup] = []
    total = len(checks_to_run)

    for i, (name, module, desc) in enumerate(checks_to_run, 1):
        print(f"⏳ [{i}/{total}] {desc} ...", end="", flush=True)
        start = time.time()
        try:
            if name == "realm":
                group = module.check(ctx, required_realms=required_realms,
                                     required_clients=required_clients)
            elif name == "auth":
                test_realm = (required_realms[0] if required_realms else "master")
                group = module.check(ctx, test_realm=test_realm)
            else:
                group = module.check(ctx)
        except Exception as e:
            group = CheckGroup(f"{desc} (执行异常)")
            group.fatal(name, f"模块执行异常: {e}")
        elapsed = time.time() - start
        print(f" 完成 ({elapsed:.1f}s)")
        results.append(group)

    # 输出报告
    print_report(results, verbose=args.verbose)


if __name__ == "__main__":
    main()
