# 注册结果核验（2026-11-03）

本轮已实现逐 Section 状态、提交与核验分离、Worker 共用判定、会话检查和事件日志。
**真实个人注册记录读取器尚未接入，因此不能声称已经完成生产环境中的自动成功确认。**

## 已确认的证据与集成边界

- 仓库已有 `RegisterSections`、`CompleteRegistration` 和 `SectionDetails` 请求；
  `SectionDetails` 的总人数/余量不是个人选课记录，不能用于证明注册成功。
- 2026-11-03 仅对已有 DegreePlans 页面做 GET 检查。本地 `session.json` 会话返回
  302，指向 `/Student/Account/Login`。没有执行真实注册、退课、换课或候补请求。
- 仓库没有包含真实个人注册记录的 HAR、响应 fixture 或网页脚本。
  没有猜测新的 Ellucian API URL、请求方法或响应字段。
- `registration_state.RegistrationReader.read(client, section_ids)` 是集成边界。
  默认 `registration_reader=None` 返回 `reader_not_configured`；界面明确提示手动核对课表。
  测试中的 reader 和记录是**合成 fixture，不是 Ellucian 抓包数据**。
- 移除了 Add/Waitlist 的 `Credits=3`，现在省略该字段，避免擅自修改固定学分。
  **省略字段是否符合真实网页请求尚未确认**；可变学分选择没有实现。
  Drop 保留原来的 `Credits: null`。

## 状态与重试行为

| 状态 | 证据与行为 |
| --- | --- |
| `REGISTERED` | 只由个人注册记录确认；当前 client 生命周期内不再 Add |
| `NOT_REGISTERED` | reader 明确确认该 Section 未注册；允许冷却后重试 |
| `REJECTED` | 明确关联到该 Section 的拒绝通知；不等于已经核实其不在课表中 |
| `UNKNOWN` | 超时、异常响应、读取失败、缺少记录或未接入 reader |
| `WAITLISTED` | 个人记录确认候补；不显示为注册成功，也不继续 Add |

批量通知只能通过明确的 SectionId、消息中的准确 ID 或单目标请求关联。
不能把课程名称形式的批量报错随意归给某个 Section。一条 Duplicate/Already Registered
永远不能证明整批成功；Closed 也不能证明某门课尚未注册。账户记录优先于提交报错。

首次 Add 前没有额外联网预检。发送后立即尝试只读核验；后续轮次先核验未知结果，
包括 Auto-Catch 中“课程已满”的情况。读取失败保留已有确认结果；未确认目标暂停重复 Add。
只有 reader 明确确认未注册，并且距上次提交响应或超时至少 5 秒，才允许重新提交。
未知状态按 2、4、8、16、30 秒退避，上限 30 秒，并尊重 Worker 更长的用户间隔。
这不能提供服务器端 exactly-once 保证，也不能排除服务器读副本延迟。

没有 reader 时，首次提交后会停留在核验轮次，不会持续重复 Add，也不会自动报成功。
这包括空通知响应和明确拒绝但尚未核对个人记录的请求；这是当前集成缺口的实际影响。
用户仍可以停止任务并在官方课表确认结果。

Waitlist 仅用于已明确 Closed、且个人记录确认未选的目标；候补不是注册成功。
换课请求的 Drop 不会自动重放；必须确认退课目标不在实际注册记录中，才能报告整次换课成功。
已有确认结果只保存在当前 client 内存中，不从历史日志恢复；重启后不会把旧学期/旧会话结果当作当前事实。
手动在官方页面改变课表后，应重新认证以建立新的 client。

## Session Check

按钮改为 Session Check，读取已有 DegreePlans 页面，禁止跟随重定向，以免把 Token 发送到 SSO。

- `VALID`：200 页面同时具有退出登录控件和防伪 Token 控件。
- `EXPIRED`：401、登录/SSO 重定向、明确的密码输入框或 SAML 登录表单。
- `UNKNOWN`：超时、429/5xx、403、无法识别的 200 页面。403 也可能是权限或 CSRF 问题，不能直接归为会话过期。

认证成功页面的 HTML 标记仍需真实有效会话验证；无法识别时保留 UNKNOWN。
三个 Worker 都使用相同的过期判定和恢复函数，每次 Worker 运行最多尝试一次自动恢复。
恢复必须是同一个学生账户，并通过 Session Check；不能确认恢复就停止并提示重新登录。
不会把登录页返回 200 作为成功，也不会让 CompleteRegistration 吞掉会话过期异常。

## 日志

默认目录：`~/.keanseatscatcher/logs/`，Windows 为 `%USERPROFILE%\.keanseatscatcher\logs\`。
可设置 `KSC_LOG_DIR`；Docker Compose 已映射到宿主机 `./logs`，且不会在任务结束后自动重启并重放注册。
每个 API client 生成一个 `registration-<run_id>.jsonl` 文件，界面首次提交时显示完整路径。

每条事件使用 UTC ISO 8601 毫秒时间戳，包含 run/round/request ID。
记录轮次起止、请求起止、实际收到响应的时间（未收到则为 null）、单调时钟耗时、
HTTP 状态、超时异常类型、各课程状态和等待核验标记。每条事件单独追加、flush、关闭文件，
因此正常进程退出前不需要统一导出。进程被强制杀死时未配对的 request_started 可识别未完成请求；
不保证操作系统断电时所有文件缓存都已落盘。

- `first_confirmed_at`：客户端首次从 reader 确认注册的时间。
- `server_registered_at`：仅接受真实记录明确提供的服务端注册时间；未提供时为 null。
- 不记录 Cookie、Token、HTTP 头、请求/响应正文、登录 URL 参数或原始异常字符串。
  拒绝原因保存为 `closed`、`credits_rejected` 等代码，避免服务端文本夹带凭证。
- 磁盘写入失败会明确提示；不把日志失败或网络失败伪装为注册成功。

## 接入真实 reader 前必须确认

1. 在有效登录账户中，只观察官方页面读取个人实际注册记录的请求；不要点击注册/退课来测试。
2. 确认请求 URL、方法、学生身份、学期范围、分页/完整性及注册/候补/计划课程的区别。
3. 保存去除身份与凭证的最小响应 fixture，再实现 `RegistrationReader`，通过构造参数注入 client。
   网络调用使用 `client._request` 以复用重定向检测和脱敏日志。
4. 返回 `RegistrationSnapshot(records=..., reason=...)`；每个条目是 `RegistrationRecord`。
   仅完整、权威且覆盖目标学期的查询才可将缺失 Section 映射成 NOT_REGISTERED；
   部分页、未知状态或解析失败应返回 UNKNOWN/缺失条目。
5. 核对 Add 的学分字段、CompleteRegistration 的真实响应，以及有效登录页识别条件。
   不为此更改用户现有课表；提交异常继续使用 Mock 验证。

## 验证

运行 `python -m pytest -q`（开发环境需要 pytest 和现有 GUI 依赖）。测试默认禁止真实 HTTP，
覆盖超时但实际成功、六门课混合结果、Duplicate、Closed 与已有选课、登录重定向/拥堵、
CompleteRegistration 超时、日志完整性/脱敏/磁盘失败、各 Worker 恢复与停止行为。
UI 另以 Qt offscreen 模式检查主窗口可以正常构造，不触发真实注册。
