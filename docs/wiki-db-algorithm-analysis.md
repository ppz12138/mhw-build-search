# 网页配装器 (wiki-db) 追加/基础搜索算法逆向分析

> 来源：https://mhwilds.wiki-db.com/sim/  
> 文件：`bzlcompiled/sim-compiled-ja.js?h=20260808T064025`  
> 分析时间：2026-08-12

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
| `k.R` | 候选索引数组（等价于 `k.G`） |

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
// b 是否被 c 支配（b 在所有维度 <= c，且至少一个维度 <）
function Vd(a, b, c) {
  if (b.j > c.j || b.m != c.m || b.a && c.a && (b.a[0] > c.a[0] || b.a[1] > c.a[1] || b.a[2] > c.a[2]))
    return !1;  // b 在孔位/部位上更好，c 不支配 b
  a = q(a);  // 遍历所有追踪技能
  for (var d = a.next(); !d.done; d = a.next())
    if (d = d.value, (b.i[d] || 0) > (c.i[d] || 0))
      return !1;  // b 在某技能上更好，c 不支配 b
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
      M = Ud(k.a);  // 已放置装备的总技能点数
      if (!(A <= M)) { P.a = 2; break }  // 当前分不够，跳过
      Fa = wf(k, I);  // 计算当前技能覆盖分
      gb = !1;
      Fa <= M && (Ia = k.Rb(k.a), qa = Ra(k.ec, k, a, Ia), gb = Ia.b(I, qa));
      if (!gb) { P.a = 2; break }  // 无法构建，跳过
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

`D.P.f` 是候选的评分因子，`Pa` 是空槽数，`M` 是已放置总分，`A` 是目标分。  
如果 `候选贡献 × 空槽 + 已得分 < 目标分`，直接剪掉。

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

5. **减少函数调用开销**
   - `_greedy_deco_check_with_future` 在 depth < 6 的几乎所有节点都被调用
   - 可以降低调用频率，或替换为更轻量的检查

### 低优先级

6. **结果上限**
   - 网页版硬上限 200，我们可以考虑在 `query_extra_stream` 中也加硬上限

7. **并行搜索**
   - 网页版单线程，但 Python 可以用 `multiprocessing` 并行搜索不同候选起点

---

## 7. 网页版未开源，以上为逆向分析

如有需要，可以进一步：
1. 对比具体搜索案例的候选顺序
2. 提取技能权重表 `he`
3. 分析 `k.G` 的排序逻辑
