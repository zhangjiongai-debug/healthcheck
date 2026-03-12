Keycloak 专项健康检查项
4.1 Keycloak 实例状态
- Pod 是否 Running / Ready
- 副本数是否达标
- 是否频繁重启
- 健康检查端点是否正常
- 管理控制台是否可访问
4.2 数据库连接状态
- Keycloak 到 PostgreSQL 是否连通
- 数据库连接池是否耗尽
- 是否存在连接失败、超时
- migration/schema 检查是否正常
- 启动日志中是否有 DB 初始化失败
4.3 Realm / Client / User Federation 基础配置检查
- 核心 realm 是否存在
- 核心 client 是否存在
- redirect URI 是否异常
- identity provider 是否可用
- LDAP/AD federation 是否可用
- 必要管理员账户是否存在
4.4 认证能力检查
- 登录页是否可访问
- Token 获取接口是否正常
- OIDC 发现文档是否正常
- JWKS endpoint 是否正常
- token 签发是否成功
- refresh token 是否正常
- logout 流程是否正常
4.5 Keycloak 集群/缓存检查
- 多副本实例 session 是否一致
- Infinispan/缓存是否正常
- 集群节点是否全部加入
- sticky session 依赖是否合理
- 节点间同步是否异常
4.6 证书与安全检查
- TLS 是否正常
- 证书是否即将过期
- 管理端口是否暴露过多
- 默认管理员密码是否未改
- 高危配置是否开启
4.7 Keycloak 性能与风险预警
- 登录失败率异常升高
- token 请求延迟升高
- DB 连接池使用率过高
- 内存占用持续升高
- Full GC 频繁
- 用户 federation 不可达
- realm 配置漂移
- session 数接近上限