二、K8s 平台层健康检查项

---
2.1 集群基础连通性与 API 健康
1）API Server 可达性
- kubectl cluster-info 是否成功
- kubectl get --raw='/readyz' 是否返回 ok
- kubectl get --raw='/livez' 是否返回 ok
- API Server 响应延迟是否异常
- API Server 是否存在超时、5xx 错误
2）集群版本与兼容性
- Kubernetes Server 版本
- 各 Node kubelet 版本是否一致或兼容
- 是否存在明显版本偏差过大（控制面与节点、节点之间）
- 关键组件镜像版本是否符合预期

---
2.2 控制面组件检查
如果是托管集群，有些组件可能不可直接访问，可降级为 API/事件/Node Condition 层面判断。
1）etcd
- etcd Pod 是否 Running / Ready
- etcd 成员数是否完整
- etcd leader 是否存在
- 是否存在 unhealth member
- etcd DB size 是否过大
- etcd 磁盘延迟是否异常
- etcd 是否出现频繁选主
- etcd 日志是否有：
  - leader changed
  - apply request took too long
  - disk backend quota
  - mvcc database space exceeded
2）kube-apiserver
- Pod 是否 Running / Ready
- 重启次数是否异常
- /readyz 子项是否全部通过
- admission webhook 调用是否大量失败
- API 请求限流是否严重
- 证书是否即将过期
- 日志中是否有：
  - etcd 连接失败
  - webhook timeout
  - x509/certificate 问题
  - authn/authz 异常
3）kube-controller-manager
- Pod 是否 Running / Ready
- leader election 是否正常
- 重启次数是否异常
- 日志中是否有：
  - node lifecycle 异常
  - serviceaccount token/signing 问题
  - deployment/replicaset 同步异常
4）kube-scheduler
- Pod 是否 Running / Ready
- leader election 是否正常
- 调度循环是否异常
- 是否存在大量 Pending Pod
- 日志是否有：
  - no nodes available
  - preemption failed
  - unschedulable pods 激增
5）CoreDNS
- CoreDNS Pod 是否 Running / Ready
- 副本数是否满足
- DNS 查询是否正常
- 集群内服务域名解析是否成功
- 日志中是否有：
  - upstream timeout
  - SERVFAIL
  - loop detected
6）kube-proxy / CNI 插件
- DaemonSet 是否全量就绪
- 每个节点上 Pod 是否正常
- 日志是否有网络规则同步失败
- 是否存在 Service 不可达、跨节点 Pod 通信失败
- CNI 插件是否 Ready
- 网络策略组件是否异常

---
2.3 Node 节点健康检查
1）节点状态
- 是否全部 Ready
- 是否存在 NotReady、Unknown
- 是否存在 SchedulingDisabled
- 是否有节点长时间 NetworkUnavailable
2）节点资源压力
- CPU 使用率是否过高
- 内存使用率是否过高
- 磁盘使用率是否过高
- inode 使用率是否过高
- 是否存在以下 Condition：
  - MemoryPressure
  - DiskPressure
  - PIDPressure
  - NetworkUnavailable
3）节点系统风险
- 节点是否频繁重启
- kubelet 是否异常
- container runtime 是否异常
- 时钟漂移是否异常
- 文件系统只读风险
- 内核日志/系统日志中是否有 OOM、磁盘错误、网络错误
4）节点污点与调度异常
- 是否存在异常 taint
- 是否有业务 Pod 因 taint 无法调度
- 节点标签是否缺失/漂移，影响工作负载调度

---
2.4 Namespace 维度检查（支持跨命名空间）
1）目标命名空间枚举
- 遍历所有业务 namespace
- 支持排除系统 namespace（如 kube-system、kube-public 等）
- 支持重点检查指定 namespace 列表
2）Namespace 状态
- namespace 是否处于 Active
- 是否存在 Terminating 卡死
- resource quota 是否接近上限
- limitrange 是否缺失
- 是否存在大量孤儿资源

---
2.5 工作负载资源检查

---
2.5.1 Pod 检查
- Pod 是否处于 Running / Succeeded
- 是否存在：
  - Pending
  - CrashLoopBackOff
  - ImagePullBackOff
  - ErrImagePull
  - CreateContainerConfigError
  - CreateContainerError
  - OOMKilled
  - ContainerStatusUnknown
  - Terminating 长时间不结束
- Pod Ready 条件是否正常
- 容器重启次数是否超阈值
- Pod 启动耗时是否过长
- 是否存在探针失败：
  - livenessProbe
  - readinessProbe
  - startupProbe
- Pod 事件中是否有频繁异常
- 是否存在频繁重建/漂移
2.5.2 Deployment 检查
- desired / current / available / ready 副本是否一致
- 是否存在 unavailable replicas
- rollout 是否卡住
- 更新过程中是否超过 deadline
- 历史 revision 是否异常多
- 是否有 Pod 长期未 Ready
2.5.3 StatefulSet 检查
- replicas 是否满足
- ordinal Pod 是否缺失
- 更新是否卡住
- PVC 绑定是否正常
- 单个 Pod 异常是否影响整体服务
- 是否存在主从/有序启动相关异常
2.5.4 DaemonSet 检查
- desired / current / ready / available 是否一致
- 是否有节点未成功下发 Daemon Pod
- 更新是否卡住
- 是否有 node selector / taint 影响覆盖率
2.5.5 Job / CronJob 检查
- Job 是否失败
- backoff 是否超阈值
- 完成率是否正常
- CronJob 是否按时调度
- 是否存在长时间未触发
- 是否存在历史失败积压
- 并发策略是否导致任务堆积

---
2.6 Service / Endpoint / Ingress 检查
1）Service 检查
- Service 是否存在
- ClusterIP / Headless / NodePort / LoadBalancer 配置是否正常
- selector 是否正确
- 是否存在无 selector 或 selector 错误
2）Endpoints / EndpointSlice 检查
- Service 对应 Endpoint 是否为空
- Endpoint 数量是否与 Ready Pod 一致
- 是否存在后端实例全空
- Endpoint 是否包含 NotReady address
3）Ingress / Gateway 检查
- Ingress 规则是否存在
- Host / Path 配置是否正确
- 后端 Service 是否可解析
- TLS Secret 是否存在
- TLS 证书是否即将过期
- Ingress Controller 是否正常同步规则
- 是否存在 404/503 风险
- 跨 namespace 引用是否符合预期

---
2.7 配置与密钥检查
1）ConfigMap
- 关键 ConfigMap 是否存在
- 配置版本是否符合预期
- 是否存在空配置、字段缺失
- 配置是否已成功加载到 Pod
- 配置变更后是否未触发重载
2）Secret
- 关键 Secret 是否存在
- TLS Secret 格式是否正确
- 证书是否即将过期
- 用户名/密码类 Secret 是否缺失
- Secret 挂载是否成功
- Secret 被引用但资源不存在

---
2.8 PVC / PV / 存储检查
1）PVC 状态
- 是否 Bound
- 是否存在 Pending
- 容量是否接近阈值
- 扩容是否成功
- 存储类是否正确
2）PV 状态
- 是否 Available / Bound / Released / Failed
- 回收策略是否符合预期
- 存储后端是否正常
3）挂载与 I/O 风险
- Pod 是否挂载成功
- 是否存在只读挂载异常
- 是否存在文件系统损坏风险
- 磁盘空间、inode 是否逼近阈值
- 延迟是否过高

---
2.9 资源容量与资源规范检查
1）资源使用情况
- namespace 维度 CPU / Memory 总量
- Pod/容器实际使用量
- 是否存在热点节点
- 是否存在资源碎片化导致调度失败
2）requests / limits 检查
- 是否未设置 requests
- 是否未设置 limits
- requests/limits 配比是否异常
- 关键服务是否存在过低 limits 导致 OOM 风险
- 是否存在 BestEffort Pod
3）HPA / VPA
- HPA 是否存在
- 指标是否可获取
- 当前副本是否触发扩缩容
- 是否存在 HPA 指标异常
- HPA min/max 是否合理
- 是否频繁抖动扩缩容

---
2.10 网络与访问检查
1）集群内网络
- Pod 到 Pod 通信是否正常
- Pod 到 Service 通信是否正常
- 跨 namespace Service 访问是否正常
- DNS 解析是否正常
2）网络策略
- 是否存在 NetworkPolicy 阻断
- 关键链路是否被误拦截
- 是否有命名空间间通信策略异常
3）外部访问
- Ingress/LoadBalancer 暴露是否正常
- NodePort 是否可访问
- 目标端口是否监听
- TLS 握手是否正常

---
2.11 事件与日志异常检查
1）K8s Event 检查
- 最近事件中是否有 Warning
- 是否有频繁：
  - FailedScheduling
  - BackOff
  - Unhealthy
  - FailedMount
  - FailedAttachVolume
  - Evicted
  - OOMKilling
- 是否有同类异常集中爆发
2）关键组件日志
- 控制面日志错误
- CNI / DNS / Ingress Controller 错误
- 关键业务 Pod 错误日志摘要
- 是否存在连续异常关键字

---
2.12 风险预警类检查
这部分很重要，不仅看“已故障”，还看“快故障”。
- Pod 重启次数持续上升
- 节点内存接近满载
- 节点磁盘接近满载
- PVC 容量接近满
- 证书剩余有效期不足
- namespace quota 即将耗尽
- endpoint 数量突然下降
- Pending Pod 数量增加
- HPA 无法取指标
- API Server 请求失败率升高
- etcd 空间逼近阈值
- 某些核心服务副本降为 1
- 核心组件使用 latest tag
- 关键 Pod 无 PDB
- 关键工作负载无反亲和策略
- 单副本关键服务存在单点风险