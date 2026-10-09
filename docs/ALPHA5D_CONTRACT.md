# V8.0 五日强势Top5：执行与验收合同

配置升级日期：2026-10-06。目标是预测未来5个A股交易日累计涨幅领先的5只股票，不是预测5只防御股，也不是提高一句“NO TRADE”的正确率。

## 2026-10-09 生产接入修复：先读新的实际执行回执

用户要求修复反复NO RANK的问题。新增的生产代码是scripts/alpha5d_pipeline.py、scripts/alpha5d_source_fixes.py和.github/workflows/alpha5d-data.yml。原排序权重保持V8.0-bootstrap-1，不将数据工程修复说成预测准确率提升。

每次双报读取本合同后，先读取data/alpha5d/pipeline_status.json，再读取其指向的data/alpha5d/runs/<真实运行标识>/receipt.json与ranking.json；不要只读取旧data/universe_summary.json而漏掉新流水线的结果。完整原始输入、沪深分页、行业来源与错误记录在回执指向的GitHub Actions原始证据artifact中，artifact名称和workflow_run_id均应为真实值。摘要和status标签不能代替原始数据验收。

数据准备工作流计划在北京时间15:25和08:05运行，仅为既有双报提供公开市场数据缓存，不是额外用户晨报。原用户任务仍08:35启动、08:50截止、09:00交付目标；本修复不改变它的schedule、timing_mode或启用状态，不连接券商、不下单、不写私人持仓。

适配器独立取得完整沪深A股票池、历史日K和成交额、行业归属、20只跨板块腾讯与独立日K样本，实际调用排序器。失败也保存原因；不能把GitHub作业完成或缓存存在称为行情已通过。数据错误不等于合法排除；新股、停牌等排除需独立依据。所有采集和排序实际证券日期必须匹配previous_session。

行业来源为新浪行业分类，不得冒充申万分类。同一行业页面重复成员仅在代码和名称一致时去重并记录原始重复数；全沪深股票池仍要求分页无缺页且重复清楚对账。同一股票有多个行业归属时保留全部候选归属，对最多16种组合穷举运行原模型；只有所有组合下前五名及顺序完全相同时才通过，并展示分数范围。超过组合预算或前五改变即需解决行业归属，不静默挑对排名有利的分类。

ranking_health=COMPLETE表示输入检查及技术排序通过；publication_state=AWAITING_EVIDENCE_REVIEW表示尚需读取真实前20、完成行业/消息时间/事件证据复核。它不是永久NO RANK开关：当数据和外部证据门槛都通过，应直接完成研究卡、正式五只冻结和交付；即便建议WAIT/NO TRADE，五只仍必须接受研究考核。没有独立证据不擅自加事件分或手工换榜。

人工盘中修复运行必须明确run_kind=late_research，使用真实生成时间，不回填成08:50。其截止线只是为当日补跑使用的实际公开信息时点，不授权任何未来数据。适配层只允许在真实同日、08:50后且生成时间不晚于当前时钟的情况下处理原执行器固定08:50的时钟检查；其他价格、分页、历史、来源、行业、样本和上市资格门槛不放松，评分公式不改变。

迟到的正式研究批次与原08:50批次分开归档：使用data/alpha5d/cohorts/<date>-late-<HHMMSS>.json，只创建，不覆盖原预测。披露D1已部分经过、原前收至D5的研究口径包含此前已发生涨跌；必须另外记录真实发布时间的可核参考价格和发布后至D5的表现，不能把发布前收益算成预测功劳。若生产排序尚未通过，则具体错误保留，不能在报告中把INCOMPLETE改成COMPLETE。

## 已实现与尚未验收

- scripts/alpha5d_engine.py：独立的、可复现的全输入截面排序器与五日成绩核验器；不读取个人持仓，不连接券商，不下单。
- scripts/test_alpha5d_engine.py：13项合成数据单元测试。本次本地13/13通过。合成证券、日期和行情只是软件测试，不是历史回测，更不是实盘收益。
- 模型状态为 HEURISTIC_NOT_TRAINED_OR_VALIDATED。未训练机器学习模型，未取得滚动样本外优于基准的证据。
- 全量历史适配已新增代码并启动真实生产试跑，但每一期完整生产排序以及成熟五日成绩仍以实际产物验收。未通过的运行保持失败状态，不把已存在的universe快照冒充验收，不把字段缺失股票悄悄排除。
- 原market_relay.py的deep_candidates是个人持仓风险排序，不是全A选股，禁止作为本系统候选池。
- 原市场中继和私人持仓文件未改；新增独立公开研究数据准备流程。没有另外开启付费服务或声称存在已部署的后台训练。

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
python scripts/alpha5d_source_fixes.py --output .cache/alpha5d --workers 8
```

排序器自身不承担供应商采集；新增适配器负责取得真实输入，盘前任务须读取并复核其实际产物。无法取得则明确E_HISTORY_INPUT_NOT_READY/INPUT_INCOMPLETE，不生成伪正式Top5。原始输入和全排序应归档或分片并记录hash，摘要不能代替执行产物。

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
