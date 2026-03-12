"""5.3 Bucket 与对象服务能力检查。

- 核心 bucket 是否存在
- bucket policy 是否正确
- 对象上传/下载/删除/列表是否正常
- presigned URL 是否可用
"""

from ..result import CheckGroup


def check(ctx: dict) -> CheckGroup:
    g = CheckGroup("5.3 Bucket 与对象服务能力")
    mc = ctx["mc"]

    if "connect_error" in ctx:
        g.fatal("服务连接", f"无法连接: {ctx['connect_error']}")
        return g

    if not mc.access_key:
        g.warn("认证信息", "未提供 access_key，跳过 Bucket 检查")
        return g

    # ── 列出 bucket ──
    buckets = mc.list_buckets_sdk()
    if buckets is None:
        # SDK 不可用，使用 mc CLI
        buckets = _list_buckets_via_mc(mc)

    if buckets is None:
        g.error("Bucket 列表", "无法获取 bucket 列表 (minio SDK 未安装且 mc CLI 不可用)")
        return g

    if not buckets:
        g.warn("Bucket 列表", "无 bucket")
    else:
        g.ok("Bucket 列表", f"共 {len(buckets)} 个 bucket",
             detail=", ".join(buckets[:20]) + ("..." if len(buckets) > 20 else ""))

    # ── 检查必需 bucket ──
    required = ctx.get("required_buckets", [])
    if required:
        existing = set(buckets) if buckets else set()
        for rb in required:
            if rb in existing:
                g.ok(f"必需 Bucket [{rb}]", "存在")
            else:
                g.error(f"必需 Bucket [{rb}]", "不存在!")

    # ── Bucket Policy 检查 (通过 mc) ──
    if buckets:
        _check_bucket_policies(mc, buckets, g)

    # ── S3 CRUD 测试 ──
    if buckets:
        _test_s3_operations(mc, buckets, g)

    return g


def _list_buckets_via_mc(mc) -> list:
    """通过 mc CLI 列出 bucket。"""
    output = mc.mc_command(["ls", "_healthcheck"], timeout=10)
    if output is None:
        return None
    buckets = []
    for line in output.strip().splitlines():
        # mc ls 输出格式: [2024-01-01 00:00:00 UTC]     0B bucket_name/
        parts = line.strip().split()
        if parts:
            name = parts[-1].rstrip("/")
            if name:
                buckets.append(name)
    return buckets


def _check_bucket_policies(mc, buckets, g):
    """检查 bucket 访问策略。"""
    # 仅通过 mc 检查
    if not mc.mc_available():
        return

    public_buckets = []
    for bucket in buckets[:10]:  # 只检查前 10 个
        output = mc.mc_command(
            ["anonymous", "get", f"_healthcheck/{bucket}"], timeout=10)
        if output and "public" in output.lower():
            public_buckets.append(bucket)

    if public_buckets:
        g.warn("公开访问 Bucket",
               f"{len(public_buckets)} 个 bucket 允许公开访问",
               detail=", ".join(public_buckets))


def _test_s3_operations(mc, buckets, g):
    """使用 SDK 测试 S3 CRUD 操作。"""
    # 选择第一个 bucket 进行测试
    test_bucket = buckets[0]

    results = mc.s3_test_operations(test_bucket)

    if "error" in results:
        g.warn("S3 操作测试", f"测试跳过: {results['error']}")
        return

    # PUT
    if results.get("put") is True:
        g.ok("对象上传 (PUT)", f"bucket [{test_bucket}] 上传成功")
    elif results.get("put"):
        g.error("对象上传 (PUT)", f"上传失败: {results['put']}")

    # GET
    if results.get("get") is True:
        g.ok("对象下载 (GET)", f"bucket [{test_bucket}] 下载并验证成功")
    elif results.get("get") is False:
        g.error("对象下载 (GET)", "下载数据不一致")
    elif results.get("get"):
        g.error("对象下载 (GET)", f"下载失败: {results['get']}")

    # LIST
    if results.get("list") is True:
        g.ok("对象列表 (LIST)", f"bucket [{test_bucket}] 列表正常")
    elif results.get("list"):
        g.error("对象列表 (LIST)", f"列表失败: {results['list']}")

    # PRESIGNED
    if results.get("presigned") is True:
        g.ok("Presigned URL", "生成成功")
    elif results.get("presigned"):
        g.warn("Presigned URL", f"生成失败: {results['presigned']}")

    # DELETE
    if results.get("delete") is True:
        g.ok("对象删除 (DELETE)", f"bucket [{test_bucket}] 删除成功")
    elif results.get("delete"):
        g.error("对象删除 (DELETE)", f"删除失败: {results['delete']}")
