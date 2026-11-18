# 注册结果核验（v1.3.1）

Dashboard 在设定时间发出首批普通 Add。Auto-Catch 在每门课通过余量检查时立即提交，
同一轮可继续检查其余 Section，最后统一核验个人课表。登录和首次普通 Add 的关键路径只使用原有请求。

## 已确认的证据与集成边界

- 仓库已有 `RegisterSections`、`CompleteRegistration` 和 `SectionDetails` 请求；
  `SectionDetails` 的总人数/余量不是个人选课记录，不能用于证明注册成功。
- 2026-11-03 Owner 在专用 Edge 窗口登录并打开 Spring 2027。通过浏览器网络事件实际观察到
  `GET /Student/Planning/DegreePlans/Current?studentId=<STUDENT_ID>`，HTTP 200，JSON 响应。
  页面没有为本次课表读取发送分页参数，也没有在切换到 Spring 2027 时加载另一个课表分页。
- `registration_reader.DegreePlanRegistrationReader` 使用同一 GET，验证 `DegreePlan.PersonId`
  后在 `DegreePlan.Terms` 里查找目标 Section 的精确记录。目标不在任何记录中时，
  根据 `SectionDetails` 返回的匹配 `Id` 和 `TermId` 确定应核对的学期。
  Auto-Catch 复用常规余量查询的数据，Dashboard 在提交后的核验过程中按需查询。
- Spring 2027 的 `2027SP` 与 Wenzhou 的 `2027SPW` 独立核验。授权页只负责登录，
  CLI 旧命令中的 `--term` 仍可解析，核验范围由真实 Section 数据决定。
  本轮在公开课表查询实际确认了 `SectionDetails` 的 `Id` 和 `TermId` 字段。
  [KeanWISE 公开课程目录](https://kean-ss.colleague.elluciancloud.com/Student/Courses)
- 原始响应、HTML、凭证和观察辅助脚本只保存在本地仓库外，不纳入 Git。
  `tests/test_registration_reader.py` 保存最小字段结构的合成 fixture：身份、Section、数量均为虚构。
  候补和带 Section 的计划课程测试是依据网页状态字段构造的变体，Owner 当时没有候补记录。
- 11 月 3 日的只读核验中，页面的已注册 Section 集合与 reader 结果逐项匹配，且待 Add 集合为空。
  HTTP Session Check 返回 VALID。验证过程限定 GET，不调用真实注册、退课、候补或完成注册请求。
- 移除了 Add/Waitlist 的 `Credits=3`，现在省略该字段，避免擅自修改固定学分。
  11 月 3 日只读查看官方网页脚本时，确认其发送 `Credits: plannedCourse.Credits()`。
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

11 月 3 日的页面使用完整 `Terms[].PlannedCourses` 渲染课表，页面注册标记与响应逐项匹配。
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
| `UNKNOWN` | 提交结果未核验、请求或读取失败、记录不完整或互相矛盾 |
| `WAITLISTED` | 个人记录确认候补；不显示为注册成功，也不继续 Add |

批量通知只能通过明确的 SectionId、消息中的准确 ID 或单目标请求关联。
不能把课程名称形式的批量报错随意归给某个 Section。一条 Duplicate/Already Registered
永远不能证明整批成功；Closed 也不能证明某门课尚未注册。账户记录优先于提交报错。

首次普通 Add 可以直接发出，同一 client 已确认注册或候补的 Section 会跳过。
部分拒绝但仍有可能已接收的 Section 时，照常发送一次 CompleteRegistration，再以个人课表逐门核验。
即使余量为零，后续轮次仍会核验有待确认的结果。读取失败会保留此前已确认的结果。

读超时、连接中断、无法识别的响应和接收确认均可能对应已写入的请求。对于这些情况，
重试要求两次完整的未注册或仅计划记录，后一次读取须在最后一次提交或确认响应后至少 5 秒开始。
如果服务端明确拒绝了某个 Section，只需一次完整记录核验。连接阶段超时可按规则再次尝试。
Auto-Catch 和 CLI 的只读核验完成后，重试前仍须重新检查余量。
等待核验的结果按 2、4、8、16、30 秒退避。还有可提交的 Section 时，轮次也遵守用户设置的间隔。
服务器读副本可能持续延迟，客户端无法保证服务器端恰好执行一次。

Waitlist 仅用于已明确 Closed、且个人记录确认未选的目标，候补状态会单独显示。
显式选择同步换课时，提交前会核验新旧课，避免对已注册的替换目标附带退课。发出的 Drop 不会自动重放。
确认退课目标已离开实际注册记录后，才能报告整次换课成功。
已有确认结果只保存在当前 client 内存中，不从历史日志恢复；重启后不会把旧学期/旧会话结果当作当前事实。
手动在官方页面改变课表后，应重新认证以建立新的 client。

## Session Check

按钮改为 Session Check，读取已有 DegreePlans 页面，禁止跟随重定向，以免把 Token 发送到 SSO。

- `VALID`：200 页面同时具有退出登录控件和带值的防伪 Token 控件。
- `EXPIRED`：401、登录/SSO 重定向、登录表单、`InvalidSession` JSON 或明确的防伪 Token 拒绝响应。
- `UNKNOWN`：超时、429/5xx、403、无法识别的 200 页面。403 也可能是权限或 CSRF 问题，不能直接归为会话过期。

11 月 3 日在已登录的 DegreePlans 页面与独立 HTTP GET 中均观察到
`a[href="/Student/Account/LogOff"]` 和 `input[name="__RequestVerificationToken"]`，Session Check 返回 VALID。
本轮留存会话的只读检查返回 EXPIRED。当前实现也识别公开网页脚本使用的 `InvalidSession` 响应。
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

每条事件使用 UTC ISO 8601 毫秒时间戳，可用 run/round/request ID 关联。
记录轮次起止、请求起止、实际收到响应的时间（未收到则为 null）、单调时钟耗时、
HTTP 状态、超时异常类型、各课程状态、已识别的学期和等待核验标记。每条事件单独追加、flush、关闭文件，
因此正常进程退出前不需要统一导出。进程被强制杀死时未配对的 request_started 可识别未完成请求；
不保证操作系统断电时所有文件缓存都已落盘。

- `first_confirmed_at`：客户端首次从 reader 确认注册的时间。
- `server_registered_at`：仅接受真实记录明确提供的服务端注册时间；未提供时为 null。
- 不记录 Cookie、Token、HTTP 头、请求/响应正文、登录 URL 参数或原始异常字符串。
  拒绝原因保存为 `closed`、`credits_rejected` 等代码，避免服务端文本夹带凭证。
- 磁盘写入失败会明确提示；不把日志失败或网络失败伪装为注册成功。

## 只读复核

```powershell
python cloud_cli.py --load-session session.json --sections <SECTION_IDS> --verify-only
```

此路径读取个人课表和所需的 Section 详情，输出逐门状态。
将 `<SECTION_IDS>` 替换为逗号分隔的目标 ID。凭证文件留在本机。

## 验证

运行 `python -m pytest -q`（开发环境需要 pytest 和现有 GUI 依赖）。测试默认禁止真实 HTTP，
覆盖首次发包顺序、跳过已确认注册、跨学期及账户核验、计划与候补区分、超时但实际成功、混合结果、Duplicate、Closed 与已有选课、登录重定向/拥堵、
CompleteRegistration 超时、日志完整性/脱敏/磁盘失败、各 Worker 恢复与停止行为。
另检查 GUI 与 CLI 的真实初始化路径都注入 reader，以及明确会话失效后恢复、UNKNOWN 核验后
重新检查空位的回归行为。Qt 使用 offscreen 模式，不触发真实注册。
