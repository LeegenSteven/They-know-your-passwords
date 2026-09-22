# 算法服务接口契约

## 传输和生命周期

KeePassXC 以 `python.exe -u server.py` 启动本地常驻进程，通过 stdin/stdout 交换 JSON Lines。每行是独立 UTF-8 JSON 对象，以 `id` 关联异步响应。stdout 不允许出现诊断信息；stderr 不得包含请求参数或口令明文。

服务启动后在主线程预热模型，同时由独立协议读取线程响应 `ping` 和 `list_models`；就绪后，推理进入单工作线程串行执行。推理请求超时后返回 `TIMEOUT`；底层推理不能安全中断，因此工作线程会完成当前调用后继续处理下一请求。

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

### `assess_trawling`

参数为 `candidate` 和可选 `model_id`、`options`。默认使用 RankGuess。候选必须为长度 5–20 的 ASCII 32–126 字符串。

成功结果的 `native` 包含 `guess_number`、`probability`、`table_size`、`clamped_low` 和 `table_capped`。`table_capped=true` 表示猜测次数是参考表提供的下界。

### `assess_reuse`

参数为 `candidate`、`history[]` 和可选 `model_id`、`options`。默认使用 PARD。候选和可用历史必须为长度 1–20 的 ASCII 32–126 字符串；历史非空但全部越域时返回 `OUT_OF_DOMAIN/UNUSABLE_HISTORY`。

成功结果的 `native` 包含 `exact_match`、`in_top_k`、`best_rank`、`best_source_index`、`top_k`、`candidate_logprob`、`usable_sources` 和 `skipped_sources`。`best_source_index` 仅指请求数组位置，任何界面均不得显示源口令或推导变换。

### `generate_candidates`

参数为 `constraints`、`count` 和可选 `model_id`。默认生成器位于 KeePassXC 宿主；请求该模型时服务返回 `UNAVAILABLE/HOST_GENERATOR_REQUIRED`。未来仅有显式注册并声明 `GENERATE_CANDIDATES` 能力的本地适配器可以处理该方法。

### `shutdown`

返回 `OK` 后停止接收请求，等待当前工作线程结束并释放模型。

## 模型选择和错误

未知模型返回 `UNAVAILABLE/MODEL_NOT_FOUND`；能力不匹配返回 `ERROR/CAPABILITY_MISMATCH`；缺少历史返回 `ERROR/MISSING_CONTEXT`；非法模型输出返回 `ERROR/INVALID_MODEL_OUTPUT`。明确指定模型失败时不得静默切换。

两类算法的原生结果不相加、不平均、不换算。等级映射由宿主依据模型、版本和推理配置分别执行；没有匹配标定时为 `UNKNOWN`。
