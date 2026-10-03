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
