# K8s 平台层健康检查工具

一键检查 Kubernetes 集群健康状态，覆盖 12 个检查维度。

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
