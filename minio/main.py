#!/usr/bin/env python3
"""
MinIO 专项健康检查 —— 一键检查脚本

用法:
    python -m minio.main --endpoint localhost:9000                          # 最简用法
    python -m minio.main --endpoint minio.example.com:9000 \\
        --access-key minioadmin --secret-key minioadmin                     # 带凭证
    python -m minio.main --endpoint localhost:9000 --mode k8s \\
        --namespace minio --label-selector app=minio                       # K8s 模式
    python -m minio.main --endpoint localhost:9000 --mode docker \\
        --docker-container my-minio                                         # Docker 模式
    python -m minio.main --endpoint localhost:9000 --mode vm               # VM 模式
    python -m minio.main --endpoint localhost:9000 --check instance,bucket
    python -m minio.main --endpoint localhost:9000 --verbose
    python -m minio.main --endpoint localhost:9000 \\
        --required-buckets uploads,backups,logs                             # 检查必需 bucket

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
    storage,
    bucket,
    admin_auth,
    data_protect,
    performance,
)

_ALL_CHECKS = [
    ("instance",    instance,     "5.1 实例与集群状态"),
    ("storage",     storage,      "5.2 存储层状态"),
    ("bucket",      bucket,       "5.3 Bucket 与对象服务能力"),
    ("admin",       admin_auth,   "5.4 管理与认证"),
    ("data",        data_protect, "5.5 数据保护与后台任务"),
    ("performance", performance,  "5.6 性能与告警"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="MinIO 专项健康检查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # MinIO 连接
    parser.add_argument("--endpoint", "-e", default="localhost:9000",
                        help="MinIO 端点地址 (默认: localhost:9000)")
    parser.add_argument("--access-key", "-ak",
                        help="MinIO Access Key")
    parser.add_argument("--secret-key", "-sk",
                        help="MinIO Secret Key")
    parser.add_argument("--secure", action="store_true",
                        help="使用 HTTPS 连接")
    parser.add_argument("--no-verify-ssl", action="store_true",
                        help="跳过 SSL 证书验证")
    parser.add_argument("--timeout", type=int, default=10,
                        help="HTTP 请求超时时间(秒) (默认: 10)")

    # 部署模式
    parser.add_argument("--mode", choices=["auto", "k8s", "docker", "vm"], default="auto",
                        help="部署模式 (默认: auto)")

    # K8s 参数
    parser.add_argument("--kubeconfig", help="kubeconfig 文件路径")
    parser.add_argument("--kube-context", help="kubeconfig context 名称")
    parser.add_argument("--namespace", "-n", default="default",
                        help="MinIO 所在的 K8s namespace (默认: default)")
    parser.add_argument("--label-selector", "-l", default="app=minio",
                        help="K8s label selector (默认: app=minio)")

    # Docker 参数
    parser.add_argument("--docker-container", help="Docker 容器名称或 ID")
    parser.add_argument("--docker-image", default="minio/minio",
                        help="Docker 镜像名称 (默认: minio/minio)")

    # 检查控制
    parser.add_argument("--check", "-c",
                        help="只运行指定模块 (逗号分隔), 可选: " +
                             ", ".join(n for n, _, _ in _ALL_CHECKS))
    parser.add_argument("--verbose", "-v", action="store_true", help="显示所有详细信息")

    # 必需 bucket
    parser.add_argument("--required-buckets",
                        help="必须存在的 bucket (逗号分隔)")

    return parser.parse_args()


def main():
    args = parse_args()

    print("\033[1m")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║            MinIO 专项健康检查                             ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\033[0m")

    # 解析必需 bucket
    required_buckets = args.required_buckets.split(",") if args.required_buckets else None

    # 初始化上下文
    try:
        print(f"⏳ 连接 MinIO ({args.endpoint}) ...")
        ctx = init_context(
            endpoint=args.endpoint,
            access_key=args.access_key,
            secret_key=args.secret_key,
            secure=args.secure,
            verify_ssl=not args.no_verify_ssl,
            timeout=args.timeout,
            deploy_mode=args.mode,
            kubeconfig=args.kubeconfig,
            kube_context=args.kube_context,
            namespace=args.namespace,
            label_selector=args.label_selector,
            docker_container=args.docker_container,
            docker_image=args.docker_image,
            required_buckets=required_buckets,
        )
        if "connect_error" in ctx:
            print(f"\033[33m⚠️  MinIO 连接异常: {ctx['connect_error']}\033[0m")
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
