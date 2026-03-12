# 健康检查工具集

包含六个独立模块：

- **K8s 平台层健康检查** — 一键检查 Kubernetes 集群健康状态，覆盖 12 个检查维度
- **Keycloak 专项健康检查** — 一键检查 Keycloak 服务健康状态，覆盖 7 个检查维度，兼容 K8s / Docker / VM 部署
- **PostgreSQL 专项健康检查** — 一键检查 PostgreSQL 数据库健康状态，覆盖 7 个检查维度，兼容 K8s / Docker / VM 部署
- **MinIO 专项健康检查** — 一键检查 MinIO 对象存储健康状态，覆盖 6 个检查维度，兼容 K8s / Docker / VM 部署
- **Jenkins 专项健康检查** — 一键检查 Jenkins CI/CD 健康状态，覆盖 7 个检查维度，兼容 K8s / Docker / VM 部署
- **GitLab 专项健康检查** — 一键检查 GitLab 健康状态，覆盖 8 个检查维度，兼容 K8s / Docker / VM 部署

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

在项目根目录下运行：

```bash
# 基本用法（使用默认 kubeconfig）
python -m k8s.main

# 指定 kubeconfig 和 context
python -m k8s.main --kubeconfig ~/.kube/config --context my-cluster

# 只运行指定模块（逗号分隔）
python -m k8s.main --check node,workload,storage

# 只检查指定命名空间
python -m k8s.main --namespace default,jenkins

# 组合使用
python -m k8s.main --check workload,service --namespace default
```

## 命令行参数

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--kubeconfig` | | kubeconfig 文件路径 |
| `--context` | | kubeconfig context 名称 |
| `--check` | `-c` | 只运行指定模块（逗号分隔） |
| `--namespace` | `-n` | 只检查指定命名空间（逗号分隔） |
| `--verbose` | `-v` | 显示所有详细信息 |

## 检查模块

| 模块名 | 说明 |
|--------|------|
| `connectivity` | 集群连通性与 API 健康 |
| `control-plane` | 控制面组件（etcd、apiserver、scheduler、controller-manager） |
| `node` | 节点健康（Ready 状态、资源压力、Taint） |
| `namespace` | Namespace 维度（状态、ResourceQuota、LimitRange） |
| `workload` | 工作负载（Pod、Deployment、StatefulSet、DaemonSet、Job） |
| `service` | Service / Endpoint / Ingress |
| `config` | ConfigMap / Secret 引用完整性 |
| `storage` | PVC / PV / 存储挂载 |
| `resource` | 资源容量与规范（requests/limits、HPA） |
| `network` | 网络与 DNS（kube-dns、NetworkPolicy、LoadBalancer） |
| `events` | 事件与日志异常（Warning 事件聚合） |
| `risk` | 风险预警（单副本、latest 标签、PDB、反亲和） |

## 检查结果级别

- **OK** — 正常
- **WARN** — 告警，建议关注
- **ERROR** — 异常，需要处理
- **FATAL** — 严重故障，需立即处理

---

## Keycloak 专项健康检查

针对 Keycloak 自身的 7 个维度进行深度检查，兼容三种部署模式。

### 部署模式

| 模式 | 说明 |
|------|------|
| `auto` | 默认，按 K8s → Docker → VM 顺序自动检测 |
| `k8s` | Kubernetes 部署，额外检查 Pod 状态、副本数、headless service、日志等 |
| `docker` | Docker 容器部署，额外检查容器运行状态、健康标记、容器日志等 |
| `vm` | 虚拟机 / 裸机部署，纯 HTTP 端点检查 |

### 使用方法

```bash
# 最简用法（自动检测部署模式）
python -m keycloak.main --url http://localhost:8080

# 带管理员凭证（启用 Admin API 相关检查）
python -m keycloak.main --url https://keycloak.example.com \
    --admin-user admin --admin-password secret

# K8s 模式，指定 namespace 和 label
python -m keycloak.main --url http://localhost:8080 --mode k8s \
    --namespace keycloak --label-selector app=keycloak

# Docker 模式，指定容器名
python -m keycloak.main --url http://localhost:8080 --mode docker \
    --docker-container my-keycloak

# VM 模式
python -m keycloak.main --url http://localhost:8080 --mode vm \
    --admin-user admin --admin-password secret

# 只跑指定模块
python -m keycloak.main --url http://localhost:8080 --check instance,auth,security

# 指定必须存在的 realm 和 client
python -m keycloak.main --url http://localhost:8080 \
    --admin-user admin --admin-password secret \
    --required-realms master,myrealm \
    --required-clients "myrealm:frontend-app,backend-api"

# 跳过 SSL 验证 + 显示详情
python -m keycloak.main --url https://keycloak.local:8443 \
    --no-verify-ssl --verbose
```

### 命令行参数

**Keycloak 连接：**

| 参数 | 说明 |
|------|------|
| `--url` | Keycloak 基础 URL（必填，如 `http://localhost:8080`） |
| `--admin-user` | 管理员用户名（启用 Admin API 检查） |
| `--admin-password` | 管理员密码 |
| `--no-verify-ssl` | 跳过 SSL 证书验证 |
| `--timeout` | HTTP 请求超时时间，默认 10 秒 |

**部署模式：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--mode` | | 部署模式：`auto` / `k8s` / `docker` / `vm` |
| `--kubeconfig` | | kubeconfig 文件路径（K8s 模式） |
| `--kube-context` | | kubeconfig context 名称（K8s 模式） |
| `--namespace` | `-n` | Keycloak 所在 namespace，默认 `default`（K8s 模式） |
| `--label-selector` | `-l` | Pod label selector，默认 `app=keycloak`（K8s 模式） |
| `--docker-container` | | Docker 容器名称或 ID（Docker 模式） |

**检查控制：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--check` | `-c` | 只运行指定模块（逗号分隔） |
| `--verbose` | `-v` | 显示所有详细信息 |
| `--required-realms` | | 必须存在的 realm（逗号分隔，默认 `master`） |
| `--required-clients` | | 必须存在的 client（格式：`realm:client1,client2;realm2:client3`） |

### 检查模块

| 模块名 | 说明 |
|--------|------|
| `instance` | 4.1 实例状态 — Pod/容器运行状态、副本数、重启次数、health 端点、管理控制台 |
| `database` | 4.2 数据库连接 — DB 健康检查、Agroal 连接池使用率/超时、日志中的 DB 错误 |
| `realm` | 4.3 Realm/Client/Federation — realm 存在性、client 配置、redirect URI、IdP、LDAP federation、管理员账户 |
| `auth` | 4.4 认证能力 — 登录页、OIDC 发现文档、JWKS、token 签发/刷新、logout、UserInfo |
| `cluster` | 4.5 集群/缓存 — Infinispan 状态、JGroups 集群大小、headless service、session 缓存、集群日志 |
| `security` | 4.6 证书与安全 — TLS 证书过期检测、默认密码检测、暴力破解保护、SSL 要求、通配符 URI、Service 暴露 |
| `performance` | 4.7 性能预警 — 登录失败率、JVM 堆内存、Full GC、HTTP 延迟、token 延迟实测、session 数量 |

### 各模式检查能力对比

| 检查项 | K8s | Docker | VM |
|--------|:---:|:------:|:--:|
| Health 端点 | ✅ | ✅ | ✅ |
| 管理控制台 | ✅ | ✅ | ✅ |
| OIDC / Token / JWKS | ✅ | ✅ | ✅ |
| Admin API (realm/client/安全配置) | ✅ | ✅ | ✅ |
| Metrics (连接池/JVM/GC/延迟) | ✅ | ✅ | ✅ |
| TLS 证书检查 | ✅ | ✅ | ✅ |
| Pod/容器状态与副本数 | ✅ | ✅ | — |
| 容器/Pod 日志分析 | ✅ | ✅ | — |
| Headless Service / Session Affinity | ✅ | — | — |
| Service 暴露检查 | ✅ | — | — |
| Realm 多副本漂移检测 | ✅ | — | — |

---

## PostgreSQL 专项健康检查

针对 PostgreSQL 自身的 7 个维度进行深度检查，兼容三种部署模式。通过 SQL 查询获取数据库内部状态，同时结合基础设施层（K8s Pod / Docker 容器 / VM 进程）进行全面诊断。

### 部署模式

| 模式 | 说明 |
|------|------|
| `auto` | 默认，按 K8s → Docker → VM 顺序自动检测 |
| `k8s` | Kubernetes 部署，额外检查 Pod 状态、StatefulSet 副本数、PVC 空间、Operator 检测 |
| `docker` | Docker 容器部署，额外检查容器运行状态、容器内磁盘空间 |
| `vm` | 虚拟机 / 裸机部署，额外检查本地 postgres 进程、磁盘空间、I/O |

### 使用方法

```bash
# 最简用法（自动检测部署模式）
python -m postgresql.main --host 127.0.0.1 --user postgres --password secret

# 指定数据库和端口
python -m postgresql.main --host pg.example.com --port 5432 \
    --user postgres --password secret --dbname mydb

# K8s 模式，指定 namespace 和 label
python -m postgresql.main --host 127.0.0.1 --port 15432 \
    --user postgres --password secret --mode k8s \
    --namespace postgres --label-selector app.kubernetes.io/name=postgresql

# Docker 模式，指定容器名
python -m postgresql.main --host 127.0.0.1 --mode docker \
    --user postgres --password secret \
    --docker-container my-postgres

# VM 模式
python -m postgresql.main --host 127.0.0.1 --mode vm \
    --user postgres --password secret

# 只跑指定模块
python -m postgresql.main --host 127.0.0.1 --user postgres --password secret \
    --check instance,connection,risk

# 检查指定业务数据库是否存在且可连接
python -m postgresql.main --host 127.0.0.1 --user postgres --password secret \
    --check-databases mydb1,mydb2,mydb3

# 显示详情
python -m postgresql.main --host 127.0.0.1 --user postgres --password secret --verbose
```

### 命令行参数

**PostgreSQL 连接：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--host` | `-H` | PostgreSQL 主机地址，默认 `127.0.0.1` |
| `--port` | `-p` | PostgreSQL 端口，默认 `5432` |
| `--user` | `-U` | PostgreSQL 用户名，默认 `postgres` |
| `--password` | `-W` | PostgreSQL 密码 |
| `--dbname` | `-d` | 连接的数据库名，默认 `postgres` |
| `--connect-timeout` | | 连接超时时间（秒），默认 `10` |

**部署模式：**

| 参数 | 说明 |
|------|------|
| `--mode` | 部署模式：`auto` / `k8s` / `docker` / `vm` |
| `--kubeconfig` | kubeconfig 文件路径（K8s 模式） |
| `--kube-context` | kubeconfig context 名称（K8s 模式） |
| `--namespace` / `-n` | PostgreSQL 所在 namespace，默认 `default`（K8s 模式） |
| `--label-selector` / `-l` | Pod label selector，默认 `app=postgresql`（K8s 模式） |
| `--docker-container` | Docker 容器名称或 ID（Docker 模式） |
| `--docker-image` | Docker 镜像名称，默认 `postgres`（Docker 模式） |

**检查控制：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--check` | `-c` | 只运行指定模块（逗号分隔） |
| `--verbose` | `-v` | 显示所有详细信息 |
| `--check-databases` | | 需要检查的业务数据库（逗号分隔） |

### 检查模块

| 模块名 | 说明 |
|--------|------|
| `instance` | 6.1 实例基础状态 — PG 版本、主从角色、可读可写验证、进程运行时长、Pod/容器状态、副本数 |
| `connection` | 6.2 连接与认证 — 端口可达性、用户认证、业务库连通、连接数使用率、idle in transaction、PgBouncer 检测 |
| `replication` | 6.3 主从复制/高可用 — WAL sender/receiver 状态、复制延迟（时间+字节）、复制槽、Patroni/repmgr/Operator 检测、split brain 风险 |
| `storage` | 6.4 存储与 WAL — 数据目录空间、数据库大小、PVC 状态、WAL 大小与堆积、checkpoint 频率、WAL 归档、磁盘 I/O |
| `internal` | 6.5 数据库内部健康 — SQL 执行、系统表可查、锁等待、deadlock、长事务、autovacuum 状态与及时性、表膨胀、无效索引、事务回滚率 |
| `backup` | 6.6 备份与恢复 — base backup 状态、WAL 归档连续性、RPO 评估、备份进度、pgBackRest/barman/wal-g 检测 |
| `risk` | 6.7 风险预警 — 连接数耗尽、复制延迟升高、XID wraparound、长事务阻塞 vacuum、checkpoint 过密、锁冲突、归档失败、角色异常 |

### 各模式检查能力对比

| 检查项 | K8s | Docker | VM |
|--------|:---:|:------:|:--:|
| SQL 层检查（连接/复制/锁/事务/vacuum/bloat） | ✅ | ✅ | ✅ |
| WAL / Checkpoint / 归档状态 | ✅ | ✅ | ✅ |
| 备份工具检测（pgBackRest/barman/wal-g） | ✅ | ✅ | ✅ |
| XID Wraparound 风险检测 | ✅ | ✅ | ✅ |
| HA 管理器检测（Patroni/repmgr） | ✅ | ✅ | ✅ |
| Pod/容器状态与副本数 | ✅ | ✅ | — |
| PVC 空间检查 | ✅ | — | — |
| Operator 检测（Zalando/CloudNativePG/CrunchyData） | ✅ | — | — |
| Split brain 检测（多 primary Pod） | ✅ | — | — |
| 容器内磁盘空间检查 | — | ✅ | — |
| 本地进程检测 / df / iostat | — | — | ✅ |

---

## MinIO 专项健康检查

针对 MinIO 自身的 6 个维度进行深度检查，兼容三种部署模式。通过 S3 API 和管理接口获取 MinIO 内部状态，同时结合基础设施层（K8s Pod / Docker 容器 / VM 进程）进行全面诊断。

### 部署模式

| 模式 | 说明 |
|------|------|
| `auto` | 默认，按 K8s → Docker → VM 顺序自动检测 |
| `k8s` | Kubernetes 部署，额外检查 Pod 状态、Deployment/StatefulSet 副本数、PVC 空间 |
| `docker` | Docker 容器部署，额外检查容器运行状态、容器内磁盘空间 |
| `vm` | 虚拟机 / 裸机部署，额外检查本地 minio 进程、磁盘空间、inode |

### 使用方法

```bash
# 最简用法（自动检测部署模式）
python -m minio.main --endpoint localhost:9000

# 带 Access Key / Secret Key（启用 S3 和管理 API 检查）
python -m minio.main --endpoint minio.example.com:9000 \
    --access-key minioadmin --secret-key minioadmin

# K8s 模式，指定 namespace 和 label
python -m minio.main --endpoint localhost:9000 --mode k8s \
    --namespace minio --label-selector app=minio

# Docker 模式，指定容器名
python -m minio.main --endpoint localhost:9000 --mode docker \
    --docker-container my-minio

# VM 模式
python -m minio.main --endpoint localhost:9000 --mode vm \
    --access-key minioadmin --secret-key minioadmin

# 只跑指定模块
python -m minio.main --endpoint localhost:9000 --check instance,bucket,performance

# 检查必须存在的 bucket
python -m minio.main --endpoint localhost:9000 \
    --access-key minioadmin --secret-key minioadmin \
    --required-buckets uploads,backups,logs

# HTTPS + 跳过 SSL 验证 + 显示详情
python -m minio.main --endpoint minio.local:9000 \
    --secure --no-verify-ssl --verbose
```

### 命令行参数

**MinIO 连接：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--endpoint` | `-e` | MinIO 端点地址，默认 `localhost:9000` |
| `--access-key` | `-ak` | MinIO Access Key |
| `--secret-key` | `-sk` | MinIO Secret Key |
| `--secure` | | 使用 HTTPS 连接 |
| `--no-verify-ssl` | | 跳过 SSL 证书验证 |
| `--timeout` | | HTTP 请求超时时间，默认 10 秒 |

**部署模式：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--mode` | | 部署模式：`auto` / `k8s` / `docker` / `vm` |
| `--kubeconfig` | | kubeconfig 文件路径（K8s 模式） |
| `--kube-context` | | kubeconfig context 名称（K8s 模式） |
| `--namespace` | `-n` | MinIO 所在 namespace，默认 `default`（K8s 模式） |
| `--label-selector` | `-l` | Pod label selector，默认 `app=minio`（K8s 模式） |
| `--docker-container` | | Docker 容器名称或 ID（Docker 模式） |
| `--docker-image` | | Docker 镜像名称，默认 `minio/minio`（Docker 模式） |

**检查控制：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--check` | `-c` | 只运行指定模块（逗号分隔） |
| `--verbose` | `-v` | 显示所有详细信息 |
| `--required-buckets` | | 必须存在的 bucket（逗号分隔） |

### 检查模块

| 模块名 | 说明 |
|--------|------|
| `instance` | 5.1 实例与集群状态 — Pod/容器运行状态、副本数、健康端点、集群节点在线、磁盘状态、运行模式与版本 |
| `storage` | 5.2 存储层状态 — 磁盘空间使用率、只读检测、离线磁盘、PVC 状态、inode 使用率、存储延迟 |
| `bucket` | 5.3 Bucket 与对象服务能力 — bucket 列表、必需 bucket 检查、S3 CRUD 测试（PUT/GET/LIST/DELETE）、Presigned URL、bucket 访问策略 |
| `admin` | 5.4 管理与认证 — 管理控制台可达性、Access Key/Secret Key 验证、用户/策略列表、OIDC/LDAP 对接 |
| `data` | 5.5 数据保护与后台任务 — versioning 状态、lifecycle 规则、站点复制、self-heal 任务、后台扫描状态 |
| `performance` | 5.6 性能与告警 — 5xx 错误率、请求延迟(TTFB)、网络吞吐、集群磁盘使用率、节点降级、quorum 风险、对象数量 |

### 各模式检查能力对比

| 检查项 | K8s | Docker | VM |
|--------|:---:|:------:|:--:|
| S3 层检查（bucket/对象 CRUD/presigned URL） | ✅ | ✅ | ✅ |
| 健康端点（liveness/readiness/cluster） | ✅ | ✅ | ✅ |
| Prometheus Metrics（延迟/错误率/吞吐） | ✅ | ✅ | ✅ |
| mc admin info（集群节点/磁盘/版本） | ✅ | ✅ | ✅ |
| 认证与策略检查 | ✅ | ✅ | ✅ |
| 数据保护（versioning/lifecycle/replication） | ✅ | ✅ | ✅ |
| Quorum 风险检测 | ✅ | ✅ | ✅ |
| Pod/容器状态与副本数 | ✅ | ✅ | — |
| PVC 空间检查 | ✅ | — | — |
| 容器内磁盘空间检查 | — | ✅ | — |
| 本地进程检测 / df / inode | — | — | ✅ |

---

## Jenkins 专项健康检查

针对 Jenkins 自身的 7 个维度进行深度检查，兼容三种部署模式。通过 Jenkins JSON API 和 Script Console (Groovy) 获取内部状态，同时结合基础设施层（K8s Pod / Docker 容器 / VM 进程）进行全面诊断。

### 部署模式

| 模式 | 说明 |
|------|------|
| `auto` | 默认，按 K8s → Docker → VM 顺序自动检测 |
| `k8s` | Kubernetes 部署，额外检查 Pod 状态、StatefulSet 副本数、PVC 空间 |
| `docker` | Docker 容器部署，额外检查容器运行状态 |
| `vm` | 虚拟机 / 裸机部署，额外检查本地 jenkins 进程 |

### 使用方法

```bash
# 最简用法（自动检测部署模式，无认证仅检查基础项）
python -m jenkins.main --url http://localhost:8080

# 带管理员凭证（启用 Script Console 深度检查）
python -m jenkins.main --url http://localhost:8080 \
    --user admin --password secret

# K8s 模式，指定 namespace 和 label
python -m jenkins.main --url http://localhost:8080 --mode k8s \
    --namespace jenkins --label-selector app.kubernetes.io/name=jenkins

# Docker 模式，指定容器名
python -m jenkins.main --url http://localhost:8080 --mode docker \
    --docker-container my-jenkins

# VM 模式
python -m jenkins.main --url http://localhost:8080 --mode vm \
    --user admin --password secret

# 只跑指定模块
python -m jenkins.main --url http://localhost:8080 \
    --user admin --password secret \
    --check controller,plugin,agent

# HTTPS + 跳过 SSL 验证 + 显示详情
python -m jenkins.main --url https://jenkins.local:8443 \
    --no-verify-ssl --verbose
```

### 命令行参数

**Jenkins 连接：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--url` | | Jenkins 基础 URL，默认 `http://localhost:8080` |
| `--user` | `-u` | Jenkins 用户名 |
| `--password` | `-p` | Jenkins 密码或 API Token |
| `--no-verify-ssl` | | 跳过 SSL 证书验证 |
| `--timeout` | | HTTP 请求超时时间，默认 15 秒 |

**部署模式：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--mode` | | 部署模式：`auto` / `k8s` / `docker` / `vm` |
| `--kubeconfig` | | kubeconfig 文件路径（K8s 模式） |
| `--kube-context` | | kubeconfig context 名称（K8s 模式） |
| `--namespace` | `-n` | Jenkins 所在 namespace，默认 `default`（K8s 模式） |
| `--label-selector` | `-l` | Pod label selector，默认 `app.kubernetes.io/name=jenkins`（K8s 模式） |
| `--docker-container` | | Docker 容器名称或 ID（Docker 模式） |
| `--docker-image` | | Docker 镜像名称，默认 `jenkins/jenkins`（Docker 模式） |

**检查控制：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--check` | `-c` | 只运行指定模块（逗号分隔） |
| `--verbose` | `-v` | 显示所有详细信息 |

### 检查模块

| 模块名 | 说明 |
|--------|------|
| `controller` | 1. 控制器状态 — Web UI/API 可达性、登录页、Pod/容器状态、副本数、重启次数 |
| `init` | 2. 初始化与配置 — 启动完成检查、系统日志 SEVERE 错误、JCasC 配置、安全域/授权策略、CSRF 保护 |
| `plugin` | 3. 插件健康 — 插件总数与状态、加载失败插件、插件依赖完整性、可更新插件 |
| `agent` | 4. Agent/Executor — 节点在线/离线状态、Executor 数量与利用率、K8s Cloud 配置、离线原因 |
| `job` | 5. Job/Pipeline — 构建队列积压、卡住的任务、Job 总数、长时间构建检测、24h 构建失败率 |
| `dependency` | 6. 依赖检查 — Jenkins Home 磁盘空间、PVC 状态、Credentials 配置、Git 工具、SMTP 邮件通知 |
| `performance` | 7. 性能与风险 — JVM 堆内存、GC 次数与耗时、线程数/死锁检测、单副本风险、Agent 全离线风险 |

### 各模式检查能力对比

| 检查项 | K8s | Docker | VM |
|--------|:---:|:------:|:--:|
| JSON API / Web UI 检查 | ✅ | ✅ | ✅ |
| Script Console (Groovy) 深度检查 | ✅ | ✅ | ✅ |
| 插件状态与依赖 | ✅ | ✅ | ✅ |
| Agent/Executor 状态 | ✅ | ✅ | ✅ |
| Job/Pipeline/构建队列 | ✅ | ✅ | ✅ |
| JVM 内存/GC/线程/死锁 | ✅ | ✅ | ✅ |
| K8s Cloud 动态 Agent 配置 | ✅ | ✅ | ✅ |
| Credentials / SCM / 邮件配置 | ✅ | ✅ | ✅ |
| Pod/容器状态与副本数 | ✅ | ✅ | — |
| PVC 空间检查 | ✅ | — | — |
| 单点风险检测（StatefulSet 副本数） | ✅ | — | — |
| 本地进程检测 | — | — | ✅ |

---

## GitLab 专项健康检查

针对 GitLab 自身的 8 个维度进行深度检查，兼容三种部署模式。通过 GitLab API v4 和内置健康端点获取 GitLab 内部状态，同时结合基础设施层（K8s Pod / Docker 容器 / VM 进程）进行全面诊断。

数据依赖（PostgreSQL / Redis / MinIO）仅做简化的连通性检查，深度诊断请分别使用对应的专项模块。

### 部署模式

| 模式 | 说明 |
|------|------|
| `auto` | 默认，按 K8s → Docker → VM 顺序自动检测 |
| `k8s` | Kubernetes 部署，额外检查各组件 Pod 状态、Deployment/StatefulSet 副本数、PVC 空间、单点风险 |
| `docker` | Docker 容器部署，额外检查容器运行状态 |
| `vm` | 虚拟机 / 裸机部署，额外检查本地进程（puma/sidekiq/gitaly/workhorse/nginx） |

### 使用方法

```bash
# 最简用法（自动检测部署模式，无 Token 仅检查健康端点）
python -m gitlab.main --url http://localhost:8080

# 带 Private Access Token（启用 API 深度检查）
python -m gitlab.main --url http://localhost:8080 --token glpat-xxxx

# K8s 模式，指定 namespace 和 label
python -m gitlab.main --url http://localhost:8080 --mode k8s \
    --namespace default --label-selector app.kubernetes.io/name=gitlab

# Docker 模式，指定容器名
python -m gitlab.main --url http://localhost:8080 --mode docker \
    --docker-container gitlab

# VM 模式
python -m gitlab.main --url http://localhost:8080 --mode vm --token glpat-xxxx

# 只跑指定模块
python -m gitlab.main --url http://localhost:8080 --token glpat-xxxx \
    --check core,web,sidekiq,runner

# HTTPS + 跳过 SSL 验证 + 显示详情
python -m gitlab.main --url https://gitlab.local \
    --no-verify-ssl --verbose --token glpat-xxxx
```

### 命令行参数

**GitLab 连接：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--url` | | GitLab 基础 URL，默认 `http://localhost:8080` |
| `--token` | `-t` | GitLab Private Token (Personal Access Token) |
| `--no-verify-ssl` | | 跳过 SSL 证书验证 |
| `--timeout` | | HTTP 请求超时时间，默认 15 秒 |

**部署模式：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--mode` | | 部署模式：`auto` / `k8s` / `docker` / `vm` |
| `--kubeconfig` | | kubeconfig 文件路径（K8s 模式） |
| `--kube-context` | | kubeconfig context 名称（K8s 模式） |
| `--namespace` | `-n` | GitLab 所在 namespace，默认 `default`（K8s 模式） |
| `--label-selector` | `-l` | Pod label selector，默认 `app.kubernetes.io/name=gitlab`（K8s 模式） |
| `--docker-container` | | Docker 容器名称或 ID（Docker 模式） |
| `--docker-image` | | Docker 镜像名称，默认 `gitlab/gitlab-ce`（Docker 模式） |

**检查控制：**

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--check` | `-c` | 只运行指定模块（逗号分隔） |
| `--verbose` | `-v` | 显示所有详细信息 |

### 检查模块

| 模块名 | 说明 |
|--------|------|
| `core` | 1. 核心服务状态 — 各组件 Pod/容器状态（Webservice/Sidekiq/Gitaly/Shell/Toolbox/KAS/Registry）、副本数、重启次数 |
| `web` | 2. 页面与 API 可用性 — 健康端点（health/readiness/liveness）、Web 首页、登录页、API metadata、版本信息 |
| `gitaly` | 3. Gitaly/Repository Storage — Gitaly 连接、仓库访问、StatefulSet/PVC 状态、存储容量 |
| `sidekiq` | 4. Sidekiq/后台任务 — 队列积压、任务延迟、失败率、进程负载、各队列详情 |
| `dependencies` | 5. 数据依赖 — DB/Redis/Gitaly 连通性(via readiness)、后台 Migration、对象存储、K8s 层 PostgreSQL/Redis/MinIO Pod 状态 |
| `runner` | 6. Runner 检查 — Runner 在线/离线/暂停状态、类型统计、最近 Job 成功率、K8s Runner Pod 状态 |
| `functionality` | 7. 功能面 — 仓库浏览、用户认证、Pipeline 功能、Container Registry、Package Registry、应用设置 |
| `risk` | 8. 风险预警 — Sidekiq 严重积压、Runner 全离线、TLS 证书过期、单点风险（单副本 Deployment/StatefulSet）、实例规模 |

### 各模式检查能力对比

| 检查项 | K8s | Docker | VM |
|--------|:---:|:------:|:--:|
| 健康端点（health/readiness/liveness） | ✅ | ✅ | ✅ |
| API v4（版本/项目/Pipeline/Runner/Sidekiq） | ✅ | ✅ | ✅ |
| 仓库浏览与 Git 操作验证 | ✅ | ✅ | ✅ |
| Runner 在线状态与 Job 成功率 | ✅ | ✅ | ✅ |
| TLS 证书过期检测 | ✅ | ✅ | ✅ |
| 应用设置与安全配置检查 | ✅ | ✅ | ✅ |
| 各组件 Pod/容器状态与副本数 | ✅ | ✅ | — |
| PVC 空间检查（Gitaly 存储） | ✅ | — | — |
| 单点风险检测（Deployment/StatefulSet 副本数） | ✅ | — | — |
| PostgreSQL/Redis/MinIO Pod 连通性检查 | ✅ | — | — |
| 本地进程检测（puma/sidekiq/gitaly/workhorse） | — | — | ✅ |
