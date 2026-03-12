#!/usr/bin/env python3
"""
Jenkins 专项健康检查 —— 一键检查脚本

用法:
    python -m jenkins.main --url http://localhost:8080                                # 最简用法
    python -m jenkins.main --url http://localhost:8080 --user admin --password secret # 带凭证
    python -m jenkins.main --url http://localhost:8080 --mode k8s \\
        --namespace jenkins --label-selector app.kubernetes.io/name=jenkins           # K8s 模式
    python -m jenkins.main --url http://localhost:8080 --mode docker \\
        --docker-container my-jenkins                                                  # Docker 模式
    python -m jenkins.main --url http://localhost:8080 --mode vm                      # VM 模式
    python -m jenkins.main --url http://localhost:8080 --check controller,plugin,agent
    python -m jenkins.main --url http://localhost:8080 --verbose

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
    controller,
    init_config,
    plugin,
    agent,
    job,
    dependency,
    performance,
)

_ALL_CHECKS = [
    ("controller",  controller,  "1. 控制器状态"),
    ("init",        init_config, "2. 初始化与配置状态"),
    ("plugin",      plugin,      "3. 插件健康检查"),
    ("agent",       agent,       "4. Agent / Executor 状态"),
    ("job",         job,         "5. Job / Pipeline 检查"),
    ("dependency",  dependency,  "6. 依赖检查"),
    ("performance", performance, "7. 性能与风险预警"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Jenkins 专项健康检查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Jenkins 连接
    parser.add_argument("--url", default="http://localhost:8080",
                        help="Jenkins 基础 URL (默认: http://localhost:8080)")
    parser.add_argument("--user", "-u", help="Jenkins 用户名")
    parser.add_argument("--password", "-p", help="Jenkins 密码或 API Token")
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
    parser.add_argument("--namespace", "-n", default="default",
                        help="Jenkins 所在的 K8s namespace (默认: default)")
    parser.add_argument("--label-selector", "-l",
                        default="app.kubernetes.io/name=jenkins",
                        help="K8s label selector (默认: app.kubernetes.io/name=jenkins)")

    # Docker 参数
    parser.add_argument("--docker-container", help="Docker 容器名称或 ID")
    parser.add_argument("--docker-image", default="jenkins/jenkins",
                        help="Docker 镜像名称 (默认: jenkins/jenkins)")

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
    print("║           Jenkins 专项健康检查                            ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\033[0m")

    # 初始化上下文
    try:
        print(f"⏳ 连接 Jenkins ({args.url}) ...")
        ctx = init_context(
            base_url=args.url,
            user=args.user,
            password=args.password,
            verify_ssl=not args.no_verify_ssl,
            timeout=args.timeout,
            deploy_mode=args.mode,
            kubeconfig=args.kubeconfig,
            kube_context=args.kube_context,
            namespace=args.namespace,
            label_selector=args.label_selector,
            docker_container=args.docker_container,
            docker_image=args.docker_image,
        )
        if "connect_error" in ctx:
            print(f"\033[33m⚠️  Jenkins 连接异常: {ctx['connect_error']}\033[0m")
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
