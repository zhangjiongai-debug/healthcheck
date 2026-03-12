#!/usr/bin/env python3
"""
PostgreSQL 专项健康检查 —— 一键检查脚本

用法:
    python -m postgresql.main --host 127.0.0.1 --port 5432 --user postgres      # 最简用法
    python -m postgresql.main --host pg.example.com --password secret \\
        --dbname mydb                                                             # 指定数据库
    python -m postgresql.main --host 127.0.0.1 --mode k8s \\
        --namespace postgres --label-selector app=postgresql                      # K8s 模式
    python -m postgresql.main --host 127.0.0.1 --mode docker \\
        --docker-container my-postgres                                            # Docker 模式
    python -m postgresql.main --host 127.0.0.1 --mode vm                         # VM 模式
    python -m postgresql.main --host 127.0.0.1 --check instance,connection
    python -m postgresql.main --host 127.0.0.1 --verbose
    python -m postgresql.main --host 127.0.0.1 --check-databases mydb1,mydb2     # 检查业务库

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
    connection,
    replication,
    storage_wal,
    internal_health,
    backup,
    risk_warning,
)

_ALL_CHECKS = [
    ("instance",    instance,        "6.1 实例基础状态"),
    ("connection",  connection,      "6.2 连接与认证"),
    ("replication", replication,     "6.3 主从复制/高可用"),
    ("storage",     storage_wal,     "6.4 存储与 WAL"),
    ("internal",    internal_health, "6.5 数据库内部健康"),
    ("backup",      backup,          "6.6 备份与恢复能力"),
    ("risk",        risk_warning,    "6.7 风险预警"),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="PostgreSQL 专项健康检查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # PostgreSQL 连接
    parser.add_argument("--host", "-H", default="127.0.0.1",
                        help="PostgreSQL 主机地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, default=5432,
                        help="PostgreSQL 端口 (默认: 5432)")
    parser.add_argument("--user", "-U", default="postgres",
                        help="PostgreSQL 用户名 (默认: postgres)")
    parser.add_argument("--password", "-W", help="PostgreSQL 密码")
    parser.add_argument("--dbname", "-d", default="postgres",
                        help="连接的数据库名 (默认: postgres)")
    parser.add_argument("--connect-timeout", type=int, default=10,
                        help="连接超时时间(秒) (默认: 10)")

    # 部署模式
    parser.add_argument("--mode", choices=["auto", "k8s", "docker", "vm"], default="auto",
                        help="部署模式 (默认: auto)")

    # K8s 参数
    parser.add_argument("--kubeconfig", help="kubeconfig 文件路径")
    parser.add_argument("--kube-context", help="kubeconfig context 名称")
    parser.add_argument("--namespace", "-n", default="default",
                        help="PostgreSQL 所在的 K8s namespace (默认: default)")
    parser.add_argument("--label-selector", "-l", default="app=postgresql",
                        help="K8s label selector (默认: app=postgresql)")

    # Docker 参数
    parser.add_argument("--docker-container", help="Docker 容器名称或 ID")
    parser.add_argument("--docker-image", default="postgres",
                        help="Docker 镜像名称 (默认: postgres)")

    # 检查控制
    parser.add_argument("--check", "-c",
                        help="只运行指定模块 (逗号分隔), 可选: " +
                             ", ".join(n for n, _, _ in _ALL_CHECKS))
    parser.add_argument("--verbose", "-v", action="store_true", help="显示所有详细信息")

    # 业务库检查
    parser.add_argument("--check-databases",
                        help="需要检查的业务数据库 (逗号分隔)")

    return parser.parse_args()


def main():
    args = parse_args()

    print("\033[1m")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          PostgreSQL 专项健康检查                          ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\033[0m")

    # 初始化上下文
    try:
        print(f"⏳ 连接 PostgreSQL ({args.host}:{args.port}/{args.dbname}) ...")
        ctx = init_context(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            dbname=args.dbname,
            connect_timeout=args.connect_timeout,
            deploy_mode=args.mode,
            kubeconfig=args.kubeconfig,
            kube_context=args.kube_context,
            namespace=args.namespace,
            label_selector=args.label_selector,
            docker_container=args.docker_container,
            docker_image=args.docker_image,
        )
        if "connect_error" in ctx:
            print(f"\033[33m⚠️  数据库连接失败: {ctx['connect_error']}\033[0m")
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

    # 解析业务库列表
    check_databases = args.check_databases.split(",") if args.check_databases else None

    # 执行检查
    results: list[CheckGroup] = []
    total = len(checks_to_run)

    for i, (name, module, desc) in enumerate(checks_to_run, 1):
        print(f"⏳ [{i}/{total}] {desc} ...", end="", flush=True)
        start = time.time()
        try:
            if name == "connection":
                group = module.check(ctx, check_databases=check_databases)
            else:
                group = module.check(ctx)
        except Exception as e:
            group = CheckGroup(f"{desc} (执行异常)")
            group.fatal(name, f"模块执行异常: {e}")
        elapsed = time.time() - start
        print(f" 完成 ({elapsed:.1f}s)")
        results.append(group)

    # 清理连接
    try:
        ctx["pg"].close()
    except Exception:
        pass

    # 输出报告
    print_report(results, verbose=args.verbose)


if __name__ == "__main__":
    main()
