MinIO 专项健康检查项
5.1 MinIO 实例与集群状态
- MinIO Pod 是否 Running / Ready
- 副本/实例数是否达标
- MinIO 集群状态是否正常
- 是否有节点离线
- 分布式模式下磁盘数是否满足纠删码要求
- 是否存在 quorum 风险
5.2 存储层状态
- 挂载磁盘是否正常
- 磁盘是否只读
- 磁盘空间使用率是否过高
- inode 是否紧张
- 单盘故障是否影响可用性
- 存储延迟是否异常
5.3 Bucket 与对象服务能力检查
- 核心 bucket 是否存在
- bucket policy 是否正确
- 对象上传是否成功
- 对象下载是否成功
- 删除是否正常
- 列表操作是否正常
- presigned URL 是否可用
5.4 管理与认证检查
- 管理接口是否正常
- Access Key / Secret Key 是否有效
- 用户/策略是否完整
- 外部 IAM / OIDC 对接是否正常
5.5 数据保护与后台任务
- versioning 是否符合预期
- lifecycle policy 是否正常
- replication 是否正常
- ILM/过期清理是否正常
- healing/self-heal 任务是否异常
- 后台扫描是否报错
5.6 MinIO 性能与告警
- PUT/GET 延迟是否异常
- 5xx 错误率是否升高
- network throughput 是否异常
- 磁盘使用率接近阈值
- 某节点离线导致降级
- quorum 即将不足
- bucket 数量或对象数过大导致压力