APISIX 专项健康检查项
1 APISIX 核心组件状态
- APISIX Pod 是否 Running / Ready
- APISIX Dashboard 是否正常
- APISIX Ingress Controller 是否正常
- 副本数是否达标
- Pod 是否频繁重启
- 配置 reload 是否成功
2 控制面与数据面状态
- APISIX 数据面实例是否全部在线
- 控制器是否成功将 Ingress/CRD 同步到 APISIX
- etcd（若 APISIX 依赖 etcd）连接是否正常
- 配置下发延迟是否异常
- 是否存在配置不同步、部分实例未生效
3 Route / Upstream / Service / Consumer 检查
- 路由是否存在
- 路由配置是否合法
- Upstream 是否有健康节点
- Upstream 节点数是否满足预期
- 后端服务是否可达
- Consumer 配置是否完整
- 插件配置是否正确加载
4 APISIX 流量与错误检查
- 4xx 是否异常升高
- 5xx 是否异常升高
- upstream timeout 是否升高
- upstream connect failed/retry 是否增多
- 请求延迟 P95/P99 是否异常
- 是否存在大量 503/502/504
5 插件专项检查
- auth 插件是否正常
- rate limit 插件是否误伤
- CORS 插件配置是否正确
- prometheus 插件是否暴露指标
- request/response rewrite 是否异常
- plugin metadata 是否缺失
6 证书与 TLS 检查
- SSL 证书是否存在
- 证书是否即将过期
- SNI 配置是否正确
- TLS 握手是否成功
- 双向 TLS 配置是否正常
7 APISIX 风险预警
- etcd 不可达风险
- Dashboard 可用但数据面未同步风险
- 核心路由后端 endpoint 为空
- 插件加载失败
- 配置发布不一致
- 网关实例仅单副本
- 证书剩余时间过短