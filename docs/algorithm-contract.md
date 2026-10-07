# 算法服务接口契约

## 传输和生命周期

KeePassXC 以 `python.exe -u server.py` 启动本地常驻进程，通过 stdin/stdout 交换 JSON Lines。每行是独立 UTF-8 JSON 对象，以 `id` 关联异步响应。stdout 不允许出现诊断信息；stderr 不得包含请求参数或口令明文。

服务启动后在主线程预热模型，同时由独立协议读取线程响应 `ping` 和 `list_models`；就绪后，推理进入单工作线程串行执行。推理请求超时后返回 `TIMEOUT`；底层推理不能安全中断，因此工作线程会完成当前调用后继续处理下一请求。

2026-10-07：协议保留原 stdout，避免适配器导入时的重定向吞掉就绪回复。完成与超时竞争只回复一次，取消尚未开始的过期任务，忽略迟到结果。宿主收到超时后重启子进程。冷启动最长 180 秒，普通推理预算 3 秒独立计算。

## 通用请求与状态

```json
{"id":"request-1","method":"assess_trawling","timeout_ms":3000,"params":{}}
```

`id` 是 1–128 字符的非空字符串。状态限定为：`OK`、`OUT_OF_DOMAIN`、`TIMEOUT`、`ERROR`、`UNAVAILABLE`、`LOADING`。错误响应包含稳定的 `error_code`；`detail` 不含输入明文。

## 方法

### `ping`

返回就绪状态、默认模型、模型能力和算法版本。加载期间返回 `LOADING`。

### `list_models`

返回已注册适配器的 `model_id`、`model_version`、`capabilities`、`input_domain`、`required_context`、`output_metrics`、`adapter_version` 和 `ready`。

模型就绪后还返回 `model_sha256` 和 `inference`：RankGuess 的 sample/batch/seed/参考设备/分数精度策略，以及 PARD 的 batch/beam/top-k/AMP/图边开关。不返回模型权重、输入明文或绝对模型路径。

### `assess_trawling`

参数为 `candidate` 和可选 `model_id`、`options`。默认使用 RankGuess。候选必须为长度 5–20 的 ASCII 32–126 字符串。

成功结果的 `native` 包含 `guess_number`、`probability`、`table_size`、`clamped_low` 和 `table_capped`。`table_capped=true` 表示猜测次数是参考表提供的下界。

新增 `precision_recovered`：上游 float32 指数下溢时，适配器复用同一模型原语，以 float64 累加并求指数恢复概率，原算法保持只读；仍无有效数值则 INVALID_MODEL_OUTPUT。

新增 `psm={band,lower_bound,upper_bound,evidence,calibrated}`，六档边界为 1、10³、10⁶、10⁹、10¹²、10¹⁵。普通区间证据 ESTIMATED_UNCALIBRATED；截断证据 REFERENCE_TABLE_LOWER_BOUND，band/upper_bound 为 null，仅显示下界，不直接归入第六档。两种结果均 calibrated=false。

### `assess_reuse`

参数为 `candidate`、`history[]` 和可选 `model_id`、`options`。默认使用 PARD。候选和可用历史必须为长度 1–20 的 ASCII 32–126 字符串；历史非空但全部越域时返回 `OUT_OF_DOMAIN/UNUSABLE_HISTORY`。

成功结果的 `native` 包含 `exact_match`、`in_top_k`、`best_rank`、`best_source_index`、`top_k`、`candidate_logprob`、`usable_sources` 和 `skipped_sources`。`best_source_index` 仅指请求数组位置，任何界面均不得显示源口令或推导变换。

新增 beam_width、search_budget={beam_width,top_k,sources} 和 evidence（EXACT_REUSE、SEARCH_HIT、SEARCH_MISS_UNKNOWN）。当前默认 beam=100/top-k=200/batch=1；未命中不等于低风险。已有 GLCA 效果集采用不同搜索配置，不能据此冻结本配置阈值。

### `generate_candidates`

参数为 `constraints`、`count` 和可选 `model_id`。默认生成器位于 KeePassXC 宿主；请求该模型时服务返回 `UNAVAILABLE/HOST_GENERATOR_REQUIRED`。未来仅有显式注册并声明 `GENERATE_CANDIDATES` 能力的本地适配器可以处理该方法。

### `shutdown`

返回 `OK` 后停止接收请求，等待当前工作线程结束并释放模型。

## 模型选择和错误

未知模型返回 `UNAVAILABLE/MODEL_NOT_FOUND`；能力不匹配返回 `ERROR/CAPABILITY_MISMATCH`；缺少历史返回 `ERROR/MISSING_CONTEXT`；非法模型输出返回 `ERROR/INVALID_MODEL_OUTPUT`。明确指定模型失败时不得静默切换。

两类算法的原生结果不相加、不平均、不换算。等级映射由宿主依据模型、版本和推理配置分别执行；没有匹配标定时为 `UNKNOWN`。

发行配置：服务在导入 torch 前默认 OMP_NUM_THREADS/MKL_NUM_THREADS=4；显式环境设置可覆盖。RankGuess 参考表身份新增 cpu_threads，新增 reference_cpu_threads（默认 1，范围 1–64），参考生成线程配置变化会重新生成缓存，不混用旧配置表。
所有错误响应显式携带 level=UNKNOWN、calibrated=false；TIMEOUT 不携带候选，不构成通过结论。
