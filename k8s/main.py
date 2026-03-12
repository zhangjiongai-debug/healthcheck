#!/usr/bin/env python3
"""
K8s 平台层健康检查 —— 一键检查脚本

用法:
    python -m k8s.main                          # 使用默认 kubeconfig
    python -m k8s.main --kubeconfig ~/.kube/config --context my-cluster
    python -m k8s.main --verbose                 # 显示所有详情
    python -m k8s.main --check node,workload     # 只跑指定模块
    python -m k8s.main --namespace ns1,ns2       # 指定检查的命名空间
"""

import argparse
import sys
import time

from .client import init_client
from .result import CheckGroup, print_report
from .checks import (
    cluster_connectivity,
    control_plane,
    node_health,
    namespace_check,
    workload,
    service_ingress,
    config_secret,
    storage,
    resource_capacity,
    network,
    events_logs,
    risk_warning,
)

# 检查模块注册表: (名称, 模块, 是否需要额外参数)
_ALL_CHECKS = [
    ("connectivity",   cluster_connectivity,  False),
    ("control-plane",  control_plane,         False),
    ("node",           node_health,           False),
    ("namespace",      namespace_check,       True),   # 支持 include_ns 参数
    ("workload",       workload,              False),
    ("service",        service_ingress,       False),
    ("config",         config_secret,         False),
    ("storage",        storage,               False),
    ("resource",       resource_capacity,     False),
    ("network",        network,               False),
    ("events",         events_logs,           False),
    ("risk",           risk_warning,          False),
]


def parse_args():
    parser = argparse.ArgumentParser(description="K8s 平台层健康检查工具")
    parser.add_argument("--kubeconfig", help="kubeconfig 文件路径")
    parser.add_argument("--context", help="kubeconfig context 名称")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示所有详细信息")
    parser.add_argument("--check", "-c", help="只运行指定模块 (逗号分隔), 可选: " +
                        ", ".join(name for name, _, _ in _ALL_CHECKS))
    parser.add_argument("--namespace", "-n", help="指定检查的命名空间 (逗号分隔)")
    return parser.parse_args()


def main():
    args = parse_args()

    print("\033[1m")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          K8s 平台层健康检查                              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\033[0m")

    # 初始化客户端
    try:
        print("⏳ 连接集群...")
        clients = init_client(kubeconfig=args.kubeconfig, context=args.context)
        print("✅ 集群连接成功\n")
    except Exception as e:
        print(f"\033[31m❌ 无法连接集群: {e}\033[0m")
        sys.exit(1)

    # 确定要运行的检查模块
    if args.check:
        selected = set(args.check.split(","))
        checks_to_run = [(n, m, p) for n, m, p in _ALL_CHECKS if n in selected]
        unknown = selected - {n for n, _, _ in _ALL_CHECKS}
        if unknown:
            print(f"\033[33m⚠️  未知模块: {', '.join(unknown)}\033[0m\n")
    else:
        checks_to_run = _ALL_CHECKS

    include_ns = args.namespace.split(",") if args.namespace else None

    # 执行检查
    results: list[CheckGroup] = []
    total_checks = len(checks_to_run)

    for i, (name, module, needs_extra) in enumerate(checks_to_run, 1):
        print(f"⏳ [{i}/{total_checks}] 检查: {name} ...", end="", flush=True)
        start = time.time()
        try:
            if needs_extra and name == "namespace":
                group = module.check(clients, include_ns=include_ns)
            else:
                group = module.check(clients)
        except Exception as e:
            group = CheckGroup(f"{name} (执行异常)")
            group.fatal(name, f"模块执行异常: {e}")
        elapsed = time.time() - start
        print(f" 完成 ({elapsed:.1f}s)")
        results.append(group)

    # 输出报告
    print_report(results, verbose=args.verbose)


if __name__ == "__main__":
    main()
