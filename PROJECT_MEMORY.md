# MHWilds 配装搜索器 - 项目记忆

## 技能体系（核心规则）

### 系列技能
- **来源**: 同系列技能名的防具2件装备时效果Ⅰ发动，4件时效果Ⅱ发动
- **等级**: 固定2级（Lv1=2件, Lv2=4件）
- **武器洗练**: 武器可出1个系列技能（但不影响防具件数计算）
- **数据位置**: `skills_data.json` → `系列技能`
- **SKILL_CAPS**: 统一为2
- **need_pieces 逻辑**: 系列技能 Lv1=2件/Lv2=4件；组合技能固定3件。代码实现: `4 if not GROUP_SK and lv >= 2 else (3 if GROUP_SK else 2)`

### 组合技能
- **来源**: 同组合技能名的防具3件装备时发动
- **等级**: 固定1级（Lv1=3件）
- **武器洗练**: 武器可出1个组合技能
- **数据位置**: `skills_data.json` → `组合技能`
- **SKILL_CAPS**: 统一为1

### 武器技能（重要架构理解，2026-08-07 修正）
- **武器 = 独立组件**：武器由「系列技能 × 组合技能」两两连接组成（系列技能25个、组合技能若干），共数百种武器组合，与防具、护石**平权**（都是装备来源）
- **固定武器技能 ≠ 技能需求**：固定=预筛选武器（只带该技能的武器进入匹配池），**不是**把该技能作为配装硬性需求（不加入 requirement_skills / search_combo）
- **平权原则**：武器提供的系列/组合技能只作为"1件补充"（帮助凑已需求的件数），配装不强制达到该技能的等级
- **来源（洗练）**: 武器洗练可出1个系列技能（等级档位Lv2=效果I/Lv4=效果II）+ 1个组合技能（Lv3）
- **来源（珠子）**: 插武器孔的珠子（武器有3个孔位，1-3级），即 WEAPON_SK

### 防具技能
- **来源**: 插防具孔的珠子 + 防具自带
- **等级**: 各技能不同，见 `skills_data.json` → `防具技能` 的 `max_lv`

## 架构

### 后端
- `gui_server.py`: HTTP服务器，端口8766，处理API请求
- 运行指令（在项目根目录终端执行）:
  - 默认端口: `python gui_server.py`
  - 指定端口: `python gui_server.py 8766`
  - 访问地址: http://localhost:8766
- `fast_search_v3.py`: DFS搜索引擎，位掩码+向量化优化
- `calc_v8_final.py`: 伤害计算模块（import耗时约43秒，正常现象）
- 数据文件: `decos_cn.json`(珠子), `armors_cn.json`(防具), `my_charms.json`(护石), `charms_cn.json`(可 craft 护石), `skills_data.json`(技能数据)

### 前端
- `index.html`: 单页面应用，内嵌CSS+JS

### 关键API
- `/api/info`: 返回技能数据、分类、上限等
- `/api/custom_search`: 自定义搜索（支持 `auto_weapon_skill` 参数，留空时自动匹配最优武器技能）
- `/api/query_extra`: 追加技能查询
- `/api/detail_calc`: 计算技能伤害详情（不搜索）

### 搜索逻辑
- `dfs_search()`: DFS回溯搜索，输入 fixed_skills(防具技能需求) + combo_skills(系列/组合技能需求)
- `dfs_search_auto_weapon()`: 武器技能自动匹配，遍历所有系列+组合技能挑最优
- 系列技能通过防具件数验证（`verify_series`），不是通过珠子
- `NO_DECO_SK = SERIES_SK | GROUP_SK`: 这些技能不能用珠子插，只能靠防具件数

## 术语规范（必须遵守）
- **套装技能**: 指系列技能（SERIES_SK）+ 组合技能（GROUP_SK），即 `NO_DECO_SK = SERIES_SK | GROUP_SK`
- **武器技能（珠子）**: 指插武器孔的珠子技能（WEAPON_SK），如攻击、看破、超会心等
- **防具技能（珠子）**: 指插防具孔的珠子技能 + 防具自带技能
- **武器洗练技能**: 指通过武器洗练获得的系列技能或组合技能（NO_DECO_SK），不是珠子
- **激活**: 系列/组合技能达到所需防具件数要求（`series_check.ok === true`）

## 武器配置区系统（重要）
### 武器配置区有两个独立槽位：
1. **📜 系列技能槽**: 可选择一个系列技能作为武器洗练技能
   - 留空：搜索时自动匹配最优系列技能
   - 禁用：该槽位不允许任何系列技能
   - 选择具体技能：固定使用该系列技能（预筛选武器）
2. **⚡ 组合技能槽**: 可选择一个组合技能作为武器洗练技能
   - 留空：搜索时自动匹配最优组合技能
   - 禁用：该槽位不允许任何组合技能
   - 选择具体技能：固定使用该组合技能（预筛选武器）

### 固定/留空/禁用的算法差异（2026-08-07 优化）
- **固定系列+固定组合**：武器池只剩1把武器 → 直接搜索（最少组合、最快）
- **固定其一+另一留空**：枚举"固定技能 × 留空侧需求相关技能"的子集（预筛选缩小武器池）
- **两个留空**：枚举全部需求相关组合（未需求系列与无技能等价，不生成以加速）
- **禁用（任一）**：auto_weapon=False，武器不带洗练技能，直接搜索
- **自动匹配=每个方案独立选武器（平权）**：dfs_search_auto_weapon 枚举每个武器候选各搜少量方案（per_weapon_limit，最多20），合并后按伤害排序，每个结果标记 `_auto_weapon_series`/`_auto_weapon_group`，方案间可选用不同武器（全留空时4种武器混合出现）。gui_server 按每个方案的标记构造展示用 weapon_skill。
- 实现：`_generate_weapon_equipment(fixed_series=, fixed_group=)` 预筛选（固定系列时跳过"只带组合"分支、固定组合时跳过"只带系列"分支、固定时排除无技能武器）；`dfs_search_auto_weapon` 枚举合并

### 固定武器技能的核心原则（2026-08-07 用户明确）
**固定武器技能 = 预筛选武器，不是框定技能范围**：
- 武器只是系列/组合技能的来源之一，固定武器技能只是固定了武器组件（提供1件该技能），缩小了武器匹配池
- 该技能仍可通过防具/护石追加，不应被当作"已固定技能"排除出追加查询
- 后端 `user_weapon_skills` 只表达"武器提供1件"，不加入需求（requirement_skills/search_combo）

### 关键区分：
- **武器配置区**选择的技能 = 武器带的洗练技能，显示在信息栏"武器技能"
- **技能选择区**选择的 NO_DECO_SK = 防具需求，不显示在信息栏"武器技能"
- 两者完全独立，互不影响

### 等级计算：
- 系列技能：几件装备 = 几级，Lv2（2件）激活效果1，Lv4（4件）激活效果2
- 组合技能：3件激活
- 武器提供1件（通过洗练获得）

## 用户需求汇总（必须遵守）

### 武器技能相关
1. 武器技能选框有空选项（"留空·自动匹配最优"），留空时搜索后自动选择最优
2. "自动选择武器技能"作为独立功能已移除，改为留空时自动匹配
3. 武器技能库可禁用技能，禁用后不参与自动匹配
4. **固定武器技能=预筛选武器，不是框定技能范围**（2026-08-07）：固定技能只是固定武器组件（提供1件），该技能仍可通过防具/护石追加，不得作为配装硬性需求（2026-08-07 明确）

### 搜索相关
4. 搜索必须能出方案，自检通过再提交
5. 武器技能(攻击/看破等)也可由防具提供，不能仅靠武器孔+护石判断可行性

### 技能选择相关
6. 技能选择界面支持直接取消技能（等级选择框含"取消"按钮）
7. 各模式（技能选择/已选/追加）收藏技能同步置顶
8. 追加收藏模式必须显示所有收藏技能（不能只显示后端返回的2个）

### 方案显示相关
9. 方案区域技能不折叠，始终显示
10. 系列/组合技能与其他技能显示方式一致，不加件数徽章
11. 方案区域有"追加技能"按钮，可基于方案现有技能追加并重新搜索

### 方案对比相关
12. 方案对比支持多选（勾选），对比伤害详细计算过程
13. 对比包含：伤害总览表 + 各技能独立贡献对比表 + 各方案技能构成

### 查询模式相关
14. 查询模式按钮下移：先模式切换再主按钮
15. 追加查询的禁用模式排除已禁用技能

### 技能组管理相关
16. 技能组支持编辑名称、删除、上下移动排序
17. 技能组操作区域放在「已选技能」卡片内

### 追加查询相关
18. 追加模式内的"追加并重新搜索"应调用查询追加（runExtraQuery）而非查询方案（runSearch）
19. 已保存方案详情增加"再次查询"功能，默认包含全部已激活技能，点击技能可取消

### 术语规范
20. "套装技能" = 系列技能 + 组合技能（NO_DECO_SK），代码中统一使用该术语

### UI相关
21. 参考主流网站审美：暗色主题、圆角卡片、微动画
22. CSS变量统一管理颜色
23. 横向标签页切换功能区域
24. 技能选择：点击展开等级，再点取消
25. 收藏置顶：所有技能列表中收藏的技能排在最前
26. damage-big 伤害数字使用三色渐变 + drop-shadow 发光
27. detail-table 圆角边框 + best-row 绿色高亮
28. header 底部发光线 (::after 伪元素)

## 重要修复记录
- 系列技能上限从4改为2（硬编码错误，已用skills_data.json覆盖）
- 组合技能上限从3改为1（同上）
- detail_calc不执行搜索，直接基于已有技能计算
- 搜索超时移除（全量搜索）
- 武器技能预检查：允许防具提供武器技能，不误判无解
- 技能上限和分类从skills_data.json自动生成
- 2026-08-07 追加模式进度条修复：_run_skill_job 进度 dict 补上 'type':'progress'；孔位二分化6步计入 total 并 yield 进度；gui_server 添加 Transfer-Encoding: chunked 分块传输（否则 BaseHTTPServer 缓冲整个响应，进度事件一次性到达）
- 2026-08-07 固定武器技能语义修复：前端 runSearch/runExtraQuery 不再把武器区技能加入 combo；后端 search_combo/requirement_skills 不再包含武器技能等级（固定=预筛选武器）；dfs_search_auto_weapon 支持部分固定（固定系列+固定组合直接搜索，固定其一枚举子集，全留空枚举需求相关全部）；_generate_weapon_equipment 新增 fixed_series/fixed_group 参数，固定时排除无技能武器

## ⚠️ 关键教训：浏览器缓存问题（2026-07-31）
**根因**：用户反复报告"功能没做/没生效"，实际代码已在 index.html 中正确实现，但浏览器缓存了旧版页面，导致用户看到的是旧代码。
**表现**：renderExtraSkills 旧版把 SKILL_CAPS 补充 loop 放在 `if (extraMode === 'normal')` 分支内，导致收藏模式只显示后端返回的2个技能（而非全部9个收藏技能）。
**修复**：
1. `gui_server.py` 的 `_send_html()` 添加 `Cache-Control: no-cache, no-store, must-revalidate` + `Pragma: no-cache` + `Expires: 0` 三重禁缓存头
2. `toggleFav()` 修复：原先只调用 `renderSkillSelection()`，现增加 `renderSelectedSkills()` + `renderExtraSkills()`（若 extraQueryResult 存在），确保各模式收藏置顶同步刷新
3. 测试时必须用 cache-busting URL（如 `?v=20260731c`）或强制刷新

## 功能验证清单（2026-07-31 全部通过）
1. ✅ 收藏模式显示全部收藏技能（9/9，原仅2个）
2. ✅ 查询模式按钮顺序：mode-toggle 在上，extra-btn 在下（top 2628 < 2677）
3. ✅ 各模式收藏置顶：normal 模式 154 项，收藏技能(lastFavIdx=8)全部排在非收藏(firstNonFavIdx=9)之前
4. ✅ 方案对比：2个对比表 + 3个 section（伤害总览对比、各技能独立伤害贡献对比、各方案技能构成）
5. ✅ 方案区域技能不折叠：22 个 chip，无折叠按钮，无 skill-mode 按钮，统一 LvX/Cap 格式
6. ✅ 方案追加技能：openAppendFromPlan 打开 modal，147 个候选，有 appendAndSearch 按钮
7. ✅ 武器技能留空自动匹配：series/combo select 留空时，_lastAutoWeapon='巨戟龙的默示录'
8. ✅ UI 美化：渐变背景、fadeIn 动画、damage-big 发光、表格圆角、best-row 高亮

## UI设计规范
- 暗色主题：--bg #161922, --card #1e2430, --accent #7c9cff（2026-07-31 更新配色）
- 圆角卡片 border-radius: 14px，card 使用渐变背景 var(--card-grad)
- 微动画：hover上浮、颜色过渡、tab切换 fadeIn 动画
- CSS变量统一管理颜色（含 --border-soft, --accent-glow, --shadow-glow）
- 横向标签页切换功能区域
- 技能选择：点击展开等级，再点取消
- 收藏置顶：所有技能列表中收藏的技能排在最前
- damage-big 伤害数字使用三色渐变 + drop-shadow 发光
- detail-table 圆角边框 + best-row 绿色高亮
- header 底部发光线 (::after 伪元素)

## localStorage 持久化
- `mhw_fav`: 收藏技能
- `mhw_dis`: 禁用技能
- `mhw_wdis`: 禁用武器技能
- `mhw_plans`: 保存的方案

## 用户新需求（2026-07-31 晚间）
1. **武器技能禁用**：武器技能除了"留空·自动匹配最优"外，还需要一个"禁用"选项，表示该武器技能槽位没有任何技能
2. **管理技能库功能澄清**：用户表示不理解"管理技能库"功能是干嘛的，需要检查并优化该功能的用途和交互
3. **系列/组合技能显示逻辑统一**：技能选择区以激活等级显示，方案展示区以件数形式显示，需要统一显示逻辑
4. **方案追加技能改为一级菜单**：不要二级菜单，在方案技能区直接选择技能，然后点击追加技能按钮追加
5. **查询方案主操作区域位置**：查询方案的主操作区域应该在查询方案这一栏里面，跟追加模式、对比模式一样
6. **查询追加模式只查询部分技能**：查询追加模式内部代码有问题，只查询了部分技能，需要修复
7. **方案对比功能增强**：已保存方案不该在二级菜单；不止需要技能对比，还需要更重要的伤害计算过程对比，把参与伤害计算的各个数值列出来

## 重要修复记录（续）
- 详情 Modal 和方案对比的 detail_calc 调用改为使用方案完整技能，而非用户选择技能
- fast_search_v3.py 数据路径从硬编码 /workspace 改为相对路径
- calc_v8_final.py 系列技能验证打印字符改为 [OK]/[NO] 避免 Windows GBK 控制台编码错误

## 2026-08-01 修复（流式追加查询 + 方案追加交互 + 预留孔位）

### 根因诊断
1. **追加查询报错 `upstream r...`**：反向代理超时。query_extra 对 147 个技能逐个搜索，累计耗时长，代理切断连接返回错误页，前端 `r.json()` 解析失败。
2. **保存方案/对比报错**：runCompare 用 Promise.all 并发调 detail_calc，单个失败拖垮全部；localStorage 可能超配额。
3. **预留孔位丢失**：min_rem_armor 被硬编码为 0，控件缺失。
4. **方案追加交互不符意图**：底部独立大区域列出所有未选技能，用户想直接点方案技能 chip。

### 修复内容
- **fast_search_v3.py**: `query_extra` → `query_extra_stream` 生成器，yield start/progress/done，每个技能完成即推送进度。
- **gui_server.py**: `_handle_query_extra` 改为流式 NDJSON 响应（`Content-Type: application/x-ndjson` + `X-Accel-Buffering: no`），逐行 flush，保持连接活跃避免代理超时。
- **index.html API 函数**: 改为 async，先 `r.text()` 再 `JSON.parse`，非 JSON 响应截取前 80 字符抛友好错误（不再出现 "Unexpected token" 不可读报错）。
- **index.html runExtraQuery**: 用 `fetch + response.body.getReader()` 流式读取 NDJSON，进度条按 `done/total*100%` 真实增长，状态文字实时显示当前技能；增量收集结果避免 done 前空白。
- **index.html 方案追加**: 移除底部 `plan-append-levels` 大区域；方案技能 chip 可点击 toggle 选中态（chip-unselected↔chip-append 橙色）；追加栏新增"➕ 追加"按钮（只追加不搜索）+ "🔍 追加并搜索"。
- **index.html 预留孔位**: 搜索栏新增"预留孔位（防具空孔等级和）"输入框，runSearch 与 runExtraQuery 读取并传 min_rem_armor。
- **index.html 保存方案**: saveState 返回 boolean，配额超限时回滚并提示；runCompare 用 Promise.allSettled 容错，单个失败跳过。

### 验证结果（2026-08-01 curl 全通过）
1. ✅ /api/info: 154 技能, 9 分类
2. ✅ /api/custom_search: 自动匹配"巨戟龙的默示录", 伤害 878.03, 0.83s
3. ✅ /api/query_extra 流式: start(total=147) → 147×progress → done(upgrade=2,extra=145)，149 行 NDJSON
4. ✅ /api/detail_calc: base 756.8 → final 796.5, 6 技能明细
5. ✅ 预留孔位 min_rem_armor=3: 方案剩余防具孔 [1,1,1] 总和=3 生效
6. ✅ node --check index.html 内嵌 JS 语法通过

### 优化内容
1. **calc_damage / calc_weighted_crit 缓存**：添加 `@functools.lru_cache(maxsize=8192)`，将技能dict转为sorted tuple作为cache key。实测缓存命中时1.4M calls/s，未命中时673K calls/s，大幅减少DFS中的重复计算。
2. **移除死代码**：fast_search_v3.py 第1735-1736行存在被第1737行覆盖的冗余slot计数代码，已清理。
3. **Windows GBK编码修复**：calc_v8_final.py 添加 `sys.stdout.reconfigure(encoding='utf-8')`，避免控制台打印中文字符时出现编码错误。

### 性能基准
- calc_damage 缓存命中: ~1,400,000 calls/s
- calc_damage 缓存未命中: ~670,000 calls/s
- 单方案搜索（6方案全搜）: ~5.7s（含详细伤害计算输出）

## 2026-08-03 工作进度

### 已完成
1. **技能组管理增强**：替换下拉框为列表，支持重命名（行内编辑）、上移/下移排序、删除
2. **追加查询搜索修正**：`appendAndSearch()` 改为调用 `runExtraQuery()` 而非 `runSearch()`，保持在追加模式内
3. **已保存方案再次查询**：`loadSavedPlan()` 增加再次查询区域，默认包含全部已激活技能（系列/组合技能仅含 `ok: true`），点击 chip 可取消，支持重置
4. **乘区显示优化**：参数名改为中文（霸主乘区、巨戟乘区、因祸得福期望、攻守发动），数值为1时自动隐藏
5. **术语规范化**：明确"套装技能" = 系列技能 + 组合技能（NO_DECO_SK）
6. **技能组背景框**：`.skill-group-item` 添加背景、边框、圆角样式
7. **预留孔位UI重写**：从搜索区移除 `min-rem-armor`，改在技能选择区底部显示防具/武器孔位预留（Lv1/Lv2/Lv3 各 0-6 个）。后端新增 `min_rem_weapon` 参数，`fill_slots` 和 `_check_deco_feasible` 增加武器孔最小保留检查
8. **武器技能显示修复**：
   - `_plan_result_to_dict` 返回 `weapon_skill` 字段
   - `renderResultCard` 新增武器技能卡片
   - 套装技能显示增加武器贡献 breakdown（如 `1防+1武/4件`）
9. **剩余孔位显示改为数量制**：显示孔位个数及各级别分布（如 `4个 (Lv1×2 Lv2×1 Lv3×1)`）
10. **自动匹配武器技能等级修复**：`dfs_search_auto_weapon` 返回 `(results, skill_name, skill_lv)` 三元组，`_handle_custom_search` 使用正确等级而非硬编码 +1

### 待验证
- 技能组管理 UI 交互（编辑、移动、删除）
- 已保存方案再次查询功能
- 追加并重新搜索切换为追加查询模式
- 预留孔位新 UI 及后端 weapon 保留逻辑
- 武器技能显示及套装技能 breakdown

### 2026-08-03 晚间修复（第三轮）
1. **系列技能显示简化**：移除武器贡献显示，只显示防具件数（如 `2件/4件` 而非 `1防+1武/4件`）
2. **剩余孔位零级隐藏**：`remainingHTML` 中只显示数量 > 0 的孔位等级（如 `Lv1×3 Lv2×1`，不显示 `Lv3×0`）
3. **系列技能 UI 优化**：`.chip-series-inactive` 文字颜色改为 `#c0c7d2`，灰度降低到 0.2，保持文字清晰可读；激活状态保持 `chip-selected` 蓝色边框高亮
4. **技能组加载后 focus 修复**：`loadSkillGroup()` 末尾调用 `document.activeElement?.blur()`，解决加载技能组后 select 无法打开的问题
5. **武器技能显示过滤**：`_plan_result_to_dict` 中 `weapon_skill_info` 只保留 `NO_DECO_SK` 技能，避免显示 利刃、格挡性能 等非套装技能

### 2026-08-03 晚间修复（第四轮）
6. **系列技能激活状态修复**：`_plan_result_to_dict` 中 `need_p` 计算改为 `4 if v >= 4 else (3 if k in GROUP_SK else 2)`，与后端搜索逻辑一致；`series_actual` 改为仅统计防具件数（排除护石）
7. **追加模式孔位显示增强**：基线框显示防具孔/武器孔各级别数量，上限6个
8. **方案对比面板武器自定义**：新增武器 DIY 区域（基础攻击/会心率/属性值/常驻攻击），支持点击"重新计算"更新伤害；新增 `/api/weapon_diy` 后端接口，线程安全地临时修改武器参数并计算伤害
9. **移除方案对比"各技能等级"表格**：与"各技能独立伤害贡献对比"重复，已删除

### 2026-08-03 晚间修复（第五轮）
10. **武器技能区完全不显示修复**：移除前端 `weaponSkillHTML` 中对 `core_skills/other_skills` 的过滤逻辑，恢复显示所有 weapon_skill；在 `renderResultCard` 模板中插入 `weaponSkillCard` 独立卡片区域
11. **系列技能激活状态修复**：`series_actual` 改为统计防具 + 护石的系列技能件数，与后端 `verify_series` 逻辑一致
12. **武器自定义激化系统**：
    - 新增武器类型选择（12种武器）
    - 新增激化类型选择（无/攻击激化/会心激化/属性激化）
    - 激化效果按武器类型区分：普通武器（攻击+10/-15会心/会心+10/-10攻击/-10锋利度/-属性/属性+X）；笛子/铳枪特殊（攻击+3/会心+2/属性+8）
    - 属性激化各武器加成：大剑+5、太刀+5、片手+4、双刀+3、大锤+4、长枪+5、斩斧+4、盾斧+5、虫棍+4、弓+3
    - 后端 `/api/weapon_diy` 支持 `weapon_type`、`augmentation`、`affixes` 参数
13. **武器自定义复原强化词条**：
    - 新增词条类型选择（攻击/会心/锋利度/属性）
    - 等级限制：攻击Lv1-4(+5/+6/+9/+12)、会心Lv1-4(+5/+6/+8/+10)、锋利度Lv1-2(+30/+50)、属性Lv1-3(+30/+50/+80)
    - 限制规则：最多5条词条，同类型最多2条
    - 锋利度为显示用属性，不参与伤害计算
 14. **武器面板计算后展示**：weapon_diy 返回 `weapon_stats` 包含 atk/crt/ele/sharp，前端结果区显示计算后武器面板

### 2026-08-03 晚间修复（第六轮）
 15. **武器技能数量限制修复**：
     - 前端 `runSearch` 新增 `weapon_series_skill` 和 `weapon_combo_skill` 参数，明确告诉后端哪个技能装备在武器上
     - 后端 `_handle_custom_search` 使用这两个参数填充 `weapon_skills_dict`，不再把 combo_skills 中的所有技能都当作武器技能
     - 武器最多出 1 个系列技能 + 1 个组合技能，符合游戏机制
 16. **系列技能件数显示修复**：前端方案卡片中系列技能显示改用 `actual_pieces`（防具+护石+武器），不再只用 `armor_pieces`
 17. **weapon_diy 记忆功能**：使用 `localStorage` 记忆上一次的 weapon_diy 设置（武器类型、激化、基础属性、词条等），刷新页面后自动恢复
 18. **自动插珠算法优化**：`fill_slots` 中 armor_fixed 填充从按技能逐个贪心改为全局贪心，每次迭代评估所有 `(孔位, 珠子)` 组合对所有赤字技能的总收益

### 2026-08-03 晚间修复（第七轮）
 19. **系列/组合技能显示统一**：
     - 技能选择区保持等级显示（每个等级=2件装备）
     - 方案技能区改为件数显示：系列技能显示为 `LvX/Y件`（X=当前等级，Y=需要件数），组合技能显示为 `?/3件`
     - 激活状态用蓝色高亮（`chip-selected`），未激活用灰色虚线（`chip-series-inactive`）
 20. **weapon_skills_dict 等级来源修复**：`gui_server.py:548-553` 武器技能等级改为从 `combo_skills` 取，不再错误地从 `fixed_skills` 取
 21. **detail_calc 支持 weapon_diy 参数**：`_handle_detail_calc` 新增 weapon 参数处理（激化、词条、属性修正），方案对比面板数值可跟随 weapon_diy 自定义参数变动
 22. **前端 runCompare 传递 weapon_diy**：`runCompare` 中通过 `getWeaponDIYParams()` 读取 weapon_diy 设置并传递给 `/api/detail_calc`

## 2026-08-08 查询模式性能优化 & 用户技能组0方案排查

### 验收标准（用户明确）
- 技能组：耳塞2、精神抖擞3、缓冲1、快吃3、利刃3、毅力【果断】(=组合技能霸主之魂Lv3)、格挡性能3、Lv1插槽3、连击5、挑战者5、广域化5、防御3
- **要求：1秒以内出满100方案**（参考站 mhwilds.wiki-db.com/sim 同组合0.4秒出200方案）

### 关键术语澄清（用户纠正）
- **毅力【果断】不是护石**，是 `skills_data.json` 中 `/组合技能/霸主之魂/技能名` 的显示名；查询时 combo={'霸主之魂':3}

### 已完成的性能优化（fast_search_v3.py）
1. **叶子记录优化**：`_try_fill_and_record(incremental=True)`——DFS叶子先跑 `_strict_leaf_check()`（增量状态严格可行性检查，严格度对齐 `_check_deco_feasible`），通过后才重建数据；增量维护全技能字典 `_cur_all_skills` 避免每叶子从6件装备重建
2. **incremental 双路径**：正常叶子用增量；`_try_early_fill` 补位路径必须 `incremental=False`（临时填充不更新增量状态）
3. **fill_slots 美化评分外提**：每轮对珠子评分一次，不再每槽位×每珠子重复
4. **效果**：最坏基准（全树搜索）33.2s→14.3s（约2.3倍），结果一致（20方案/最高分93.5）；追加模式回归通过

### 已修复的搜索bug
- **Bug1 系列优先路径无条件return**：`_sf_bt` 枚举不完备（`_tried_ns` 每部位只试一个非系列代表件 + Top2000截断），不能无条件返回，收不满 max_results 时必须回退常规DFS补全
- **Bug2 回退DFS丢弃快速路径结果**：新增 `_sf_keys`/`_sf_results`，`_dfs(0)` 结束后按装备指纹（name+part_idx）去重合并

### ⚠️ 0方案问题排查结论（重要）
1. **WSLOTS 默认就是 [3,3,3]**（fast_search_v3.py 第73行），武器槽不是缺省项
2. **利刃/格挡性能是 WEAPON_SK**：`armors_cn.json` 中无任何防具含这两个技能（已验证），只能靠Lv3武器珠；利刃珠/格挡性能珠存在 +3 版本（Lv3槽）
3. **decos_cn.json 数据已核实无误**（2026-08-08 与 gamewith 荒野装饰品图鉴逐条比对）：共361珠（weapon 295 + armor 66）。《荒野》防具珠本来就只有单档+1（防音珠【2】/浑身珠【2】/快吃珠【1】/挑战珠【3】/友爱珠【1】/防御珠【1】/连击珠【3】/缓冲珠【1】均无更高版本）；Ⅱ/Ⅲ高阶版只存在于武器珠。→ 早期“数据偏斜导致0方案”的结论是**错误的**，已推翻
4. **实测**：武器槽3/6/8个Lv3都是0方案；插桩显示 DFS 能到达叶子（_check_deco_feasible 调用32840次）。早期“可行性检查误杀”的怀疑已部分推翻：抓到的被拒叶子多数确实无解（如某快照仅8个Lv1槽但需求8个Lv1珠+3个预留）
5. **参考站真相**（Browser agent 实测）：mhwilds.wiki-db.com/sim 是日站 wiki-db 的 JS 模拟器；默认配置下该完整技能组也是0方案（0.015s返回）；其装饰珠数据存于用户 localStorage `mhwilds_deco` 由用户自行导入——用户看到的"0.4秒200方案"依赖其浏览器本地导入的完整珠子数据
6. **gamewith 图鉴比对**：防音珠【2】/浑身珠【2】/早食珠【1】/挑战珠【3】/友爱珠【1】/防御珠【1】/连击珠【3】/缓冲珠【1】均只有单档Lv1效果，与我方数据一致——《荒野》防具珠本来就只有单档+1，Ⅱ/Ⅲ高阶版只存在于武器珠

### 2026-08-08 晚间修复（真实bug，已修）
**Bug：fill_slots 贪心插珠侵占预留槽位**：赤字贪心按槽位升序扫描，会把广域珠/缓冲珠等Lv1珠插进被「Lv1插槽×3」预留的槽位，导致最终预留校验失败返回 None（快照实证：缺挑战+1/连击+2、有8个Lv3空槽，本可解却被拒）。修复：fill_slots 开头把孔位技能需求（LvN插槽/分侧预留）的槽位物理隔离到 reserved_a/reserved_w，贪心/美化插珠不可触碰，最终校验与返回值重新并回；min_keep 相应只留通用预留。
**Bug：预检查预留槽重复计数**：_check_deco_feasible/_greedy_deco_check_with_future/_strict_leaf_check 的珠子降级链检查把预留槽也计入可用容量（偏松）。修复：三处均在槽位需求验证后扣减预留槽（优先从武器侧扣，低阶槽更稀缺留给珠子），与 fill_slots 隔离方向一致。

### ⏳ 未完成（下次继续）
1. **修复后该技能组仍0方案**（库直调与真实API路径均0）。系列优先路径中996个通过预检的快照全部来自「假想最强护石」上界预检（_super_cur），真实护石组合×1901防具组合无一通过预检 → 怀疑该技能组在我方数据下真的不可行（或解空间极小）
2. **待跑的独立暴力验证**（不依赖搜索引擎，判定数据层SAT性）：逐部位合并候选状态（技能向量截断到需求值+霸主件数+槽位计数，dict去重+剩余部位上界剪枝）→ 得到的防具状态×31去重护石，用回溯珠子求解器（武器侧_fill_weapon_slots_smart + 防具侧递归分配，含Lv1插槽×3预留）判定。若确无解→需与用户确认其浏览器里看到方案时的具体配置（武器槽/预留/护石）或数据差异；若有解→说明搜索引擎仍有剪枝误杀
3. 系列优先路径的 _tried_ns 单代表件+Top2000截断依旧是不完备枚举（已回退常规DFS兑底，非阻塞项）
4. 服务已用新代码重启于 http://localhost:8766；临时诊断文件已全部清理（含 _brute.py，重跑时按上述思路重写）
`(candidates, all_skill_names, _cached_weapon_skills, armor_fixed, _cached_weapon_fixed, best_by_part, best_slot_by_part, candidates_by_part, part_series_availability)`——candidates_by_part 在索引7

### 测试注意事项
- 直接测库时 `fs.WSLOTS` 是模块级全局变量，测完要还原；gui_server 的 `_apply_weapon_slots` 按请求设置
- Windows 控制台 GBK：诊断脚本需 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')`
- 服务运行于 http://localhost:8766，代码改动后必须重启才生效