# 网页配装器 (wiki-db) 追加/基础搜索算法逆向分析

> 来源：https://mhwilds.wiki-db.com/sim/  
> 文件：`bzlcompiled/sim-compiled-ja.js?h=20260808T064025`（2026-08-13 重新抓取复核，版本 hash 未变）  
> 原始文件（已保存）：`docs/source/sim-compiled-ja.js`（338KB）、`docs/source/sim-compiled-ja.js.es6.js`（318KB）  
> 分析时间：2026-08-12 / 2026-08-13 复核更新

---

## 1. 核心数据结构

| 字段 | 含义 |
|------|------|
| `a.a[7]` | 7个装备槽位（头、身、腕、腰、脚、护石、武器），`null` 表示空 |
| `a.b` | 全候选装备列表（防具 + 护石 + 武器） |
| `k.G` | 预排序的候选索引数组，搜索按此顺序遍历 |
| `a` | 当前已放置的装备数组（长度7） |
| `u` | 结果容器，每个元素是一个结果组 |
| `k.L` | 结果数量上限（默认 200） |
| `k.ec` | 候选生成/过滤函数 |
| `k.R` | **武器候选数组**（`xf()` 中 `a.R = b[1]`，`f()` 补位搜索按此遍历） |

---

## 2. 关键函数

### 2.1 技能评分

```javascript
// 单技能加权分：权重 he[a] 默认 100
function ge(a, b) { return 0 < b ? (he[a] || 100) * b : 0 }

// 一套装备的总技能分
function ie(a) {
  var b = 0, c;
  for (c in a) b += ge(c, a[c]);
  return b
}
```

**要点**：每个技能有权重 `he[skill_name]`，默认 100。评分 = 权重 × 技能等级。

### 2.2 当前配装总分

```javascript
// 当前已放置装备的总覆盖分
function wf(a, b) {
  var c = 0;
  a = q(a.b);  // 遍历所有候选
  for (var d = a.next(); !d.done; d = a.next())
    d = d.value, c += ge(d, b[d]);  // 累加每个候选的技能贡献
  return c = Math.round(1E3 * c) / 1E3  // 保留3位小数
}
```

**要点**：`wf(k, current_skills)` 计算当前已选装备对 `current_skills` 的加权覆盖总分。

### 2.3 剩余需求

```javascript
function vf(a) {
  for (var b = a.a.map(function(l) {
    return l ? l.o ? a.a[1] ? a.a[1].i : null : l.i : null
  }).filter(function(l) { return !! l }), c = {}, d = q(a.b), e = d.next(); !e.done; e = d.next()) {
    e = e.value;
    for (var f = a.f[e], h = q(b), k = h.next(); !k.done; k = h.next())
      f -= k.value[e] || 0;
    c[e] = f
  }
  return c
}
```

**要点**：`vf(k)` 返回当前还缺多少技能点。`a.f` 是需求技能字典，`b` 是已装备的技能贡献，`c` = 需求 - 已提供 = 剩余需求。

### 2.4 支配检查（ domination ）

```javascript
// b 是否被 c 支配：返回 true = c 支配 b（b 在孔位/技能/部位上不优于 c）
function Vd(a, b, c) {  // 实际位于 sim-compiled-ja.js 第81行
  if (b.j > c.j || b.m != c.m || b.a && c.a && (b.a[0] > c.a[0] || b.a[1] > c.a[1] || b.a[2] > c.a[2]))
    return !1;  // b 孔位更大 / 部位不同 / 孔位更宽 → b 优于 c，c 不支配 b
  a = q(a);  // 遍历所有追踪技能
  for (var d = a.next(); !d.done; d = a.next())
    if (d = d.value, (b.i[d] || 0) > (c.i[d] || 0))
      return !1;  // b 在某技能上等级更高 → c 不支配 b
  return !0  // c 支配 b
}

// 在结果集 u 中查找是否存在结果 dominating b
function jf(a, b) {
  a = q(a.a);
  for (var c = a.next(); !c.done; c = a.next())
    if (c = c.value.find(b)) return c;  // 找到支配者
  return null
}
```

**要点**：
- `Vd` 检查一个装备是否被另一个支配（孔位更差、技能更少）
- `jf(u, hb)` 在已有结果中查找是否有结果支配当前候选 `hb`
- **这是搜索过程中实时剪枝的关键**

### 2.5 空槽计数

```javascript
function Cf(a) {
  for (var b = 0, c = 0; 6 > c; c++)
    (!a[c] || a[c].o && !a[1]) && b++;
  return b
}
```

**要点**：`Cf(k.a)` 计算当前还有多少个空槽位。

### 2.6 搜索核心（生成器版回溯）

```javascript
function e(w, z, I, A) {
  // w = 当前候选起始索引, z = 候选总数, I = 当前技能, A = 当前技能分
  return za(e, function(P) {
    switch (P.a) {
    case 1:
      oa = !1;
      if (a.length >= k.L) return P.return();  // 结果够200个就停
      M = Ud(k.a);  // 已放置装备的孔位数之和（见 2.9）
      if (!(A <= M)) { P.a = 2; break }  // 目标分 > 孔位容量 → 进入搜索
      Fa = wf(k, I);  // 计算当前技能覆盖分
      gb = !1;
      Fa <= M && (Ia = k.Rb(k.a), qa = Ra(k.ec, k, a, Ia), gb = Ia.b(I, qa));  // 技能覆盖分 ≤ 孔位容量时，尝试用珠子补满剩余需求（见 2.11）
      if (!gb) { P.a = 2; break }  // 珠子补不满，无法提前构建，进入搜索
      return sa(P, !0, 4);
    case 4:
      return P.return();
    case 2:
      if (w >= z || !cb(k.a, null)) return P.return();  // 候选遍历完或槽满
      Pa = Cf(k.a);  // 空槽数
      Ad = null; k.eb && (Ad = k.Lb());  // 排除过滤器
      u.a.unshift([]);  // 新建结果组
      D = {}; D.ma = w;  // 从 w 开始遍历
    case 5:
      if (!(D.ma < z)) { P.a = 7; break }
      D.P = k.G[D.ma];  // 取第 D.ma 个候选
      if (k.a[D.P.m]) { P.a = 6; break }  // 该槽已满
      if (k.eb && !Ad(D.P)) { P.a = 7; break }  // 被排除
      // ★ 核心剪枝：评分估算 < 目标评分则跳过
      if (Math.round(12 * (D.P.f * Pa + M)) < Math.round(12 * A)) { P.a = 7; break }
      hb = Sa(Vd, k.b, D.P);  // 绑定 domination 检查函数
      if (lh = jf(u, hb)) { P.a = 6; break }  // ★ 被已有结果支配，跳过
      k.a[D.P.m] = D.P;  // 放置
      ib = !1;
      D.Ja = vf(k);  // 剩余需求
      D.ya = wf(k, D.Ja);  // 新评分
      // 如果评分已达标且无约束违规，尝试提前记录
      if (!(D.ya < A || 0 < D.P.j || D.P.b)) { P.a = 8; break }
      // ★ 递归搜索剩余槽位
      mf = function(jb) { ... }(D)();
      lc = void 0;
    case 9:
      lc = mf.next();
      ib = ib || lc.value;
      if (!(0 <= h && Ta() - n > h)) { P.a = 11; break }  // 超时检查
      return sa(P, ib || oa, 11);
    case 11:
      if (!lc.done) { P.a = 9; break }  // 继续递归
    case 8:
      ib || u.a[0].push(D.P), k.a[D.P.m] = null, oa = oa || ib;  // 撤销
    case 6:
      D = { P: D.P, ma: D.ma, ya: D.ya, Ja: D.Ja }; D.ma++; P.a = 5; break;  // 下一个候选
    case 7:
      return u.a.shift(), sa(P, oa, 0);  // 返回结果
    }}})}
```

### 2.7 补位搜索（函数 f）

```javascript
function f(w, z, I) {
  // w = 起始索引, z = 结束索引, I = 目标评分
  return za(f, function(qa) {
    switch (qa.a) {
    case 1:
      A = !1, oa = k.R, M = {}, M.ra = w;
    case 2:
      if (!(M.ra < oa.length)) { qa.a = 4; break }
      Fa = oa[M.ra];
      if (k.a[Fa.m]) { qa.a = 3; break }  // 槽已满
      k.a[Fa.m] = Fa;  // 放置
      M.Ka = vf(k);
      M.za = wf(k, M.Ka);
      if (M.za == I && !k.a[1].j) { k.a[Fa.m] = null; qa.a = 4; break }  // 评分刚好且不需要护石
      // ★ 递归：先搜后续候选 f()，再搜主搜索 e()
      gb = function(Pa) { ... }(M)();
      Ia = void 0;
    case 6:
      Ia = gb.next();
      A = !(!A && !Ia.value);
      if (!(0 <= h && Ta() - n > h)) { qa.a = 8; break }  // 超时
      return sa(qa, A, 8);
    case 8:
      if (!Ia.done) { qa.a = 6; break }
      k.a[Fa.m] = null;  // 撤销
    case 3:
      M = { Ka: M.Ka, za: M.za, ra: M.ra }; M.ra++; qa.a = 2; break;
    case 4:
      return sa(qa, A, 0)
    }}})}
```

### 2.8 入口点（分块执行）

```javascript
// 入口
var h = 200;  // 结果上限
Af(this);
var k = this,
    l = vf(this),        // 剩余需求
    m = wf(this, l),     // 当前评分
    n = Ta(),            // 开始时间
    p = function z() {   // 生成器链
      var I;
      return za(z, function(A) {
        return 1 == A.a
          ? ((I = !!k.a[1]) ? A = ta(A, f(0, 0, m), 2) : (A.a = 2, A = void 0), A)
          : ta(A, e(0, k.G.length, l, m), 0)
      })
    }(),
    u = new hf;  // 结果容器

d();  // 启动分块执行

// 分块执行器：每次跑一段生成器，然后 setTimeout 让出主线程
function d() {
  do var w = p.next().done || a.length >= k.L;
  while (!(w || 0 <= h && Ta() - n > h));
  w = b(w ? 100 : Math.floor(a.length / k.L * 100), a) || w;  // 更新进度条
  n = Ta();
  w || setTimeout(d, 0)  // 继续下一块
}
```

### 2.9 装备数据模型（第 73-80 行，2026-08-13 复核补充）

模拟器内有多种装备类，字段语义此前未完全厘清：

| 类 | 用途 | 关键字段 |
|----|------|---------|
| `Fd` | **普通防具/护石** | `m`=部位(0-5/6)、`j`=**孔位数**、`a`=孔位数组`[s1,s2,s3]`、`i`=技能`{名:级}`、`A`=防御、`R`=5维系列标记数组、`o`=是否含"胴系统倍化/倍加"技能、`f`=**评分因子(默认0)** |
| `Hd` | 内置套装（活动装备，如"ホープマスク"） | 同 `Fd`，但 `f`=数据预置评分因子（如 24）、`o`=1 |
| `Nd` | **套装聚合**（`Pd` 合并同技能多件装备） | 继承首件装备的 `f`，`G`=原装备组 |
| `Dd` | **槽位搜索虚拟装备**（如"LV2スロット頭"） | `f`=**槽位等级**(1/2/3)，`c`=槽位大小计数`{1,2,3,4,99}` |
| `Ed` | 武器（ガンナー/剣士） | `f`=0 |
| `Rd` | 武器槽（"武器スロなし"） | `f`=0 |
| `Zd` | **升级版装备**（孔位+1，名字带"+"） | 孔位数=`a.filter(c=>c>0).length`，`f`/`j` 继承 |

关键点：
- `Ud(k.a)` 累加的是已放置装备的 **`j`（孔位数）**，用于 case 1 的 `A <= M` 判断（目标分 ≤ 孔位容量时认为可直接用珠子补满 → 尝试提前构建结果）。
- 评分因子 `f`：普通防具为 0，槽位装备=槽位等级，套装聚合/内置套装取首件或数据预置值。

### 2.10 候选构建 `xf` / `Pd` / `Qb`（第 78、548 行，2026-08-13 复核补充）

```javascript
function xf(a) {
  a.o = a.c.tb(Ra(a.Ea, a));                  // 候选生成
  a.A = 0; Va(a.o, function(c){ this.A += c }, a);  // 候选总数
  var b = Ra(0 < a.M ? a.mc : a.Ea, a);
  b = a.c.ub(a.U, b, a.ba);                    // 数据源查询
  a.G = b[0];                                  // ★ 主候选数组（防具/护石）
  a.eb || (a.G = Pd(a.G, a.b));                // 套装聚合
  a.R = b[1];                                  // ★ 武器候选数组（f() 遍历）
  a.ta && (a.G = a.Qb(a.G))                    // 快速模式：候选层支配过滤
}
```

- **`Pd`（套装聚合）**：扫描候选，把 `f` 相同且 `Od`（同部位、同孔位、技能完全相同）的装备从数组移除并合并为一个 `Nd` 对象，**大幅减少候选数量**。
- **`Qb`（候选层支配过滤）**：用 `Vd` 剔除"被更优候选支配"的装备，仅在快速模式（`ta`）或武器模式下启用。
- 权重表 `he` 由 `g.Ua()` 从 protobuf 数据 `ph.get()` 加载（字段1=技能名、字段2=权重），`qh(a, b)` 对未知技能默认 **100**。

### 2.11 珠子补位检查 `Rb`（第 663 行，2026-08-13 复核补充）

这是 `e()` case 1 中 `Ia.b(I, qa)` 的实现——**搜索中间判断剩余需求能否用珠子补满**，是"提前构建结果/终止分支"的核心：

```javascript
g.Rb = function(a) {                 // a = k.a（当前 7 槽）
  ...
  b.prototype.b = function(e, f) {   // e = 剩余需求 {技能:点}
    var h = [], k = [0,0,0,0];       // h=所需珠子, k=占用槽位统计
    for (p in e) {
      var l = e[p];
      if (!(0 >= l)) {
        var m = He(p);               // He: 技能名 → 珠子对象（Ge 索引）
        if (!m || l > qh(c.c, m.name)) return !1;  // 无对应珠子/超权重上限
        for (var n = 0; n < l; n++) h.push(m);     // 每个技能点 1 颗珠子
        k[m.b] += l;                 // 按珠子槽位大小累计
      }
    }
    e = d[1]-k[1]; var p = d[2]-k[2]; k = d[3]-k[3];
    0 > e && (p += e); 0 > p && (k += p);
    if (0 > k) return !1;            // 槽位容量溢出 → 无法补满
    if (c.j) ...                      // 固定珠子处理
    f(h); return !0
  }
};
```

相关支撑：`He`（技能→珠子，`Fe` 由 `Ee` 构建 `Ge` 索引）、`Je`（内置珠子数据）、`kf`（珠子按类型分组计数）、`Ef`（装备槽位容量统计 `[1,2,3,4]`）。

---

## 3. 算法流程总结

```
入口
  │
  ├─ 如果需要护石槽位 → 先跑 f(0, 0, m) 补位
  │     └─ f(): 遍历候选，逐个尝试放置，递归调用 e() 继续搜索
  │
  └─ 主搜索 e(0, k.G.length, l, m)
        │
        ├─ 遍历候选 (D.ma 从 w 到 z)
        │     │
        │     ├─ 剪枝1: 评分估算 < 目标 → 跳过
        │     ├─ 剪枝2: 被已有结果支配 → 跳过
        │     ├─ 剪枝3: 槽位已满 → 跳过
        │     ├─ 剪枝4: 被排除 → 跳过
        │     │
        │     ├─ 放置候选到空槽
        │     ├─ 计算新评分和剩余需求
        │     │
        │     ├─ 如果评分达标 → 记录结果
        │     │
        │     └─ 递归搜索剩余槽位 (e() 或 f())
        │           │
        │           └─ 生成器 yielding → 回到 d() → setTimeout 让出
        │
        └─ 结果达到 200 个或时间耗尽 → 停止
```

---

## 4. 与 Python 版的核心差异

| 维度 | 网页版 (JS) | Python 版 |
|------|------------|-----------|
| **语言** | JavaScript V8 JIT | CPython 解释器 |
| **搜索框架** | Generator + setTimeout 分块 | 纯递归 DFS |
| **候选排序** | 预计算顺序 `k.G`（按相关性） | 按部位分组，内部无预排序 |
| **剪枝策略** | 评分阈值 + 支配检查 + 空槽计数 | 贪心珠子检查 + 降级链 + 系列件数 |
| **支配检查** | **搜索过程中实时检查** | 仅在叶子节点 `_try_fill_and_record` 中检查 |
| **技能权重** | 有（`he[skill]`，默认100） | 无（所有技能平等） |
| **多技能珠子** | 未明确看到特殊处理 | 按最佳单技能点数估算 |
| **结果上限** | 200（硬上限） | `max_results` 参数 |
| **超时** | 每步检查 `Ta() - n > h` | 每 256 结果检查一次 |

---

## 5. 关键优化发现

### 5.1 支配检查是最大的性能差异

网页版在 **搜索中间过程** 就检查当前部分配装是否被已有结果支配：
```javascript
if (lh = jf(u, hb)) { P.a = 6; break }  // 被支配 → 剪掉整个子树
```

Python 版只在叶子节点做支配检查（`_dominated_check`），这意味着很多中间节点不会被剪掉。

### 5.2 评分阈值剪枝

```javascript
if (Math.round(12 * (D.P.f * Pa + M)) < Math.round(12 * A)) { P.a = 7; break }
```

`D.P.f` 是候选的评分因子，`Pa` 是空槽数，`M` 是已放置装备的**孔位数之和**（`Ud` 累加 `j`），`A` 是目标分（剩余需求的加权覆盖分）。  
如果 `候选贡献 × 空槽 + 已得分 < 目标分`，直接剪掉（`break` 结束整个候选循环，前提是候选已按评分因子排序）。

**评分因子 `f` 的来源**（2026-08-13 复核，见 2.9 装备模型）：
- 普通防具（`Fd`）：`f = 0`，剪枝退化为 `M < A`（孔位容量不足目标分 → 结束）
- 槽位搜索装备（`Dd`）：`f = 槽位等级`（1/2/3），此时 `f × Pa` 才有"每空槽潜力"语义
- 套装聚合（`Nd`）：`f = 首件装备的 f`
- 内置套装（`Hd`）：`f = 数据预置值`（如ホープマスク = 24）

### 5.3 技能权重

网页版对不同技能有不同的权重 `he[skill]`。例如：
- 攻击类技能权重可能更高
- 防御/辅助类技能权重较低

这意味着评分高的配装更可能被优先探索，低分分支被更早剪掉。

### 5.4 分块执行

网页版使用生成器 + `setTimeout` 将搜索分块执行，每块跑几十到几百步后让出主线程。这不是直接的速度优化，但允许：
- 实时更新进度条
- 响应超时检查
- 防止浏览器冻结

---

## 6. 对 Python 版的优化建议

### 高优先级

1. **搜索过程中添加支配检查**
   - 在 `_dfs` 递归的每个节点，检查当前部分配装是否可能被已有叶子结果支配
   - 如果支配，直接剪掉整个子树

2. **引入技能权重**
   - 从网页版数据中提取 `he[skill]` 权重
   - 用加权评分替代简单的 `score = skill_score + slot_sum`

3. **更激进的候选预过滤**
   - 网页版的候选顺序是预计算好的，我们可以在 `_build_candidates` 中也按加权技能覆盖排序
   - 降低 bucket merge 阈值（目前是 >8，可以降到 >3）

### 中优先级

4. **评分阈值剪枝**
   - 在 `_dfs` 的每个节点，计算乐观上界（当前分 + 剩余候选最高分）
   - 如果上界 < 当前最佳结果分，剪枝
   - 注意量纲：参照版左右两侧都对齐到加权分（`SKILL_WEIGHT`），孔位价值需按"可装技能珠"折算，否则剪枝要么永不触发、要么误杀正解

5. **减少函数调用开销**
   - `_greedy_deco_check_with_future` 在 depth < 6 的几乎所有节点都被调用
   - 可以降低调用频率，或替换为更轻量的检查

6. **候选聚合（套装合并）**
   - 参照版 `Pd` 把"同部位、同孔位、技能完全相同"的装备合并成一个候选节点
   - Python 版可按同样规则合并等价防具，直接压缩候选数组规模

7. **候选层支配过滤**
   - 参照版快速模式用 `Qb` 在**搜索前**用 `Vd` 剔除被更优候选支配的装备
   - Python 版目前只在叶子做支配检查，可在 `_build_candidates` 阶段先做一次候选级支配过滤

8. **提取技能权重表 `he`**
   - 权重从 protobuf 数据加载（默认 100），可从 `ph.get()` 中提取真实权重替换 Python 版的无权重评分

### 低优先级

6. **结果上限**
   - 网页版硬上限 200，我们可以考虑在 `query_extra_stream` 中也加硬上限

7. **并行搜索**
   - 网页版单线程，但 Python 可以用 `multiprocessing` 并行搜索不同候选起点

---

## 7. 网页版未开源，以上为逆向分析

原始文件已保存至 `docs/source/`（`sim-compiled-ja.js` / `sim-compiled-ja.js.es6.js`，hash `20260808T064025`，2026-08-13 重新抓取确认版本未更新）。可进一步：
1. 对比具体搜索案例的候选顺序
2. 提取技能权重表 `he`（`g.Ua()` 从 protobuf `ph.get()` 加载，见 2.10）
3. 分析 `k.G` 的排序逻辑（`xf()` 中 `Pd` 聚合 + `Qb` 支配过滤，见 2.10）
4. 提取珠子数据（`Je` 内置珠子表 + `Ge` 索引）用于 Python 版补位检查
