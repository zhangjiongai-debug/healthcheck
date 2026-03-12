GitLab 专项健康检查项
GitLab 组件较多，建议拆成 Web、Sidekiq、Gitaly、Redis、PostgreSQL、Ingress、Runner 几部分。

---
1 GitLab 核心服务状态
- Webservice Pod 是否正常
- Sidekiq Pod 是否正常
- Toolbox Pod 是否正常
- Shell 是否正常
- Gitaly 是否正常
- 副本数是否满足
- Pod 是否频繁重启
2 GitLab 页面与 API 可用性
- GitLab Web 首页是否可访问
- 登录页是否正常
- API /users/sign_in 或基础接口是否正常
- 健康检查端点是否正常
- Nginx/Workhorse 是否正常
3 Gitaly / Repository Storage
- Gitaly 服务是否可用
- Git 仓库是否可读写
- 仓库存储挂载是否正常
- push / clone / fetch 是否正常
- Gitaly 与 Praefect（若有）通信是否正常
- repository storage 容量是否健康
4 Sidekiq / 后台任务
- Sidekiq 队列是否堆积
- 失败任务是否增多
- 任务处理延迟是否异常
- 邮件/通知/导入导出等后台任务是否正常
5 数据依赖检查
- GitLab 到 PostgreSQL 是否正常
- GitLab 到 Redis 是否正常
- GitLab 到对象存储（MinIO/S3）是否正常
- artifacts/uploads/packages/lfs 是否可访问
- 数据库 migration 是否完成
6 GitLab Runner 检查
- Runner 是否在线
- Runner 是否被 pause
- Runner 是否能正常拉取任务
- 最近 job 成功率是否正常
- executor（K8s/docker/shell）是否正常
- Runner token 是否失效
7 GitLab 功能面检查
- 仓库浏览是否正常
- 用户认证是否正常
- Pipeline 创建是否正常
- Job 日志是否正常
- 制品上传下载是否正常
- Container Registry（若启用）是否正常
- Package Registry（若启用）是否正常
8 GitLab 风险预警
- Sidekiq 队列持续积压
- Gitaly 存储接近上限
- PostgreSQL/Redis 连接异常
- Runner 全离线
- 对象存储不可达
- 大量 500/502/503
- migration 未完成
- 仓库存储单点风险
- TLS 证书即将过期