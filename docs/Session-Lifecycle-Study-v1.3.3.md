# KeanSeatsCatcher v1.3.3 会话生命周期

观测日期为 2026-11-20，时间统一为北京时间（UTC+08:00）。源码基线为 `aaf596a`，About 页面显示 Version 1.3.3。本轮交付物为研究报告。真实环境实验限定于正常网页登录、应用会话检查和单个 Section 的只读查询。

本次观察到，原始 KeanWISE 凭证在第 28 分钟有效、第 35 分钟已失效。余位查询保留了续签 Cookie，第 39 分钟仍能读到已登录的 Degree Plans。旧应用会话失效时，同一浏览器的 Okta SSO 仍可以重新签入。

## 真实环境实测

### 观测方法

账号持有人在独立的 Edge 窗口完成认证。浏览器走 KeanWISE 登录、Okta SAML 和 KeanWISE 回跳。对照阶段用同一浏览器的 SSO 分别建立 B、C、D 三组应用凭证。各组的 KeanWISE 主 Cookie 和防伪 Cookie 与初始组值不同，初始组在 D 建组后仍通过检查。页面取样后导航至 `about:blank`，以排除该页面的后台请求。约 40 分钟的测量中共发出 22 次应用探测和 2 次 Okta 会话查询，另有正常的浏览器登录流程。实验只记录状态码、时刻、Cookie 名与属性、值是否变化和页面判定标记。

Kean 注册办公室将该站点列为云版 Ellucian Colleague 的 KeanWISE Self-Service。浏览器会话 Cookie 的存储属性可以和应用认证票据的服务端有效期不同。 [Kean Registrar](https://www.kean.edu/offices/registrars-office/registrar-resources-faculty-and-advisors) · [ASP.NET Core Cookie 认证](https://learn.microsoft.com/en-us/aspnet/core/security/authentication/cookie?view=aspnetcore-10.0#absolute-cookie-expiration)

| 组 | 模拟路径 | 基线后的操作 |
| --- | --- | --- |
| A | Dashboard 等待 Scheduled Time | 保持静置，最后查一次 Degree Plans |
| B | Auto-Catch 的应用请求 | 用同一个主会话，约每 7 分钟对一个 Section 执行一次只读 `SectionDetails` |
| C | Dashboard 手动 Session Check | 约每 7 分钟用主会话的副本检查一次 Degree Plans |
| D | 较短时间的静置对照 | 独立静置后检查一次 Degree Plans |

HTTP 探测按 v1.3.3 的 `KeanApiClient` 执行。计时以本机单调时钟为准。应用会话年龄从浏览器提取凭证开始计算，静置从该组上一次响应完成开始计算。浏览器阶段只走官方登录。对照组共用一个 Okta 浏览器会话，服务端是否按账号维护共享活动状态仍需验证。 [对应的应用请求](../core_api.py#L232)

### 浏览器登录与会话可见性

- 13:25:28 匿名 `DegreePlans` GET 返回 302 到 KeanWISE 自己的登录路径，KSC 判为 `EXPIRED / login_redirect`。13:25:35 同一请求路径带新应用凭证返回 200，页面同时有登出控件和防伪字段。匿名对照只用于确认当天的拒绝信号。
- 应用主 Cookie `studentselfservice_production`、`studentselfservice_production_Sso` 和 `.ColleagueSelfServiceAntiforgery` 在浏览器中都是会话 Cookie，客户端看不到带日期的到期属性。Okta 的 `idx` 也为会话 Cookie。浏览器关闭与服务端接受应用 Cookie 的时间分别由浏览器和服务端决定。 [Okta Identity Engine Cookie 说明](https://developer.okta.com/docs/guides/oie-upgrade-sessions-api/main/)
- 13:26:04 首次读取 Okta 的当前会话接口，HTTP 200。`createdAt` 为 13:25:29，`expiresAt` 为 15:26:06，距观察时刻约两小时。随后从同一浏览器清除实验用 KeanWISE Cookie 并正常打开应用，B、C、D 均经 Okta 返回 Degree Plans，约 7 秒完成，期间无需再次输入密码或完成验证。
- A 的应用会话失效后，14:03:35.893 读取同一 Okta 会话仍返回 200。`createdAt` 仍为 13:25:29，`expiresAt` 已移到 16:03:37。两次响应都给出距读取约两小时的到期点。读取当前会话本身可能被计为活动，因此这是可移动的观测到期点。Okta 的全局政策分别控制最大寿命和空闲期限，应用登录政策还能独立要求重新验证。 [Okta 对 `expiresAt` 的说明](https://support.okta.com/help/s/article/how-to-manage-the-sessions-me-api-expiration-time?language=en_US) · [空闲和最大寿命](https://support.okta.com/help/s/article/How-is-the-Session-Lifetime-calculated) · [应用登录政策](https://help.okta.com/oie/en-us/content/topics/identity-engine/policies/osop-evaluation.htm)
- 14:03:35.953 同一浏览器再次只清除实验用应用 Cookie，然后走官方 SSO，约 10 秒建立 E，未出现交互表单。14:03:48.191 的 Session Check 为 200 / `VALID`。这是旧应用会话返回 302 时，同一个 Okta 浏览器会话仍能签入应用的直接证据。

### 静置、检查和轮询的对照

表中时刻为本机收到并记录结果的时间。应用会话年龄和静置间隔按请求开始时测量。活动组前五次的间隔约 7 分钟。

| 应用会话年龄 | B 只读余位查询 | C Session Check | 关键响应 |
| --- | --- | --- | --- |
| 基线 | 13:26:13.597 · 200 JSON | 13:27:08.279 · 200 `VALID` | 回发 `_0`，`_sfid` 值相同 |
| 约 7 分钟 | 13:33:16.345 · 200 JSON | 13:34:12.319 · 200 `VALID` | 主认证 Cookie 未回发 |
| 约 14 分钟 | 13:40:16.495 · 200 JSON | 13:41:11.247 · 200 `VALID` | 两组均回发 `_ls` 的清除和重发 |
| 约 21 分钟 | 13:47:15.728 · 200 JSON | 13:48:11.172 · 200 `VALID` | 两组均回发主认证 Cookie，只有 B 的主 jar 接受了变化 |
| 约 28 分钟 | 13:54:15.984 · 200 JSON | 13:55:10.978 · 200 `VALID` | C 再次收到主认证 Cookie，主 jar 仍保持原始凭证 |
| 约 35 分钟 | 14:01:15.896 · 200 JSON | 14:02:09.790 · 302 `EXPIRED` | C 重定向到同源登录路径 |
| 约 39 分钟 | 14:05:17.797 · 主会话读取 Degree Plans 得到 200 `VALID` | — | B 再次收到新值的主认证 Cookie |

| 静置组 | 上一次成功 | 首次静置后检查 | HTTP 响应 |
| --- | --- | --- | --- |
| D | 13:28:12.749 | 13:52:16.947，间隔 24 分 00.859 秒 | 200 `VALID`，回发主认证 Cookie |
| A | 13:28:14.488 | 14:03:16.353，间隔 35 分 00.641 秒 | 302 `EXPIRED / login_redirect`，重定向到同源登录路径 |

C 持原始应用凭证在 28 分 02.828 秒仍能读到已登录的 Degree Plans 页面，在 35 分 02.609 秒已被重定向到登录路径。A 被拒绝时的凭证年龄为 37 分 40.969 秒。B 在第 21 分钟保留了首次收到的主认证 Cookie 新值，随后仅轮询余位，在 39 分 04.625 秒用同一个主会话读到了受保护页面。受保护页面同时有登出控件和防伪字段，响应再次换发主认证 Cookie。这一组响应支持应用认证 Cookie 的滑动续签机制。

### Session Check 的 Cookie 与防伪字段

| 对象 | 13:25:35 的已登录检查响应 | 对主会话的影响 |
| --- | --- | --- |
| `studentselfservice_production_0` | `Set-Cookie` 回发不同的值，未带 `Expires` 或 `Max-Age` | 值进入检查副本，主 jar 保持原样 |
| `studentselfservice_production_sfid` | 回发与原 Cookie 相同的值 | 主 jar 保持原样 |
| `.AspNetCore.Mvc.CookieTempDataProvider` | `Expires` 为过去时间，表示移除该 Cookie | 主 jar 保持原样 |
| 页面 `__RequestVerificationToken` | 首个隐藏字段值与发送的 header 不同 | 只用于检查页面标记，主 header 保持原样 |

首条 `SectionDetails` 只读请求于 13:26:13 返回 200 JSON。其 `Set-Cookie` 同样回发 `_0` 和 `_sfid`，变化进了 B 的主 jar。应用主 Cookie 与防伪 Cookie 的值在该次读取后保持原样。防伪字段与会话 Cookie 各自有独立的用途。 [ASP.NET Core 防伪机制](https://learn.microsoft.com/en-us/aspnet/core/security/anti-request-forgery?view=aspnetcore-10.0)

约第 14 分钟，两条路径都出现了 `_ls` 的清除和重发。第 21 分钟，B（13:47:15.728）和 C（13:48:11.172）首次观察到 `Set-Cookie: studentselfservice_production` 的值改变。该 Cookie 带 `Secure`、`HttpOnly` 和 `SameSite=Lax`，无 `Expires` 和 `Max-Age`。B 的主 jar 接受了变化，C 的变化仅体现在检查副本。两组的防伪请求头仍使用原值。

D 在 13:52:16.947 检查时读取到不同的页面防伪字段。13:53:27.241 仍持原防伪请求头发一次 `SectionDetails`，得到 200 JSON，KSC 未识别到防伪拒绝。该实测结论限于这次只读查询，写入路径的防伪校验独立适用。


## 源码可确认

### Dashboard 与 Session Check

- Dashboard 的 Scheduled Time 等待循环只读本地时间。启动时可能有一次独立的学分预读。等待期间可以手动检查会话，再用同一账号重新登录并在截止前把新凭证交给仍在倒计时的 worker。到点进入正常执行链路，既有的检查结果和进行中的检查均不阻塞到点执行。 [等待与截止交接](../ui_main.py#L113) · [检查与开始入口](../ui_main.py#L435) · [账户校验](../ui_main.py#L602)
- 按钮在 `new_read_session()` 创建的副本上，向 `DegreePlans` 发一次 `Cache-Control: no-cache` 的 GET。HTTP 层禁止跟随重定向。200 页面须同时含退出登录控件和非空防伪字段才能得到 `VALID`。页面字段的值只用于判定存在，主会话的请求头继续使用原值。HTTP 接受的回发 Cookie 跟随副本一起丢弃。恢复函数调用无副本参数的 `check_session()`，该路径使用主 Cookie jar。 [按钮](../ui_main.py#L78) · [会话副本](../core_api.py#L92) · [请求与检查](../core_api.py#L159) · [恢复入口](../worker_session.py#L5)
- 401、识别到的登录重定向、登录表单、明确的 `InvalidSession` JSON 和特定 400 防伪拒绝会触发过期处理。无法识别的返回、403、429、5xx 和传输失败在 Session Check 上是 `UNKNOWN`。一次 `VALID` 说明刚读到了登录状态页面，后续业务请求仍各自接受认证和防伪校验。 [判定逻辑](../core_api.py#L121) · [三态结果](../core_api.py#L232)

### Auto-Catch 与自动恢复

- Auto-Catch 和 CloudWorker 用主 `requests.Session` 逐个发只读 `SectionDetails` POST。该路径会积累 HTTP 库接纳的响应 Cookie，并继续使用 Auth 阶段提取的 Verification Token 请求头。常规轮询把非 200、连接超时和解析失败折叠为无可用结果。连续出现 429、5xx 或 403 时，worker 可以继续按轮询间隔运行，界面可能显示无余位。恢复入口由明确的会话拒绝触发。 [余位请求与错误处理](../core_api.py#L257) · [轮询状态结果](../core_api.py#L396) · [Auto-Catch 循环](../auto_catch_worker.py#L76)
- Dashboard、Auto-Catch 和 CloudWorker 每次运行最多尝试一次自动恢复，成功后再次失效也会停止。恢复要求同一学生账户，更新本地凭证后执行 Session Check，成功时再读注册记录。注册记录读取仍可能处于 `UNKNOWN`，应以原有核验与防重试规则处理。 [Dashboard 恢复](../ui_main.py#L184) · [Auto-Catch 恢复](../auto_catch_worker.py#L41) · [Cloud 恢复](../cloud_worker.py#L67) · [恢复验证](../worker_session.py#L5) · [注册核验](../core_api.py#L605)
- Windows GUI 给 Authentication 和后台恢复分配了不同的 `KeanAuthManager`。手动重登可以复用尚存的浏览器。后台 `silent_relogin()` 会退出自己已有的驱动再新开浏览器，Edge 和 Chrome 的启动参数均未指定持久用户配置。Keep Browser Open 只影响人工登录窗口。新窗口遇到密码、Okta Verify 或其他交互挑战时，通用登录按钮点击无法完成认证。驱动启动、页面稳定、按钮查找和跳转等待各有自己的计时，源码中的 30 秒只对应最后的 Degree Plans 等待。 [GUI 连接](../ui_main.py#L778) · [手动浏览器路径](../core_auth.py#L21) · [驱动启动](../core_auth.py#L80) · [后台重登](../core_auth.py#L228) · [EdgeDriver 官方文档](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/capabilities-edge-options)
- Cloud CLI 用 `--load-session` 导入凭证时，`auth_manager` 为 `None`，失效后直接停止。即使用可见浏览器完成 CLI 的首次登录，CloudWorker 的恢复仍固定尝试 headless。 [导入和可见登录](../cloud_cli.py#L102) · [CloudWorker 恢复](../cloud_worker.py#L67)

## 本机复核与历史日志

- 在本机启动两次默认配置的 EdgeDriver 157.0.4341.60，只停留在初始空白页。两个驱动报告了不同的临时用户数据目录。
- 用本机回环 HTTP 模拟带 `Set-Cookie` 的有效页面。真实 `PingWorker` 返回 `VALID`，主 Cookie jar 和原防伪请求头均保持原值。直接在主会话上检查时，同一个 Cookie 名在原始无域 Cookie 和新收到的带域 Cookie 中分别出现，下一请求同时发送了这两项。这复现了扁平化导入的局部风险。真实环境的 Cookie 观察见上文。 [凭证提取](../core_auth.py#L150) · [导入与副本](../core_api.py#L52)
- 在本机给 Auto-Catch 的只读查询模拟一次 503，worker 记录本轮未发现余位、未触发过期处理，恢复计数为 0。
- 既有日志中，11 月 19 日 09:08:34.421 的课表只读请求收到 200，同一日志在 09:55:16.172 的 Session Check 收到 302，判为 `EXPIRED / login_redirect`。两次响应相隔 46 分 41.751 秒。日志欠缺原始登录时间和浏览器活动，适合作为历史旁证。当天 13:36 的查询和 13:46 的检查另有 503，Session Check 判为 `UNKNOWN`。
- 现有离线回归测试 115 项通过。测试夹具拦截真实 HTTP。 [测试网络隔离](../tests/conftest.py#L18)

## 仍需进一步验证

- KeanWISE 的精确失效和换签阈值，以及活动会话的绝对上限。本轮的主要对照来自一个账号和一个时间窗口。旧凭证的观测窗口为第 28 到 35 分钟之间，轮询仅验证到约 39 分钟。更长的静置、网络中断以及更换账号或环境后需重新取样。
- Okta 自然失效的时刻及触发重认证的情形。两次 `expiresAt` 提供的是采样时的到期点，且查询可能影响它。空闲期限、绝对上限和应用 MFA 重验条件仍需用更长的静置对照或管理员政策确认。当前会话接口已被 Okta 标记弃用，实验只把它用于取证。 [Okta Identity Engine 会话说明](https://developer.okta.com/docs/guides/oie-upgrade-sessions-api/main/)
- KSC 的 `silent_relogin()` 本轮只审计了源码并验证其默认 EdgeDriver 配置行为。真实环境验证的是已有浏览器通过官方 SSO 重新入站。还需独立确认 KSC 真正的恢复路径在浏览器失联、SSO 失效或需 MFA 时的用户体验。

## 最小改进方案

1. **定时任务沿用现有的同账号认证。** 长倒计时在到点前预留一次正常登录和 MFA 的时间，例如提前 5 到 10 分钟。通过 Authentication 打开页面、重新提取，再做一次 Session Check。界面若需要小改动，优先显示最近一次有效检查的时间，以及过期时直达 Authentication 的入口。
2. **让 Auto-Catch 明确呈现健康状态。** 只有拿到可用的余位数据才显示无余位。遇到连续的暂不可用响应，沿用原轮询间隔做有界退避并持续显示原因。明确过期且恢复失败时，停止并提示同账号重新认证。未来若保留自动恢复，优先复用仍打开的已认证窗口，最多一次。重新启用任务前继续校验账号并核对原有业务记录。
3. **续签 Cookie 的处理按需做窄修。** 如果以后明确要让用户主动执行的 Session Check 接收续签，保留 Cookie 的域、路径和过期属性，只采用已验证同源响应的更新，并与正在运行的请求同步。页面字段变化本身无需触发 Token 替换。保留域和路径也可避免同名新旧 Cookie 同时发送。
