#!/usr/bin/env python3
"""
APISIX 专项健康检查 —— 一键检查脚本

用法:
    python -m apisix.main --admin-url http://localhost:9180                        # 最简用法
    python -m apisix.main --admin-url http://localhost:9180 --admin-key edd1c9f034335f136f87ad84b625c8f1
    python -m apisix.main --admin-url http://localhost:9180 \\
        --dashboard-url http://localhost:9000                                       # 带 Dashboard
    python -m apisix.main --admin-url http://localhost:9180 --mode k8s \\
        --namespace apisix                                                          # K8s 模式
    python -m apisix.main --admin-url http://localhost:9180 --mode docker \\
        --docker-container apisix                                                   # Docker 模式
    python -m apisix.main --admin-url http://localhost:9180 --mode vm              # VM 模式
    python -m apisix.main --admin-url http://localhost:9180 --check core,route,plugin
    python -m apisix.main --admin-url http://localhost:9180 --verbose

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
    core_component,
    control_data_plane,
    route_upstream,
    traffic_error,
    plugin_check,
    tls_cert,
    dashboard_check,
    risk_warning,
)

_ALL_CHECKS = [
    ("core",      core_component,    "1. 核心组件状态"),
    ("plane",     control_data_plane, "2. 控制面与数据面状态"),
    ("route",     route_upstream,    "3. Route / Upstream / Service / Consumer"),
    ("traffic",   traffic_error,     "4. 流量与错误检查"),
    ("plugin",    plugin_check,      "5. 插件专项检查"),
    ("tls",       tls_cert,          "6. 证书与 TLS 检查"),
    ("dashboard", dashboard_check,   "7. Dashboard 检查"),
    ("risk",      risk_warning,      "8. 风险预警"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="APISIX 专项健康检查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # APISIX 连接
    parser.add_argument("--admin-url", default="http://localhost:9180",
                        help="APISIX Admin API URL (默认: http://localhost:9180)")
    parser.add_argument("--admin-key", "-k",
                        help="APISIX Admin API Key")
    parser.add_argument("--gateway-url",
                        help="APISIX Gateway URL (如: http://localhost:9080)")

    # Dashboard 连接
    parser.add_argument("--dashboard-url",
                        help="APISIX Dashboard URL (如: http://localhost:9000)")
    parser.add_argument("--dashboard-user",
                        help="Dashboard 用户名")
    parser.add_argument("--dashboard-pass",
                        help="Dashboard 密码")

    # SSL
    parser.add_argument("--no-verify-ssl", action="store_true",
                        help="跳过 SSL 证书验证")
    parser.add_argument("--timeout", type=int, default=15,
                        help="HTTP 请求超时时间(秒) (默认: 15)")

    # 部署模式
    parser.add_argument("--mode", choices=["auto", "k8s", "docker", "vm"], default="auto",
                        help="部署模式 (默认: auto)")

    # K8s 参数
    parser.add_argument("--kubeconfig", help="kubeconfig 文件路径")
    parser.add_argument("--kube-context", help="kubeconfig context 名称")
    parser.add_argument("--namespace", "-n", default="apisix",
                        help="APISIX 所在的 K8s namespace (默认: apisix)")
    parser.add_argument("--label-selector", "-l",
                        default="app.kubernetes.io/name=apisix",
                        help="APISIX K8s label selector (默认: app.kubernetes.io/name=apisix)")
    parser.add_argument("--dashboard-label-selector",
                        default="app.kubernetes.io/name=apisix-dashboard",
                        help="Dashboard K8s label selector")

    # Docker 参数
    parser.add_argument("--docker-container", help="APISIX Docker 容器名称或 ID")
    parser.add_argument("--docker-image", default="apache/apisix",
                        help="APISIX Docker 镜像名称 (默认: apache/apisix)")
    parser.add_argument("--dashboard-docker-container",
                        help="Dashboard Docker 容器名称或 ID")
    parser.add_argument("--dashboard-docker-image",
                        default="apache/apisix-dashboard",
                        help="Dashboard Docker 镜像名称")

    # 检查控制
    parser.add_argument("--check", "-c",
                        help="只运行指定模块 (逗号分隔), 可选: " +
                             ", ".join(n for n, _, _ in _ALL_CHECKS))
    parser.add_argument("--verbose", "-v", action="store_true", help="显示所有详细信息")

    return parser.parse_args()


def main():
    args = parse_args()

    print("\033[1m")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║           APISIX 专项健康检查                              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\033[0m")

    # 初始化上下文
    try:
        print(f"⏳ 连接 APISIX ({args.admin_url}) ...")
        ctx = init_context(
            admin_url=args.admin_url,
            admin_key=args.admin_key,
            dashboard_url=args.dashboard_url,
            dashboard_user=args.dashboard_user,
            dashboard_pass=args.dashboard_pass,
            gateway_url=args.gateway_url,
            verify_ssl=not args.no_verify_ssl,
            timeout=args.timeout,
            deploy_mode=args.mode,
            kubeconfig=args.kubeconfig,
            kube_context=args.kube_context,
            namespace=args.namespace,
            label_selector=args.label_selector,
            dashboard_label_selector=args.dashboard_label_selector,
            docker_container=args.docker_container,
            docker_image=args.docker_image,
            dashboard_docker_container=args.dashboard_docker_container,
            dashboard_docker_image=args.dashboard_docker_image,
        )
        if "connect_error" in ctx:
            print(f"\033[33m⚠️  APISIX 连接异常: {ctx['connect_error']}\033[0m")
            print("   部分检查将不可用，继续执行基础设施层检查...\n")
        else:
            print(f"✅ 连接成功, 部署模式: {ctx['mode'].value}\n")
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

    # 执行检查
    results: list[CheckGroup] = []
    total = len(checks_to_run)

    for i, (name, module, desc) in enumerate(checks_to_run, 1):
        print(f"⏳ [{i}/{total}] {desc} ...", end="", flush=True)
        start = time.time()
        try:
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
