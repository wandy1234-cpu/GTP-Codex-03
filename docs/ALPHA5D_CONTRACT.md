# V8.0 五日强势Top5：执行与验收合同

配置升级日期：2026-10-06。目标是预测未来5个A股交易日累计涨幅领先的5只股票，不是预测5只防御股，也不是提高一句“NO TRADE”的正确率。

## 已实现与尚未验收

- scripts/alpha5d_engine.py：独立的、可复现的全输入截面排序器与五日成绩核验器；不读取个人持仓，不连接券商，不下单。
- scripts/test_alpha5d_engine.py：13项合成数据单元测试。本次本地13/13通过。合成证券、日期和行情只是软件测试，不是历史回测，更不是实盘收益。
- 模型状态为 HEURISTIC_NOT_TRAINED_OR_VALIDATED。未训练机器学习模型，未取得滚动样本外优于基准的证据。
- 尚未验收：全部合格沪深A股至少61日历史K线/行业字段的真实输入适配、完整生产排序以及成熟五日成绩。不把已存在的 universe 快照冒充这项验收，不把字段缺失股票悄悄排除。
- 原 market_relay.py 的 deep_candidates 是个人持仓风险排序，不是全A选股，禁止作为本系统候选池。
- 未改动原市场中继的工作流、定时频率或私人持仓文件；执行器由升级后的盘前任务调用。没有另外开启付费服务或声称存在已部署的后台训练。

## 目标与口径

预测日为D1，之后第5个有效交易日为D5；官方交易日历确认，不按5个自然日，也不是在D1之后额外多算5天。

选股成绩：上一有效交易日收盘总回报基准至D5收盘的累计收益，用一致的除权除息/公司行动处理。记录真实沪深全A冠军与合格可研究宇宙冠军两种对照，不隐瞒流动性等过滤造成的差异。

成交成绩：D1实际可成交价格（或事先规定并单列的开盘模拟价）至D5收盘，扣成本；另列开盘跳空、限价板不可买、T+1约束、滑点、信号未触发。未经核验的成交必须UNVERIFIED。

NO TRADE/WAIT只属于成交账，不从选股账中删除。没有实际成交不能说已避免损失。D1表现仅诊断，D5成熟后才结案。

## rank(data) 输入

JSON顶层：forecast_date、previous_session（YYYY-MM-DD）、cutoff（预测日08:50:00+08:00）、data_commit、source_urls、coverage、universe。

coverage.sh_a / sz_a：successful_pages（从1至terminal_page连续）、missing_pages、terminal_page、terminal_verified、raw_rows。unique_count应与universe原始行数一致；本版对未对账重复行保守报错。至少20个不同代码samples，每项含symbol、board（sh_main/star/sz_main/chinext）、security_date、sina_close、tencent_close、daily_close、identity_matches、source_urls；分层还需调用方核查市值/涨跌方向。样本与输入raw_close也应一致。标签不是证据，调用方必须读取原始数值、分页清单和原始链接。

universe保留整个原始股票宇宙，每行：symbol、name、eligible、sector、asof_date、available_at、source_urls、amount_unit='CNY'、raw_close、adjustment_basis、bars。

bars至少61根截至previous_session的日线，字段date/open/high/low/close/volume/amount，日期唯一升序；全部价格使用同一、当时可得的复权基准，成交额为真实人民币，成交量口径一致。历史停牌缺少记录应通过供应商官方停牌信息处理，不捏造日线。不能从分钟单价重造OHLC或按现价猜成交量倍率。

明确的事前剔除才可eligible=false，exclude_reason限：non_sh_sz_a、st、known_suspension、listing_lt_60_sessions、liquidity_lt_50m；须附exclusion_evidence。数据没拉到不是合法剔除理由。61根历史不足但上市超过60个交易日属于输入缺失；新股实际少于61根本版必须报告不足，不能补造。20日成交额中位数低于5000万元由代码过滤；该门槛是未优化初始规则，须披露。

可选catalyst_points、earnings_points各0至5。正分必须同时提供<key>_evidence与<key>_public_at，不晚于cutoff。0只表示未授予验证加分，不意味着已扫描全市场所有公告或确定没有事件。

事件加分统一量表：0=无已验证新增；1=原文存在但收入映射弱；3=直接业务/订单/临床/政策适用有证据；5=金额/进度/盈利或产业范围对比原预期存在可核验重要变化。只用0/1/3/5，说明证据，不用情绪热度冒充基本面变化。业绩加分同理，必须区分绝对增长与相对一致预期；没有可核预期不得称超预期。

## 初始排序（不是概率，不是已优化模型）

30分多周期动量（5/10/20日分位按50/30/20组合）+15分60日收盘突破+10分收盘位置+15分行业五日收益中位数与MA10宽度+10分5/20日成交量比+10分MA10斜率+5分新增催化+5分业绩预期差。

仅在同时出现MA10过度偏离和弱收盘位置时加0至15分惩罚；不因昨日涨停、20cm或已经上涨直接否决。暂不设置行业分散名额，不为凑五个行业替换更强股票；集中风险另行披露。

该模型是可反驳的价格/板块基线，不等于已具备捕捉未来冠军的能力。未来买家/卖压仍需证据卡，不将“机构会买”当事实。全量运行输出ranking、top5、eligible_codes、baselines.momentum20_top5、每股特征、输入hash与数据commit。COMPLETE只代表本执行器检查与排序完成；仍需外部Sector/消息时点/交易日/来源真实性门槛。

## 调用

```bash
python scripts/alpha5d_engine.py rank <verified-input.json> <new-ranking.json>
python scripts/alpha5d_engine.py evaluate <frozen-cohort.json> <new-evaluation.json> --outcomes <verified-outcomes.json>
PYTHONPATH=scripts python -m unittest -v scripts/test_alpha5d_engine.py
```

程序不自带全A供应商适配，不能假装输入已经存在。盘前任务需从Data Route V2.6取得并核验真实完整输入，在真实Python环境执行；无法取得则明确E_HISTORY_INPUT_NOT_READY/INPUT_INCOMPLETE，不生成伪正式Top5。原始输入和全排序应归档或分片并记录hash，摘要不能代替执行产物。

## 不可改写的两本账

- data/alpha5d/cohorts/YYYY-MM-DD.json：当日首次正式候选，create-only。至少包含forecast_date、previous_session、官方五日sessions、cutoff、真实generated_at/delivered_at（未知明确标记）、model_version、data_commit/input_hash、完整eligible_codes、rank1..5代码/参考价/分数/证据/条件、冻结基准。每日可更新新一批，不改写老批；继续入选仍保留每个批次独立跟踪。
- data/alpha5d/evaluations/YYYY-MM-DD.json：成熟后的选股成绩，另文件，不覆写原预测。
- execution另存，只有实际可核触发/成交才可填；NO TRADE不免除选股成绩。
- 历史V7聊天能找到原始冻结输出才迁入历史账，标记实际来源；不能把今天回放当成当日盘前生成。

## evaluate(cohort,outcomes) 输入与成绩

cohort需包含rank输出中的top5/eligible_codes/baselines以及forecast_date、sessions。outcomes需包含同样forecast_date/sessions、observed_at（D5 15:00之后）、source_urls、basis='total_return_previous_close_to_fifth_close'、prices。prices按代码给reference/final，为一致总回报基准水平；不是混用两种复权价格。单调缩放不影响收益比。停牌/退市/公司行动必须保留并核实，缺失不能删除亏损样本。

成绩同时报告：五只等权平均和中位数五日收益；真正涨幅前5命中只数；真正前20捕获只数；五只中进入合格宇宙前5%数量/比例；每只实际涨幅排名；对等权宇宙及同日20日动量Top5基准。前5%是辅助稳定指标，不冒充“全市场真正前5名命中率”。遇涨幅并列按代码排序只作确定性标号，解释边界并列。

完整宇宙结果缺失时，全市场排名指标INCOMPLETE；任何候选结果缺失则候选均值不计算，不缩小分母。执行收益、MAE/MFE和基准ETF收益需另行从真实价格路径计算，本执行器没有就不得声称已计算。

## 真正的Learning Loop

每天评估最新成熟批次，反查实际前20赢家：是否初筛漏掉、行业漏掉、排名错了、错误怕高、执行不可买，或预测后才突发新事件。另报告重叠榜单与换股率，不能悄悄换人来提升成绩。

至少积累20个成熟日批次再进行第一次诊断；这只是管理检查点，不是统计证明。按时间滚动样本外比较，按交易日分组、清除跨训练/验证边界的五日标签重叠，不能随机拆证券行；重叠日批次不是独立样本。保持独立测试段，记录尝试过的所有版本，用日期块评估不确定性。

基准先行：复杂新模型若没有在同宇宙、同时点、同成本口径下稳定胜过简单20日动量Top5，不允许宣称提升或替换主模型。禁止一天输赢后改权重。可以研发学习排序模型，但只有训练产物、数据覆盖、滚动样本外和上线前影子验证完整才称已训练、有效或自学习。
