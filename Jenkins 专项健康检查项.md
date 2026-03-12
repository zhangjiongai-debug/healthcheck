Jenkins 专项健康检查项
1 Jenkins 控制器状态
- Jenkins Pod/实例是否 Running / Ready
- Web UI 是否可访问
- 登录页是否正常
- /login / /api/json 是否可访问
- Pod 是否频繁重启
2 Jenkins 初始化与配置状态
- Jenkins 是否完成启动
- init script / plugin 加载是否成功
- 配置是否被正确加载（JCasC 若启用）
- 系统日志中是否有关键错误
- 是否处于安全锁定/初始化未完成状态
3 插件健康检查
- 核心插件是否安装
- 插件是否加载失败
- 插件版本是否冲突
- 插件依赖是否缺失
- 是否存在高危过旧插件
4 Agent / Executor 状态
- Jenkins Agent 是否在线
- K8s 动态 Agent 是否可创建
- Label 是否匹配
- executor 数量是否足够
- 是否存在离线 agent
- agent 连接是否频繁断开
5 Job / Pipeline 检查
- 最近构建是否成功
- 失败率是否升高
- 是否存在长期卡住的构建
- 构建队列是否堆积
- pipeline stage 是否异常中断
- SCM 拉取是否成功
- 构建产物上传是否正常
6 Jenkins 依赖检查
- Jenkins Home PVC 是否正常挂载
- 磁盘容量是否充足
- 对 GitLab/Git 仓库访问是否正常
- 对制品库/对象存储访问是否正常
- 邮件/通知通道是否正常
7 Jenkins 性能与风险预警
- 堆内存使用率过高
- GC 频繁
- 构建队列积压
- executor 利用率过高
- Jenkins Home 接近满盘
- 插件冲突风险
- 控制器单点风险
- agent 全部离线风险