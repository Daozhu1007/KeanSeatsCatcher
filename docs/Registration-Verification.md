# 注册结果核验（2026-11-03）

已接入真实个人课表读取器，并在 GUI 及 CLI 的全部认证初始化路径启用。
首次 Add 前必须核验个人课表；无法确认状态时暂停提交。

## 已确认的证据与集成边界

- 仓库已有 `RegisterSections`、`CompleteRegistration` 和 `SectionDetails` 请求；
  `SectionDetails` 的总人数/余量不是个人选课记录，不能用于证明注册成功。
- 2026-11-03 Owner 在专用 Edge 窗口登录并打开 Spring 2027。通过浏览器网络事件实际观察到
  `GET /Student/Planning/DegreePlans/Current?studentId=<STUDENT_ID>`，HTTP 200，JSON 响应。
  页面没有为本次课表读取发送分页参数，也没有在切换到 Spring 2027 时加载另一个课表分页。
- `registration_reader.DegreePlanRegistrationReader` 使用同一 GET、`studentId` 查询参数及
  `client._request`。验证 `DegreePlan.PersonId` 与当前账户一致，从 `DegreePlan.Terms` 按
  明确的 `Code` 选学期；不使用 `TermToShow` 推断用户要核验的学期。
  实际响应的 `TermToShow` 为另一学期，而页面选择的是 `2027SP`。
- 本次 Spring 2027 对应 `2027SP`，与 Spring 2027 Wenzhou (`2027SPW`) 不同。
  GUI 认证页和 CLI `--term` 默认 `2027SP`，其他学期应显式修改；一个 client 固定一个学期。
- 原始响应、HTML、凭证和观察辅助脚本只保存在本地仓库外，不纳入 Git。
  `tests/test_registration_reader.py` 保存最小字段结构的合成 fixture：身份、Section、数量均为虚构。
  候补和带 Section 的计划课程测试是依据网页状态字段构造的变体，Owner 当时没有候补记录。
- 真实只读核验中，页面的已注册 Section 集合与 reader 结果逐项匹配，且待 Add 集合为空。
  HTTP Session Check 返回 VALID。验证过程限定 GET，不调用真实注册、退课、候补或完成注册请求。
- 移除了 Add/Waitlist 的 `Credits=3`，现在省略该字段，避免擅自修改固定学分。
  本次只读查看官方网页脚本，确认其发送 `Credits: plannedCourse.Credits()`。
  **现有省略字段行为仍未经过真实提交验证**；本轮不扩展学分选择，也不发送注册请求来验证。
  Drop 保留原来的 `Credits: null`；CompleteRegistration 的真实响应仍未观察。

## 字段与完整性

`DegreePlan.Terms[].PlannedCourses` 同时包含已注册课程、计划课程及其他状态，不能把整个数组
当作“尚未注册”。Reader 使用课程条目的状态标志和 `Section.Id` / `Section.TermId`：

- `HasRegisteredSection` 加 `IsPreregistered` 或 `IsInProgress`，并与
  `AcademicHistory.Section` 一致，确认 REGISTERED。
  网页脚本将 `IsPreregistered` 显示为 “Registered, but not started”。
- `IsWaitlisted` 确认 WAITLISTED；Section 中的 `Waitlisted` 是总人数，不能证明本人候补。
- `IsPlanned` 确认 PLANNED；只有课程、没有 Section 的计划项不对应任何目标 Section。
- `IsDropped` / `IsWithdrawn` 对应已离开课表；未知、矛盾或缺失状态保持 UNKNOWN。

本次实际页面使用完整 `Terms[].PlannedCourses` 渲染当前课表，页面注册标记与响应逐项匹配。
缺失 Section 只有在目标学期唯一、`IsAcademicHistoryAvailable=true`、学期未完成、无 Notifications、
记录可解析且 `ActiveSectionIds` 全部被覆盖时才映射为 NOT_REGISTERED。其他学期出现的目标
不会借用本学期的缺失记录来允许 Add。部分数据既不推断缺失项未注册，也不凭计划状态允许 Add。
未知状态和读取失败保留已确认注册；不把登录 HTML、缓存页面或人数变化作为个人课表证据。

## 状态与重试行为

| 状态 | 证据与行为 |
| --- | --- |
| `REGISTERED` | 只由个人注册记录确认；当前 client 生命周期内不再 Add |
| `PLANNED` | 个人记录确认仅在计划中，尚未注册；满足提交条件时可 Add |
| `NOT_REGISTERED` | reader 明确确认该 Section 未注册；允许冷却后重试 |
| `REJECTED` | 明确关联到该 Section 的拒绝通知；不等于已经核实其不在课表中 |
| `UNKNOWN` | 超时、异常响应、读取失败、缺少记录或未接入 reader |
| `WAITLISTED` | 个人记录确认候补；不显示为注册成功，也不继续 Add |

批量通知只能通过明确的 SectionId、消息中的准确 ID 或单目标请求关联。
不能把课程名称形式的批量报错随意归给某个 Section。一条 Duplicate/Already Registered
永远不能证明整批成功；Closed 也不能证明某门课尚未注册。账户记录优先于提交报错。

首次 Add 前先读取个人课表，已注册/候补目标不发送 Add。发送后立即尝试只读核验；后续轮次先核验未知结果，
包括 Auto-Catch 中“课程已满”的情况。读取失败保留已有确认结果；未确认目标暂停重复 Add。
只有 reader 明确确认未注册或仅计划，并且距上次提交响应或超时至少 5 秒，才允许重新提交。
Auto-Catch/CLI 的核验轮次本身不会提交；核验后必须重新检查余量，仅提交本轮有空位的 Section。
未知状态按 2、4、8、16、30 秒退避，上限 30 秒，并尊重 Worker 更长的用户间隔。
这不能提供服务器端 exactly-once 保证，也不能排除服务器读副本延迟。

没有 reader 时，首次提交也暂停，保持 UNKNOWN。空通知响应和明确拒绝均不能代替个人记录核验。

Waitlist 仅用于已明确 Closed、且个人记录确认未选的目标；候补不是注册成功。
换课请求的 Drop 不会自动重放；必须确认退课目标不在实际注册记录中，才能报告整次换课成功。
已有确认结果只保存在当前 client 内存中，不从历史日志恢复；重启后不会把旧学期/旧会话结果当作当前事实。
手动在官方页面改变课表后，应重新认证以建立新的 client。

## Session Check

按钮改为 Session Check，读取已有 DegreePlans 页面，禁止跟随重定向，以免把 Token 发送到 SSO。

- `VALID`：200 页面同时具有退出登录控件和防伪 Token 控件。
- `EXPIRED`：401、登录/SSO 重定向、明确的密码输入框或 SAML 登录表单。
- `UNKNOWN`：超时、429/5xx、403、无法识别的 200 页面。403 也可能是权限或 CSRF 问题，不能直接归为会话过期。

本次在真实有效登录页面及独立 HTTP GET 中确认：`a[href="/Student/Account/LogOff"]`
与 `input[name="__RequestVerificationToken"]` 同时存在；Session Check 返回 VALID。
这确认了当前部署的标记，未来无法识别的页面仍保留 UNKNOWN。
三个 Worker 都使用相同的过期判定和恢复函数，每次 Worker 运行最多尝试一次自动恢复。
恢复必须是同一个学生账户，并通过 Session Check；不能确认恢复就停止并提示重新登录。
不会把登录页返回 200 作为成功，也不会让 CompleteRegistration 吞掉会话过期异常。
RegisterSections 明确返回认证拒绝时，撤销本次 attempted/等待核验/冷却和 Drop 跟踪，
恢复后重新读取个人记录再决定是否提交。若失效发生在 CompleteRegistration，则保留此前 Add
的不确定结果，不能因为登录恢复就重放该请求或 Drop。

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

## 只读复核

```powershell
python cloud_cli.py --load-session session.json --term 2027SP --sections <SECTION_IDS> --verify-only
```

此路径仅读取个人课表并输出逐 Section 状态，不启动 Worker，不发送 Add、Drop、Waitlist 或
CompleteRegistration。`<SECTION_IDS>` 替换为逗号分隔的目标 ID；凭证文件应只留在本机。

## 验证

运行 `python -m pytest -q`（开发环境需要 pytest 和现有 GUI 依赖）。测试默认禁止真实 HTTP，
覆盖首次预检拦截已有选课、学期/账户/完整性、计划与候补区分、超时但实际成功、混合结果、Duplicate、Closed 与已有选课、登录重定向/拥堵、
CompleteRegistration 超时、日志完整性/脱敏/磁盘失败、各 Worker 恢复与停止行为。
另检查 GUI 与 CLI 的真实初始化路径都注入 reader，以及明确会话失效后恢复、UNKNOWN 核验后
重新检查空位的回归行为。Qt 使用 offscreen 模式，不触发真实注册。
