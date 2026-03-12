# 健康检查工具集

包含两个独立模块：

- **K8s 平台层健康检查** — 一键检查 Kubernetes 集群健康状态，覆盖 12 个检查维度
- **Keycloak 专项健康检查** — 一键检查 Keycloak 服务健康状态，覆盖 7 个检查维度，兼容 K8s / Docker / VM 部署

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
