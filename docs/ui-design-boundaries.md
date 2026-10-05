# UI design boundaries and visual debugging

## Classify design references

Before creating a plan, diff list, implementation, measurement, or acceptance result, classify reference-image content as application UI, device illustration, or design annotation.

- Device shells, system time, battery, Wi-Fi, signal, carrier, notch, Home Indicator, and status-bar backgrounds are device illustrations by default. Do not implement them as page DOM, CSS, icons, pseudo-elements, or reserved blank space.
- Canvas titles, module numbers, dimension lines, and explanatory labels are annotations by default. Exclude them from application copy, icon, geometry, and pixel acceptance.
- Actual application navigation, business dates/times, and functional icons remain application UI when their position and function identify them as such.
- Only an explicit user instruction for a specific illustrated element makes that element an exception. Do not extend the exception to adjacent device elements.
- Keep original reference images unchanged. The operating system owns system bars; the app may retain real safe-area and system-margin adaptation without drawing a simulated system bar or adding space to replace excluded device content.

## Native Android and browser work

- Main Android may use Kotlin and Jetpack Compose when explicitly authorized by the active user task. This authorization applies to Main Android only; it does not extend to LM or iOS.
- For Android native UI, use Compose semantics, layout bounds, constraints, modifiers, and source locations for diagnosis and automated checks. Edge CDP is only evidence for the retained browser WebUI and cannot stand in for native acceptance.
- For browser CSS work, use the existing Microsoft Edge CDP workflow and fixed fixtures. Before changing CSS, capture target and parent geometry, scroll position, relevant computed styles, matched CSS rules, and rule source locations.
- Hold viewport, fonts, fixture data, animation state, expanded state, and scroll position constant. Diagnose one module and one cause at a time; locally retest that module, then check states affected by shared rules. Run the full acceptance batch only after the batch is ready.
- If the same deviation does not improve after two iterations, recheck element mapping, coordinate conversion, and cascade coverage before another edit. For Compose also recheck parent constraints and modifier order. Do not relax tolerance or stack compensating styles.
- Use `./scripts/acceptance.sh` to produce formal comparison images and indices. Do not substitute manual screenshots. Keep failed states in `acceptance/_failed/index.yaml`.

## Confirmed application behavior (2026-10-01)

- The chat header's back control returns to the previous application page. It is a
  separate application control from the history button and must not create or clear a conversation.
- The user confirmed that reading mode retains the complete structured timetable card.
  The supplementary five-column timetable is not a requirement for another output style;
  its additional station-number and stop-duration column headers are excluded from native copy acceptance.
  Existing return-to-bottom, follow-up draft and scroll interactions remain in scope.

## 用户确认的页面留白（2026-10-02）

- 用户明确要求保留原稿手机屏幕内的页面左右留白，并修正受影响的验收基准。
- 页面留白属于应用布局；不能与设备外壳、系统状态栏或画板边距一起排除。
- 完整应用画框取设备内侧屏幕表面，保留应用留白；独立组件示意应映射到应用内容区。
- 截图尺寸、系统安全区、用户字体缩放、44dp 最小触控范围及现有误差门槛不因此改变。
- 原来剔除页面留白的通过记录仅作为历史证据，不能证明修订基准下通过。

## 用户确认的底部系统适配（2026-10-02）

- 输入区底边随实际系统安全区变化，适配手势导航、三键导航和键盘；不将设备示意区高度写成固定应用留白。
- 不重复叠加已消费的系统边距，不把输入控件移入系统按键或键盘覆盖区域。
- 原稿应用组件的尺寸和间距仍须对齐；真实系统安全区引起的屏幕底部留白单独记录。

## 用户确认的整体协调优先（2026-10-03）

- 用户明确放宽尚未完成的验收标准，优先保证整体和谐，不要求逐像素接近设计稿。
- 原稿保留为视觉方向与应用功能依据；已通过的测量及历史运行记录不改写。
- 尚未完成的字形、图标细节、精确坐标与原图墨迹并集测量只作为诊断，不再单独阻止整体验收。
- 新验收优先检查视觉层级、统一色彩与间距、文字可读、组件排列、完整功能与真实操作体验；遵循 `docs/ui-acceptance-policy.md`。
- 44dp触控下限、内容遮挡、系统安全区、日期/来源语义、密钥遮蔽和证据真实性仍为硬性要求，不因放宽视觉标准而减弱。

## 用户确认的客户端操作修订（2026-10-03）

- 聊天顶栏取消应用返回键，原位置改为设置键；历史键打开左侧会话抽屉，右侧加号建立空对话。
- 空对话展示查询引导和可编辑示例；点击示例填入草稿，不直接发送。
- 设置提供商须弹出独立选择层，一次展示可用选项；连接测试须就地展示加载、成功或失败反馈。
- 保存设置后回到对话；不记住 API Key 表示不落盘，当前进程内仍可用于对话。
- 阅读、生成和普通状态使用同一输入框；回到底部仅在离开底部时显示，到底后消失。
- 未来日期时刻查询不联查尚未发生的车组交路，也不以交路失败提示误导用户。
- 此次用户截图中的系统状态栏、设备悬浮条和底部手势条仍属于设备示意，不实现。

## 交叉评测后的客户端修订（2026-10-04）

- 根据用户授权的修复方案，未发送草稿按会话保留；打开历史、重开当前会话、新建后返回不应丢失草稿。
- 空对话的占位符使用首次提问语境；普通回看显示“回到底部”，仅阅读期间确有新内容时提示“有新内容”。这些是应用状态文案修订，不实现画板标注。
- 密钥无效仅用于有明确认证失败证据的状态；未配置、地址拒绝和网络失败不得仅因错误提及API Key就标成认证错误。
- 提供商选择保留独立弹层和所有已有选项，自定义入口固定可见；辅助设置操作降低视觉权重，保留44dp触控及原有保存/测试反馈。

## 用户确认的图标与选择控件修订（2026-10-04）

- 核心品牌 Logo 保留；功能图标优先采用统一的开放矢量素材，记录固定版本、原始来源与许可，避免用字符或表情代替控件图标。
- 布尔设置使用可点击、可拖动的滑动开关，并提供开关语义、状态与禁用反馈。
- 已添加提供商用卡片列表呈现；当前提供商用绿色圆点及无障碍说明标识，编辑入口与切换操作分开，底部保留添加入口。
- API 方言、主题等枚举设置点击后展开所有选项，不使用反复点击轮换值的交互。
- 按钮正文按操作层级适度增大；继续保留44dp触控、安全区和密钥隐私要求。

## 用户确认的轻量动效（2026-10-04）

- 可为侧栏、页面进入、展开区域、滑动开关和按钮反馈增加短促过渡；不增加持续或装饰性循环动效。
- 动效不得重建会话、重启请求或影响草稿；退出动画期间防止重复导航，结束后仍按既有布局与安全区验收。

## 用户确认的聊天与历史操作修订（2026-10-04）

- 回到底部使用覆盖消息区域的小浮动按钮，仅按钮自身有背景；外层透明且不额外占用消息列表高度。
- 历史条目只显示单行标题，长标题省略，不显示第二行摘要。
- 会话操作使用靠近所点条目的紧凑圆角菜单，保留重命名及删除确认；移除重复标题与取消行，点击外部或按返回关闭。
- 侧栏露出的右侧上下角使用真实裁切；下拉选项的选中背景不能越过圆角边界。

## 用户反馈后的消息气泡与余票草稿（2026-10-04）

- 用户消息气泡随文字长度伸缩，整体靠右；不为短消息设置固定最小宽度制造空白。
- 时刻卡片的余票快捷追问附带已知日期、车次及起讫站；缺少区间时保留明确的可编辑站名占位，不猜测乘车区间。

## 所有回复的操作区（2026-10-04）

- 已保存的每条助手回复均展示复制、重新生成及分享，不以是否包含时刻卡片作为出现条件。
- 操作区每条回复仅出现一次，复制和分享包含文字及结构化结果的可读内容；重新生成使用该回复对应的原提问。
- 分享调用系统分享面板，不自动发送到任何应用或联系人；图标沿用固定版本Lucide素材。

- 加载提示使用与实际阶段相符的通用描述；检索卡位于助手回复区，生成期间列车头像保留。
- 单行历史条目使用48dp最小高度，标题垂直居中，不保留双行布局的顶部排布。

- 新建入口复用待用的空白会话，禁止反复点击产生更多空白会话；当前已是空白会话时提示“已在新对话中”，保留未发送草稿。

## 失败与无记录回复区（2026-10-04）

- 失败、无记录、批量结果及生成中尚未保存的结果统一放入助手头像右侧的回复内容区，不铺满整行。
- 无文字回复仍保留列车头像；同一条回复的多个结果沿用相同左边界，避免重复头像。
