PostgreSQL 专项健康检查项
6.1 PostgreSQL 实例基础状态
- PostgreSQL Pod/实例是否正常
- 主库是否可写
- 只读副本是否可读
- 进程是否存活
- readiness/liveness 是否正常
6.2 连接与认证
- 数据库端口是否可达
- 用户认证是否成功
- 关键业务库连接是否正常
- 连接数是否接近上限
- 是否存在大量 idle in transaction
- 连接池（若有 PgBouncer）是否正常
6.3 主从复制/高可用
- 是否存在主库
- 从库是否全部在线
- replication lag 是否超阈值
- wal sender / receiver 是否正常
- failover 状态是否正常
- Patroni/repmgr/Operator 状态是否正常
- 是否出现 split brain 风险
6.4 存储与 WAL
- 数据目录空间是否充足
- WAL 目录空间是否充足
- checkpoint 是否过于频繁
- archive 是否成功
- WAL 堆积是否异常
- 磁盘 I/O 延迟是否升高
6.5 数据库内部健康
- 数据库是否能执行简单 SQL
- 核心表是否可查询
- 锁等待是否严重
- deadlock 是否频繁
- 长事务是否存在
- autovacuum 是否正常
- bloating 是否严重
- 是否存在 invalid index
- 是否有 failed transaction 持续积累
6.6 备份与恢复能力
- 最近备份是否成功
- 最近一次 base backup 时间
- WAL 归档是否连续
- 恢复点目标是否满足
- 备份文件是否可访问
- 恢复演练状态（若有）是否正常
6.7 PostgreSQL 风险预警
- 磁盘即将写满
- 连接数逼近上限
- replication lag 持续升高
- autovacuum 落后
- 长事务阻塞 vacuum
- checkpoint 过密
- 锁冲突激增
- 备份失败
- 主从角色异常漂移