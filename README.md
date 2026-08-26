# ATP ZeroMQ Proxy

基于用户提供的 ATP v6.3.15 demo 实现的独立多客户端接入服务。底层 API
使用 demo 中的 `CThostFtdcMdApi` / `CThostFtdcTraderApi` 兼容接口，上层通过
ZeroMQ 向交易引擎和策略提供行情、查询、下单及撤单能力。

> 项目名沿用业务侧的 `atp-proxy` 命名；ATP v6.3.15 SDK 的公开类名和
> Python 包名仍然是 `CThostFtdc*` / `openctp-ctp`，这是 SDK 本身的命名，
> 不是误接入另一个协议。

## 已实现能力

- 行情前置连接、登录、订阅、退订和深度行情推送。
- 交易前置连接、直接登录及结算确认。
- 查询账户、持仓和委托，带串行流控和短时快照缓存。
- 限价单报单、撤单、订单回报和成交回报。
- `client_id + strategy_id + client_order_id` 跨重启幂等。
- PostgreSQL 持久化本地订单 ID 与 ATP `FrontID / SessionID / OrderRef /
  ExchangeID / OrderSysID` 的映射。
- 多客户端、多策略行情订阅引用计数。
- Linux x86_64 / Windows 加载真实 API；macOS 可运行全部非柜台测试。

## 架构

```text
ATP MD/TD Front
       |
       v
   atp-proxy <------> PostgreSQL
     |    |
 PUB:5585 REP:5586
     |    |
     +----+------ trading engines / strategies
```

ATP 原生回调只写线程安全发布队列，ZeroMQ PUB socket 始终由固定发布线程
操作，避免跨线程共享 socket。

## 安装与运行

生产环境建议使用 Linux x86_64。依赖锁定为与 demo 匹配的
`openctp-ctp==6.3.15.7`。

```bash
cd atp-proxy
cp .env.example .env
# 填写 ATP 前置、账号和 PostgreSQL 密码
uv sync
uv run python src/main.py
```

macOS 不加载真实原生库，但可以执行测试：

```bash
uv sync
uv run pytest -q
```

Docker（Apple Silicon 会以 `linux/amd64` 运行）：

```bash
cp .env.example .env
docker compose up --build -d
docker compose logs -f atp-proxy
```

systemd：

```bash
uv sync
sudo scripts/install_systemd.sh
sudo systemctl enable --now atp-proxy.service
```

## 配置

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `ATP_BROKER_ID` | 经纪商代码 | 必填 |
| `ATP_USER_ID` | 用户/投资者账号 | 必填 |
| `ATP_PASSWORD` | 密码 | 必填 |
| `ATP_FRONT_MD` | 行情前置地址 | 必填 |
| `ATP_FRONT_TD` | 交易前置地址 | 必填 |
| `ATP_ENABLE_MD` | 是否创建、登录行情 API 并接受行情订阅；关闭时 `ATP_SYMBOLS` 必须为空 | `true` |
| `ATP_SYMBOLS` | 启动时订阅的合约，逗号分隔 | 空 |
| `ZMQ_PUB_PORT` | 事件发布端口 | `5585` |
| `ZMQ_REP_PORT` | 命令端口 | `5586` |
| `ATP_QUERY_MIN_INTERVAL_SECONDS` | 查询最小间隔 | `1.0` |
| `ATP_QUERY_TIMEOUT_SECONDS` | 查询超时 | `10.0` |
| `ATP_SNAPSHOT_TTL_SECONDS` | 查询缓存有效期 | `5.0` |
| `DATABASE_URL` | PostgreSQL 连接 URL | 必填 |
| `ATP_FLOW_PATH` | 原生 API 流文件目录 | `flow` |

真实 `.env` 不应提交到版本库。当前 ZeroMQ 接口没有身份认证，生产环境必须
通过私有网络或防火墙限制端口，尤其是 REP 命令端口。

## PUB topics

```text
marketdata.ATP.<symbol>
orders.<account_id>
orders.<account_id>.<strategy_id>
trades.<account_id>
trades.<account_id>.<strategy_id>
account.<account_id>
positions.<account_id>
status.ATP
errors.ATP
```

消息由 topic 和 JSON 两个 frame 组成：

```json
{
  "schema_version": 1,
  "event": "marketdata",
  "published_at": 1784512345678,
  "data": {}
}
```

## REP commands

健康检查：

```json
{"action":"ping","request_id":"health-1"}
```

行情订阅和退订：

```json
{"action":"subscribe_market_data","client_id":"engine-01","strategy_id":"arb","symbols":["CU3M-LME"]}
{"action":"unsubscribe_market_data","client_id":"engine-01","strategy_id":"arb","symbols":["CU3M-LME"]}
```

账户、持仓、委托和持久化成交查询：

```json
{"action":"get_account","max_age_ms":5000}
{"action":"get_positions","force_refresh":true}
{"action":"get_orders","max_age_ms":5000}
{"action":"get_orders","local_only":true,"strategy_only":true,"strategy_id":"arb"}
{"action":"get_trades","client_id":"engine-01","strategy_id":"arb","after_id":0,"limit":500}
```

成交会在发送 PUB 事件前写入 PostgreSQL。`get_trades` 按 owner 隔离并使用
`next_after_id`、`has_more` 分页，供 Engine 启动和重连时恢复漏收成交。

下单：

```json
{
  "action":"place_order",
  "request_id":"request-123",
  "client_id":"engine-01",
  "strategy_id":"arb",
  "client_order_id":"arb-20260730-000001",
  "symbol":"CU3M-LME",
  "exchange":"LME",
  "direction":"BUY",
  "offset":"OPEN",
  "price":3900.0,
  "volume":1
}
```

撤单：

```json
{"action":"cancel_order","client_id":"engine-01","strategy_id":"arb","client_order_id":"arb-20260730-000001"}
```

快速发送命令或订阅行情：

```bash
uv run python src/command_example.py '{"action":"ping"}'
uv run python src/consumer_example.py
```

## v6.3.15 兼容说明

- 交易连接在前置连接成功后直接执行 `ReqUserLogin`，登录后再做结算确认。
- v6.3.15 的 `CreateFtdcMdApi` 第二个参数是 UDP 标志，不是新版接口中的
  production mode。本项目只传 flow path，避免误启用 UDP。
- SDK 原生二进制仅提供 Windows 和 Linux x86_64；macOS 不能连接真实柜台。
- `PUB/SUB` 不补发离线事件。客户端重连后应主动查询账户、持仓和委托恢复
  快照。
