#!/usr/bin/env python3
"""MHWilds 快速配装搜索 v3 — 位掩码+向量化的DFS搜索

核心优化（参照网页配装器策略）：
1. 技能→索引映射，用tuple替代dict做技能累加
2. 候选装备预计算技能向量，消除DFS内的dict.get开销
3. 精确赤字向量，逐技能检查可行性
4. 分数上限剪枝+技能可行性剪枝
5. 技能权重评分系统（对齐网页版 ge()）
"""
import json, time, itertools, sys, os, functools, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA = os.path.dirname(os.path.abspath(__file__))

# ==================== 技能权重（参照网页版 ge()） ====================
# 网页版：ge(a,b) = (he[a] || 100) * b，默认权重100
# 当前使用统一权重100，后续可从网页版数据提取精确权重
SKILL_WEIGHT = 100

# ==================== 技能别名映射 ====================
# 游戏内显示名 → 数据库标准名（前端/用户输入可能用别名）
SKILL_ALIASES = {
    '黑蚀一体': '黑蚀龙之力',
    '黑蚀一体Ⅰ': '黑蚀龙之力',
    '黑蚀一体II': '黑蚀龙之力',
    '黑蚀一体III': '黑蚀龙之力',
    '毅力【果断】': '霸主之魂',
    '毅力果断': '霸主之魂',
}

def _normalize_skill_name(name):
    if name in SKILL_ALIASES:
        return SKILL_ALIASES[name]
    return name

def _normalize_skills_dict(skills):
    if not skills:
        return skills
    return {_normalize_skill_name(k): v for k, v in skills.items()}

# ==================== 技能数值 ====================
MUZ_ATK = {0:0, 1:3, 2:6, 3:10, 4:15, 5:20}
CHAL_ATK = {0:0, 1:4, 2:8, 3:12, 4:16, 5:20}
COUNTER_ATK = {0:0, 1:10, 2:15, 3:25}
ATK_VAL = {0:0, 1:3, 2:5, 3:7, 4:8, 5:9}
ATK_MUL = {0:1.0, 1:1.0, 2:1.0, 3:1.0, 4:1.02, 5:1.04}
CRIT_VAL = {
    '看破': {0:0, 1:4, 2:8, 3:12, 4:16, 5:20},
    '挑战者': {0:0, 1:3, 2:5, 3:7, 4:10, 5:15},
    '力量解放': {0:0, 1:10, 2:20, 3:30, 4:40, 5:50},
    '精神抖擞': {0:0, 1:10, 2:20, 3:30},
    '无我之境': {0:0, 1:3, 2:6, 3:10},
    '弱点特效': {0:0, 1:5, 2:10, 3:15, 4:20, 5:30},
}
SUPER_CRIT = {0:1.25, 1:1.28, 2:1.31, 3:1.34, 4:1.37, 5:1.40}
ELEM_CRIT = {0:1.0, 1:1.05, 2:1.10, 3:1.15}
DRAGON_ELE = {0:(0,1.0), 1:(40,1.0), 2:(50,1.1), 3:(60,1.2)}
OFF_GUARD = {0:1.0, 1:1.05, 2:1.10, 3:1.15}
BURST_ATK = {0:0, 1:8, 2:10, 3:12, 4:15, 5:18}
BURST_ELE = {0:0, 1:60, 2:80, 3:100, 4:120, 5:140}
FORAY_ATK = {0:0, 1:6, 2:8, 3:10, 4:12, 5:15}
FORAY_CRT = {0:0, 1:0, 2:5, 3:10, 4:15, 5:20}
COAL_ELE = {0:1.0, 1:1.05, 2:1.10, 3:1.15}
ABSORB_ELE = {0:0, 1:40, 2:50, 3:60}
ABSORB_COV = 0.50
OGUARD_COV = 0.40
GEKI_MUL = {0:1.0, 1:1.0, 2:1.2, 3:1.2, 4:1.3}
GEKI_ADD = {0:0, 1:0, 2:20, 3:20, 4:40}
FIRE_DRAGON_DMG = {0: 0, 1: 0, 2: 40, 3: 40, 4: 80}
W_ATK = 205 + 24
W_CRT = 5 - 5
W_ELE = 280 + 40 + 160
PERM_ATK = 11  # 护符+6 + 猫饭+5 = 技能加区，不计入面板
BAHAR_MUL = 1.05

# 孔位填充时优先考虑的伤害技能(10倍权重)
DAMAGE_PRIORITY_SKILLS = frozenset([
    '攻击', '超会心', '会心击【属性】', '弱点特效', '看破',
    '挑战者', '连击', '无伤', '攻击守势', '逆袭',
    '精神抖擞', '龙属性攻击强化', '因祸得福', '属性吸收',
    '力量解放', '无我之境', '攻势',
    '火属性攻击强化', '水属性攻击强化', '冰属性攻击强化', '雷属性攻击强化',
])

def _deco_priority_score(deco_skills, fs_cur, caps):
    """计算珠子的优先级得分，伤害技能10倍权重"""
    score = 0
    for sk, pts in deco_skills:
        cap = caps.get(sk, 99)
        cur = fs_cur.get(sk, 0)
        remaining = max(0, cap - cur)
        actual_gain = min(pts, remaining)
        if actual_gain <= 0:
            continue
        if sk in DAMAGE_PRIORITY_SKILLS:
            score += actual_gain * 10
        else:
            score += actual_gain
    return score
WSLOTS = [3, 3, 3]
TMV = 309; TEM = 13.4
WP = 1.32; WE = 1.15
PC_R = WP*0.45*(TMV/100)
EC_R = WE*0.20*(TEM/10)
UR=0.70; URE=0.85; UM=0.50; UKZ=0.60; URK=0.50; UW=0.80; UF=0.90; UCOU=0.40; UCSG=0.30

STATE_CN = {
    'rage':'愤怒','rengeki':'连击','mukizu':'无伤','weak':'弱点','furue':'精神抖擞',
    'oguard':'攻守','counter':'逆袭','rikikai':'力量解放',
    'kuroshoku':'黑蚀','kuroshoku_migo':'黑蚀+无我','none':'无'
}

SKILL_CAPS = {
    '利刃': 3, '格挡性能': 3, '快吃': 3, '减轻胆怯': 3, '缓冲': 1, '耳塞': 3,
    '攻击': 5, '看破': 5, '超会心': 5, '会心击【属性】': 3,
    '龙属性攻击强化': 3, '攻击守势': 3, '无伤': 5, '挑战者': 5,
    '弱点特效': 5, '连击': 5, '力量解放': 5, '精神抖擞': 3,
    '无我之境': 3, '逆袭': 3, '因祸得福': 3, '匠': 5, '攻势': 5,
    '怨恨': 5, '火场怪力': 5, '巧击': 5, '属性吸收': 3,
    '属性变换': 3, '锁刃刺击': 5, '破坏王': 3,
    '广域化': 5, '满足感': 3, '精灵加护': 3, '回避性能': 5,
    '防御': 7, '体术': 5, '跑者': 3, '强化持续': 3,
    '适应环境': 2, '适应水域·油泥': 2, '地质学': 3,
    '火属性攻击强化': 3, '水属性攻击强化': 3,
    '冰属性攻击强化': 3, '雷属性攻击强化': 3,
    '巨戟龙的默示录': 4, '火龙之力': 4, '凶爪龙之力': 4,
    '黑蚀龙之力': 4, '泡狐龙之力': 4, '煌雷龙之力': 4,
    '海龙之涡雷': 4, '冻峰龙的反叛': 4, '锁刃龙的饥饿': 4, '霸主之魂': 3,
}

# 从skills_data.json补充缺失的技能上限
_SKILLS_DATA_FALLBACK = {}

# ==================== 加载数据 ====================
print("加载数据...", end=' ', flush=True)
import os
with open(os.path.join(DATA, 'decos_cn.json'), 'r', encoding='utf-8') as f: decos = json.load(f)
with open(os.path.join(DATA, 'armors_cn.json'), 'r', encoding='utf-8') as f: armors = json.load(f)
with open(os.path.join(DATA, 'my_charms.json'), 'r', encoding='utf-8') as f: my_charms = json.load(f)
with open(os.path.join(DATA, 'charms_cn.json'), 'r', encoding='utf-8') as f: craft_charms = json.load(f)
with open(os.path.join(DATA, 'skills_data.json'), 'r', encoding='utf-8') as f: skills_data = json.load(f)

# 从skills_data.json补充缺失的SKILL_CAPS
for _cat_key in ['武器技能', '防具技能']:
    for _sname, _sinfo in skills_data.get(_cat_key, {}).items():
        _lv = _sinfo.get('max_lv', 0)
        if _lv > 0 and _sname not in SKILL_CAPS:
            SKILL_CAPS[_sname] = _lv
# 系列技能补充缺失的上限（不覆盖硬编码值）：系列技能最多4件
for _sname in skills_data.get('系列技能', {}):
    if _sname != '说明' and _sname not in SKILL_CAPS:
        SKILL_CAPS[_sname] = 4
# 组合技能补充缺失的上限（不覆盖硬编码值）：组合技能上限3级
for _sname in skills_data.get('组合技能', {}):
    if _sname != '说明' and _sname not in SKILL_CAPS:
        SKILL_CAPS[_sname] = 3

WEAPON_SK = frozenset(skills_data.get('武器技能', {}).keys())

SERIES_SK = frozenset(k for k in skills_data.get('系列技能', {}) if k != '说明')
GROUP_SK = frozenset(k for k in skills_data.get('组合技能', {}) if k != '说明')
NO_DECO_SK = SERIES_SK | GROUP_SK
SLOT_SKILLS = frozenset([f'Lv{n}插槽' for n in range(1, 5)])

def _is_slot_skill(sk):
    """是否通过装备孔位而非珠子满足的技能（如Lv1插槽、防具Lv1插槽）"""
    return sk.endswith('插槽')


def _slots_to_skills(slots, prefix=''):
    """把孔位列表换算成插槽技能贡献
    prefix=''  → Lv1/2/3插槽
    prefix='防具' → 防具Lv1/2/3插槽
    prefix='武器' → 武器Lv1/2/3插槽
    """
    r = {}
    for s in slots:
        if s <= 0:
            continue
        for lv in range(1, s + 1):
            key = f'{prefix}Lv{lv}插槽'
            r[key] = r.get(key, 0) + 1
    return r


# 珠子索引
deco_idx = {}
deco_skill_map = {}
for d in decos:
    sks = []
    if d['skill1']: sks.append((d['skill1'], d['skill1_level']))
    if d.get('skill2'): sks.append((d['skill2'], d['skill2_level']))
    for sk, lv in sks:
        deco_idx.setdefault((sk, d['type']), []).append((d['slot'], lv, d['name']))
    deco_skill_map[d['name']] = sks

_deco_pool = {}
for dtype in ('weapon', 'armor'):
    seen = set(); pool = []
    for (sk, dt), entries in deco_idx.items():
        if dt != dtype: continue
        for sr, pts, dn in entries:
            if dn not in seen:
                seen.add(dn)
                pool.append((sr, sk, pts, dn))
    _deco_pool[dtype] = pool

def limit_break(slots, r):
    """极限突破：R5防具三个插槽各升1级(最高3)，R6防具前两个插槽各升1级(最高3)"""
    s = list(slots)
    if r == 5: s = [min(x+1, 3) for x in s]
    elif r == 6 and len(s) >= 2:
        s[0] = min(s[0]+1, 3); s[1] = min(s[1]+1, 3)
    return s

parts = {'head': [], 'body': [], 'arms': [], 'waist': [], 'legs': []}
for a in armors:
    p = a.get('part', '')
    if p in parts:
        sk = {}
        for k, v in a.get('skills', {}).items():
            sk[k] = sk.get(k, 0) + v
        sk.pop('巧击', None)
        sk.pop('锁刃刺击', None)
        r = a.get('rarity', 0)
        # 基础版使用原始孔位（不应用极限突破）。极限突破只体现在动态生成的 + 版上，
        # 否则 + 版会在已突破的 slots 上再突破一次（双重突破，孔位虚高一级）。
        slots = list(a.get('slots', [0,0,0]))
        parts[p].append({'name': a['name'], 'rarity': r, 'skills': sk, 'slots': slots})

# 动态生成极限突破版本（仅R5/R6，每件基础装备最多生成1个升级版）
for p in list(parts.keys()):
    base_armors = [a for a in parts[p] if not a['name'].endswith('+')]
    for a in base_armors:
        r = a.get('rarity', 0)
        if r not in (5, 6):
            continue
        base_slots = a.get('slots', [0,0,0])
        if r == 5:
            upgrade = [min(x+1, 3) for x in base_slots]
        elif r == 6 and len(base_slots) >= 2:
            upgrade = [min(base_slots[0]+1, 3), min(base_slots[1]+1, 3), base_slots[2]]
        else:
            continue
        if upgrade == base_slots:
            continue
        parts[p].append({
            'name': a['name'] + '+',
            'rarity': r,
            'skills': dict(a.get('skills', {})),
            'slots': upgrade
        })

all_charms = []
for c in my_charms: all_charms.append(c)
for c in craft_charms: all_charms.append(c)
charm_pool = []
for c in all_charms:
    sk = c.get('skills', {})
    has_useful = any(s in SKILL_CAPS for s in sk)
    if has_useful:
        ca = list(c.get('armor_slots', []))
        cw = list(c.get('weapon_slots', []))
        charm_pool.append({'name': c['name'], 'skills': dict(sk), 'armor_slots': ca, 'weapon_slots': cw})

print(f"珠子:{len(decos)} 防具:{sum(len(v) for v in parts.values())} 护石:{len(charm_pool)}")

# ==================== 武器候选池（平权处理，统一进入候选构建） ====================
# 武器 = 独立装备部位（part_idx=6），与防具/护石平权。
# 每个武器候选包含：技能、武器孔位、分数。
# 武器技能（系列/组合）只提供1级，是否真正激活由配装总件数决定。
weapon_pool = []
weapon_slots = list(WSLOTS)
w_slot_sum = sum(weapon_slots)

# 无技能武器
weapon_pool.append({
    'name': '武器[无技能]',
    'part_idx': 6,
    'skills': {},
    'slots': [],
    'slots_sorted': (),
    'weapon_slots': weapon_slots,
    'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
    'rarity': 0,
    'score': 0,
    'max_slot': max(weapon_slots) if weapon_slots else 0,
    'slot_sum': 0,
    'w_slot_sum': w_slot_sum,
    '_is_weapon': True,
    '_weapon_series': None,
    '_weapon_series_level': None,
    '_weapon_group': None,
})

# 系列技能武器（Lv2=效果I, Lv4=效果II）
for s in SERIES_SK:
    weapon_pool.append({
        'name': f'武器[{s}]',
        'part_idx': 6,
        'skills': {s: 1},
        'slots': [],
        'slots_sorted': (),
        'weapon_slots': weapon_slots,
        'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
        'rarity': 0,
        'score': 100,
        'max_slot': max(weapon_slots) if weapon_slots else 0,
        'slot_sum': 0,
        'w_slot_sum': w_slot_sum,
        '_is_weapon': True,
        '_weapon_series': s,
        '_weapon_series_level': 1,
        '_weapon_group': None,
    })

# 组合技能武器（Lv3=效果I）
for s in GROUP_SK:
    weapon_pool.append({
        'name': f'武器[{s} Lv3]',
        'part_idx': 6,
        'skills': {s: 1},
        'slots': [],
        'slots_sorted': (),
        'weapon_slots': weapon_slots,
        'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
            'rarity': 0,
            'score': 100 + 3,
            'max_slot': max(weapon_slots) if weapon_slots else 0,
            'slot_sum': 0,
            'w_slot_sum': w_slot_sum,
            '_is_weapon': True,
            '_weapon_series': None,
            '_weapon_series_level': None,
            '_weapon_group': s,
        })

# 系列+组合技能武器
for s in SERIES_SK:
    for g in GROUP_SK:
        weapon_pool.append({
            'name': f'武器[{s} + {g}]',
            'part_idx': 6,
            'skills': {s: 1, g: 1},
            'slots': [],
            'slots_sorted': (),
            'weapon_slots': weapon_slots,
            'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
            'rarity': 0,
            'score': 200,
            'max_slot': max(weapon_slots) if weapon_slots else 0,
            'slot_sum': 0,
            'w_slot_sum': w_slot_sum,
            '_is_weapon': True,
            '_weapon_series': s,
            '_weapon_series_level': 1,
            '_weapon_group': g,
        })

print(f"珠子:{len(decos)} 防具:{sum(len(v) for v in parts.values())} 护石:{len(charm_pool)} 武器:{len(weapon_pool)}")

# ==================== 伤害计算（与v2完全一致） ====================
def _skills_to_tuple(skl):
    return tuple(sorted(skl.items()))

@functools.lru_cache(maxsize=8192)
def _calc_damage_cached(skills_tuple):
    skl = dict(skills_tuple)
    def cl(sk, cap): return min(skl.get(sk, 0), cap)
    chal=cl('挑战者',5); burst=cl('连击',5); muzu=cl('无伤',5)
    weak=cl('弱点特效',5); furue=cl('精神抖擞',3); rikikai=cl('力量解放',5)
    super_lv=cl('超会心',5); ecrit=cl('会心击【属性】',3); migo=cl('无我之境',3)
    counter=cl('逆袭',3); atk=cl('攻击',5); kanken=cl('看破',5)
    dragon=cl('龙属性攻击强化',3); oguard=cl('攻击守势',3)
    coal=cl('因祸得福',3); foray=cl('攻势',5)
    absorb=cl('属性吸收',3)
    fire_dragon=cl('火龙之力',4)
    bahar=cl('霸主之魂',3)
    geki=cl('巨戟龙的默示录',4)
    touhou=cl('冻峰龙的反叛',4)
    kizuna=cl('锁刃龙的饥饿',4)
    kuroshoku=cl('黑蚀龙之力',4)
    kyozou=cl('凶爪龙之力',4)
    ecb=ELEM_CRIT[ecrit]; scb=SUPER_CRIT[super_lv]
    bahar_mul = BAHAR_MUL if bahar >= 3 else 1.0
    atk_mul = ATK_MUL[atk]
    d_mul = DRAGON_ELE[dragon][1]
    geki_mul = GEKI_MUL[geki]
    geki_add = GEKI_ADD[geki]
    if coal > 0:
        coal_expect = 1.0 + (COAL_ELE[coal] - 1.0) * UCSG
    else:
        coal_expect = 1.0
    d_add = DRAGON_ELE[dragon][0]
    absorb_add = ABSORB_ELE[absorb] * ABSORB_COV
    kyozou_atk = 8 if kyozou >= 2 else 0
    states = []
    if chal > 0 or geki > 0: states.append(('rage', UR))
    if burst > 0: states.append(('rengeki', URE))
    if muzu > 0: states.append(('mukizu', UM))
    if kuroshoku >= 2 and migo >= 3:
        states.append(('kuroshoku_migo', 0.60))
    elif kuroshoku >= 2:
        states.append(('kuroshoku', 0.50))
    if rikikai > 0: states.append(('rikikai', URK))
    if weak > 0: states.append(('weak', UW))
    if furue > 0: states.append(('furue', UF))
    if counter > 0: states.append(('counter', UCOU))
    if oguard > 0: states.append(('oguard', OGUARD_COV))
    if not states: states.append(('none', 1.0))
    wr = er = 0.0
    state_details = []
    for combo in itertools.product(*([[True, False]] * len(states))):
        pr = 1.0
        add_atk = PERM_ATK
        add_crt = 0
        add_ele = 0.0
        bactive = False
        geki_mul_act = 1.0
        geki_add_act = 0
        og_act = 1.0
        for (nm, up), act in zip(states, combo):
            pr *= up if act else (1 - up)
            if not act: continue
            if nm == 'rage':
                if chal > 0:
                    add_atk += CHAL_ATK[chal]; add_crt += CRIT_VAL['挑战者'][chal]
                if geki > 0:
                    geki_mul_act = geki_mul
                    geki_add_act = geki_add
            elif nm == 'rengeki':
                add_atk += BURST_ATK[burst]; add_ele += BURST_ELE[burst]; bactive = True
            elif nm == 'mukizu':
                add_atk += MUZ_ATK[muzu]
            elif nm == 'kuroshoku':
                add_crt += 15
            elif nm == 'kuroshoku_migo':
                add_crt += 25
            elif nm == 'rikikai':
                add_crt += CRIT_VAL['力量解放'][rikikai]
            elif nm == 'weak':
                add_crt += CRIT_VAL['弱点特效'][weak]
            elif nm == 'furue':
                add_crt += CRIT_VAL['精神抖擞'][furue]
            elif nm == 'counter':
                add_atk += COUNTER_ATK[counter]
            elif nm == 'oguard':
                og_act = OFF_GUARD[oguard]
        if atk > 0:
            add_atk += ATK_VAL[atk]
        if kyozou_atk > 0:
            add_atk += kyozou_atk
        if kanken > 0:
            add_crt += CRIT_VAL['看破'][kanken]
        if migo > 0:
            add_crt += CRIT_VAL['无我之境'][migo]
        ea = W_ATK * atk_mul * og_act * bahar_mul + add_atk
        ec = min(W_CRT + add_crt, 100)
        be = W_ELE * d_mul * geki_mul_act * coal_expect + d_add + geki_add_act + add_ele + absorb_add
        cr = ec / 100.0
        crit_phys = cr * scb + (1 - cr)
        crit_elem = cr * ecb + (1 - cr)
        phys = pr * ea * PC_R * crit_phys
        elem = pr * be * EC_R * crit_elem
        wr += phys
        er += elem
        if pr > 0.005:
            state_names = []
            for (nm, up), act in zip(states, combo):
                if act:
                    state_names.append(STATE_CN.get(nm, nm))
            state_details.append({
                'states': ' + '.join(state_names) if state_names else '无',
                'prob': pr,
                'ea': ea,
                'ec': ec,
                'be': be,
                'phys': phys,
                'elem': elem,
                'total': phys + elem
            })
    total = wr + er + FIRE_DRAGON_DMG.get(fire_dragon, 0)
    detail = {
        'base_stats': {
            'W_ATK': W_ATK, 'W_CRT': W_CRT, 'W_ELE': W_ELE, 'PERM_ATK': PERM_ATK
        },
        'multipliers': {
            'atk_mul': atk_mul, 'd_mul': d_mul, 'bahar_mul': bahar_mul,
            'geki_mul': geki_mul, 'coal_expect': coal_expect, 'og_act': og_act
        },
        'additive': {
            'add_atk': add_atk, 'add_crt': add_crt, 'add_ele': add_ele,
            'd_add': d_add, 'geki_add': geki_add, 'absorb_add': absorb_add,
            'kyozou_atk': kyozou_atk
        },
        'skill_levels': {
            '挑战者': chal, '连击': burst, '无伤': muzu, '弱点特效': weak,
            '精神抖擞': furue, '力量解放': rikikai, '超会心': super_lv,
            '会心击【属性】': ecrit, '无我之境': migo, '逆袭': counter,
            '攻击': atk, '看破': kanken, '龙属性攻击强化': dragon,
            '攻击守势': oguard, '因祸得福': coal, '攻势': foray,
            '属性吸收': absorb, '火龙之力': fire_dragon, '霸主之魂': bahar,
            '巨戟龙的默示录': geki, '冻峰龙的反叛': touhou,
            '锁刃龙的饥饿': kizuna, '黑蚀龙之力': kuroshoku,
            '凶爪龙之力': kyozou
        },
        'coefficients': {
            'PC_R': PC_R, 'EC_R': EC_R,
            'UR': UR, 'URE': URE, 'UM': UM, 'UKZ': UKZ, 'URK': URK, 'UW': UW, 'UF': UF,
            'UCOU': UCOU, 'UCSG': UCSG, 'OGUARD_COV': OGUARD_COV
        },
        'states': state_details,
        'summary': {
            'phys': wr, 'elem': er, 'fixed': FIRE_DRAGON_DMG.get(fire_dragon, 0),
            'total': total, 'scb': scb, 'ecb': ecb
        }
    }
    return total, detail

def calc_damage(skl):
    total, _ = _calc_damage_cached(_skills_to_tuple(skl))
    return total

def calc_damage_detail(skl):
    total, detail = _calc_damage_cached(_skills_to_tuple(skl))
    return total, detail

@functools.lru_cache(maxsize=8192)
def _calc_weighted_crit_cached(skills_tuple):
    skl = dict(skills_tuple)
    def cl(sk, cap): return min(skl.get(sk, 0), cap)
    chal=cl('挑战者',5); burst=cl('连击',5); muzu=cl('无伤',5)
    weak=cl('弱点特效',5); furue=cl('精神抖擞',3); rikikai=cl('力量解放',5)
    migo=cl('无我之境',3); counter=cl('逆袭',3); atk=cl('攻击',5); kanken=cl('看破',5)
    foray=cl('攻势',5)
    kuroshoku=cl('黑蚀龙之力',4)
    migo=cl('无我之境',3)
    states = []
    if chal > 0: states.append(('rage', UR))
    if burst > 0: states.append(('rengeki', URE))
    if muzu > 0: states.append(('mukizu', UM))
    if kuroshoku >= 2 and migo >= 3:
        states.append(('kuroshoku_migo', 0.60))
    elif kuroshoku >= 2:
        states.append(('kuroshoku', 0.50))
    if rikikai > 0: states.append(('rikikai', URK))
    if weak > 0: states.append(('weak', UW))
    if furue > 0: states.append(('furue', UF))
    if counter > 0: states.append(('counter', UCOU))
    if not states: states.append(('none', 1.0))
    wcr = 0.0
    for combo in itertools.product(*([[True, False]] * len(states))):
        pr = 1.0
        add_crt = 0
        for (nm, up), act in zip(states, combo):
            pr *= up if act else (1 - up)
            if not act: continue
            if nm == 'rage': add_crt += CRIT_VAL['挑战者'][chal]
            elif nm == 'kuroshoku': add_crt += 15
            elif nm == 'kuroshoku_migo': add_crt += 25
            elif nm == 'rikikai': add_crt += CRIT_VAL['力量解放'][rikikai]
            elif nm == 'weak': add_crt += CRIT_VAL['弱点特效'][weak]
            elif nm == 'furue': add_crt += CRIT_VAL['精神抖擞'][furue]
        if kanken > 0: add_crt += CRIT_VAL['看破'][kanken]
        if migo > 0: add_crt += CRIT_VAL['无我之境'][migo]
        ec = min(W_CRT + add_crt, 100)
        wcr += pr * ec
    return wcr

def calc_weighted_crit(skl):
    return _calc_weighted_crit_cached(_skills_to_tuple(skl))

@functools.lru_cache(maxsize=65536)
def gain(sk, old, add):
    cap = SKILL_CAPS.get(sk, 99)
    c = min(old, cap)
    return min(add, cap - c) if c < cap else 0

_WEAPON_DECO_POOL = None
_ARMOR_DECO_POOL = None
_ARMOR_DECO_BY_SKILL = None  # skill -> [deco, ...] 索引

def _build_deco_pool_full(dtype):
    seen = set(); pool = []
    for d in decos:
        if d.get('type') != dtype: continue
        dn = d['name']
        if dn in seen: continue
        seen.add(dn)
        sks = []
        if d.get('skill1'): sks.append((d['skill1'], d.get('skill1_level', 0)))
        if d.get('skill2'): sks.append((d['skill2'], d.get('skill2_level', 0)))
        pool.append({'name': dn, 'slot': d['slot'], 'skills': sks})
    return pool

def _get_deco_pool(dtype):
    global _WEAPON_DECO_POOL, _ARMOR_DECO_POOL, _ARMOR_DECO_BY_SKILL
    if dtype == 'weapon':
        if _WEAPON_DECO_POOL is None:
            _WEAPON_DECO_POOL = _build_deco_pool_full('weapon')
        return _WEAPON_DECO_POOL
    else:
        if _ARMOR_DECO_POOL is None:
            _ARMOR_DECO_POOL = _build_deco_pool_full('armor')
            # 同时构建技能→珠子索引
            _ARMOR_DECO_BY_SKILL = {}
            for _d in _ARMOR_DECO_POOL:
                for _sk, _pts in _d['skills']:
                    _ARMOR_DECO_BY_SKILL.setdefault(_sk, []).append(_d)
        return _ARMOR_DECO_POOL

def _get_armor_deco_for_skill(sk):
    """获取提供指定技能的防具珠子列表（用索引加速）"""
    if _ARMOR_DECO_BY_SKILL is None:
        _get_deco_pool('armor')
    return _ARMOR_DECO_BY_SKILL.get(sk, [])

_fill_weapon_cache_tl = threading.local()
def _fw_cache():
    d = getattr(_fill_weapon_cache_tl, 'd', None)
    if d is None:
        d = {}
        _fill_weapon_cache_tl.d = d
    return d
_FEASIBILITY_ONLY = False  # True时跳过fill_slots的优化循环（追加技能查询用）

def _fill_weapon_slots_smart(fs, w_slots, fixed_skills):
    slots = sorted([s for s in w_slots if s > 0], reverse=True)
    w_fixed = {s: r for s, r in fixed_skills.items() if s in WEAPON_SK and fs.get(s, 0) < r}
    if not w_fixed:
        return fs, [], slots
    if not slots:
        return None
    cache_key = (frozenset(w_fixed.items()), tuple(slots),
                 tuple(sorted((s, fs.get(s, 0)) for s in w_fixed)))
    if cache_key in _fw_cache():
        cached = _fw_cache()[cache_key]
        if cached is None:
            return None
        new_fs = dict(fs)
        for sk, lv in cached['add_skills'].items():
            new_fs[sk] = min(new_fs.get(sk, 0) + lv, SKILL_CAPS.get(sk, 99))
        return new_fs, list(cached['used']), list(cached['rem_slots'])
    from itertools import combinations_with_replacement
    pool = _get_deco_pool('weapon')
    # 按技能贡献去重：相同(slot, {skill:pts})只保留一个
    seen_patterns = set()
    cand_decos = []
    for deco in pool:
        if deco['slot'] > max(slots):
            continue
        has_relevant = False
        relevant_skills = {}
        for sk, pts in deco['skills']:
            if sk in w_fixed and pts > 0:
                has_relevant = True
                relevant_skills[sk] = pts
        if has_relevant:
            pattern = (deco['slot'], frozenset(relevant_skills.items()))
            if pattern not in seen_patterns:
                seen_patterns.add(pattern)
                cand_decos.append(deco)
    if not cand_decos:
        _fw_cache()[cache_key] = None
        return None
    # 按有效贡献排序：pts高且slot低优先
    cand_decos.sort(key=lambda d: (-sum(pts for sk, pts in d['skills'] if sk in w_fixed), d['slot']))
    n_slots = len(slots)
    # 快速上界检查：n_slots个槽都放贡献最高的珠子仍不够 → 无解
    # （注意枚举用 combinations_with_replacement，允许重复取同一珠子，
    #   因此上界必须是 n_slots*max_contrib，而非 top-n 不同珠子的和，否则会误杀）
    _total_deficit = sum(w_fixed.values())
    _max_contrib = 0
    for d in cand_decos:
        _c = sum(min(pts, w_fixed.get(sk, 0)) for sk, pts in d['skills'] if sk in w_fixed)
        if _c > _max_contrib:
            _max_contrib = _c
    if _max_contrib == 0 or n_slots * _max_contrib < _total_deficit:
        _fw_cache()[cache_key] = None
        return None
    # 逐技能可行性预检：每个赤字技能能否在剩余slot数内被满足
    for sk, need in w_fixed.items():
        have = fs.get(sk, 0)
        if have >= need:
            continue
        d = need - have
        pool = deco_idx.get((sk, 'weapon'), [])
        if not pool:
            _fw_cache()[cache_key] = None
            return None
        best_pts = max(pts for sr, pts, dn in pool)
        need_slots = (d + best_pts - 1) // best_pts
        if need_slots > n_slots:
            _fw_cache()[cache_key] = None
            return None
    for n in range(1, n_slots + 1):
        for combo in combinations_with_replacement(range(len(cand_decos)), n):
            deco_list = [cand_decos[i] for i in combo]
            deco_slots = sorted([d['slot'] for d in deco_list], reverse=True)
            ok = True
            for ds, ss in zip(deco_slots, slots):
                if ds > ss:
                    ok = False; break
            if not ok:
                continue
            test_fs = dict(fs)
            for deco in deco_list:
                for sk, pts in deco['skills']:
                    test_fs[sk] = min(test_fs.get(sk, 0) + pts, SKILL_CAPS.get(sk, 99))
            all_ok = True
            for sk, need in w_fixed.items():
                if test_fs.get(sk, 0) < need:
                    all_ok = False; break
            if all_ok:
                used = [d['name'] for d in deco_list]
                used_slots = set()
                for deco in deco_list:
                    for i, s in enumerate(slots):
                        if i not in used_slots and s >= deco['slot']:
                            used_slots.add(i)
                            break
                rem_slots = [s for i, s in enumerate(slots) if i not in used_slots]
                add_skills = {}
                for sk in w_fixed:
                    add_skills[sk] = test_fs.get(sk, 0) - fs.get(sk, 0)
                _fw_cache()[cache_key] = {
                    'used': used, 'rem_slots': rem_slots, 'add_skills': add_skills
                }
                return test_fs, used, rem_slots
    _fw_cache()[cache_key] = None
    return None

def fill_slots(skills, a_slots, w_slots, fixed_skills, min_keep_armor=0, min_keep_weapon=0):
    fs = dict(skills); used = []
    a = sorted([s for s in a_slots if s > 0])
    w = sorted([s for s in w_slots if s > 0], reverse=True)
    armor_min = {}
    weapon_min = {}
    for sk, lv in fixed_skills.items():
        if not sk.endswith('插槽'):
            continue
        try:
            if sk.startswith('防具Lv'):
                n = int(sk[4:-2])
                armor_min[n] = armor_min.get(n, 0) + lv
            elif sk.startswith('武器Lv'):
                n = int(sk[4:-2])
                weapon_min[n] = weapon_min.get(n, 0) + lv
            elif sk.startswith('Lv'):
                n = int(sk[2:-2])
                armor_min[n] = armor_min.get(n, 0) + lv
        except ValueError:
            pass
    total_slot_keep = sum(armor_min.values())
    armor_keep_extra = sum(armor_min.values())
    weapon_keep_extra = sum(weapon_min.values())
    # 预留槽位隔离：孔位技能需求的槽位先从可插池移除，
    # 防止贪心插珠把低阶珠插进预留槽导致最终校验失败
    reserved_a, reserved_w = [], []
    # 满足最小等级需求（优先消耗高阶槽，保留低阶槽给珠子）
    for lv, need in sorted(armor_min.items(), reverse=True):
        for _ in range(need):
            found = False
            for i, s in enumerate(a):
                if s >= lv:
                    a.pop(i); reserved_a.append(lv); found = True; break
            if not found:
                return None
    for lv, need in sorted(weapon_min.items(), reverse=True):
        for _ in range(need):
            found = False
            for i, s in enumerate(w):
                if s >= lv:
                    w.pop(i); reserved_w.append(lv); found = True; break
            if not found:
                return None
    w_result = _fill_weapon_slots_smart(dict(fs), w, fixed_skills)
    if w_result is None:
        return None
    fs, w_used, rem_w = w_result
    used.extend(w_used)
    pool_a = _get_deco_pool('armor')
    armor_fixed = {s: r for s, r in fixed_skills.items()
                   if s not in WEAPON_SK and not _is_slot_skill(s)
                   and s not in NO_DECO_SK
                   and fs.get(s, 0) < r}
    if armor_fixed:
        deficit = {}
        for sk, need in armor_fixed.items():
            d = need - fs.get(sk, 0)
            if d > 0:
                deficit[sk] = d
        if deficit:
            deficit_skills = set(deficit.keys())
            pool_a_relevant = [d for d in pool_a if any(sk in deficit_skills for sk, pts in d['skills'])]
            deco_by_slot = {1: [], 2: [], 3: []}
            for d in pool_a_relevant:
                deco_by_slot[d['slot']].append(d)
            while deficit:
                best_placement = None
                best_score = -1
                for si, s in enumerate(a):
                    if s <= 0:
                        continue
                    for slot_lv in range(1, s + 1):
                        for deco in deco_by_slot.get(slot_lv, []):
                            total_gain = 0
                            bonus = 0
                            for sk, pts in deco['skills']:
                                if sk in deficit:
                                    total_gain += min(pts, deficit[sk])
                                cur = fs.get(sk, 0)
                                if sk in fixed_skills and cur < fixed_skills.get(sk, 0):
                                    bonus += gain(sk, cur, pts)
                            if total_gain > 0:
                                score = total_gain * 100 + bonus
                                if score > best_score:
                                    best_score = score
                                    best_placement = (deco, si)
                if best_placement is None:
                    break
                deco, idx = best_placement
                a.pop(idx)
                for sk, pts in deco['skills']:
                    fs[sk] = min(fs.get(sk, 0) + pts, SKILL_CAPS.get(sk, 99))
                    if sk in deficit:
                        deficit[sk] = max(0, deficit[sk] - min(pts, deficit[sk]))
                        if deficit[sk] <= 0:
                            del deficit[sk]
                used.append(deco['name'])
    for s, r in fixed_skills.items():
        if _is_slot_skill(s):
            continue
        if s in NO_DECO_SK:
            continue
        if fs.get(s, 0) < r:
            return None
    w_rem = sorted([s for s in rem_w if s > 0], reverse=True)
    _w_keep = min_keep_weapon  # 分侧/孔位预留已物理隔离，此处只留通用预留
    if not _FEASIBILITY_ONLY:
        w_pool = _get_deco_pool('weapon')
        w_pool_sorted = sorted(w_pool, key=lambda d: (-_deco_priority_score(d['skills'], fs, SKILL_CAPS), -d['slot']))
        for deco in w_pool_sorted:
            if len(w_rem) <= _w_keep:
                break
            # 评分每轮只算一次（原来每槽位重算，同一珠子重复评分slots倍）
            p_score = _deco_priority_score(deco['skills'], fs, SKILL_CAPS)
            if p_score <= 0:
                continue
            g_total = sum(gain(sk, fs.get(sk, 0), pts) for sk, pts in deco['skills'])
            if g_total <= 0:
                continue
            for i, s in enumerate(w_rem):
                if s >= deco['slot']:
                    w_rem.pop(i)
                    for sk, pts in deco['skills']:
                         fs[sk] = min(fs.get(sk, 0) + pts, SKILL_CAPS.get(sk, 99))
                    used.append(deco['name'])
                    break
    min_keep = min_keep_armor  # 孔位预留已物理隔离，此处只留通用预留
    if not _FEASIBILITY_ONLY:
        while len(a) > min_keep:
            # 每轮先对所有珠子评分一次，再扫槽位选最优（原来每槽位×每珠子重算评分）
            scored = []
            for deco in pool_a:
                p_score = _deco_priority_score(deco['skills'], fs, SKILL_CAPS)
                if p_score <= 0:
                    continue
                g_total = sum(gain(sk, fs.get(sk, 0), pts) for sk, pts in deco['skills'])
                if g_total > 0:
                    scored.append((p_score * 100 + g_total, deco))
            if not scored:
                break
            best_d = None; best_s = -1; best_i = -1
            for si, s in enumerate(a):
                for sc, deco in scored:
                    if deco['slot'] > s: continue
                    if sc > best_s:
                        best_s = sc; best_d = deco; best_i = si
            if best_d is None: break
            a.pop(best_i)
            for sk, pts in best_d['skills']:
                fs[sk] = min(fs.get(sk, 0) + pts, SKILL_CAPS.get(sk, 99))
            used.append(best_d['name'])
    _w_keep = min_keep_weapon
    if _w_keep > 0:
        if sum(1 for s in w_rem if s > 0) < _w_keep:
            return None
    all_rem_a = a + reserved_a
    all_rem_w = w_rem + reserved_w
    for lv, need_cnt in sorted(armor_min.items(), reverse=True):
        avail = sum(1 for s in all_rem_a if s >= lv)
        if avail < need_cnt:
            return None
    for lv, need_cnt in sorted(weapon_min.items(), reverse=True):
        avail = sum(1 for s in all_rem_w if s >= lv)
        if avail < need_cnt:
            return None
    return fs, used, a + reserved_a, w_rem + reserved_w

# ==================== 轻量级缺口检查 ====================
def can_fill_gap(skill_gap, a_slots, w_slots):
    if not skill_gap:
        return True
    w_gap = {sk: need for sk, need in skill_gap.items() if sk in WEAPON_SK}
    w_slots_avail = sorted([s for s in w_slots if s > 0], reverse=True)
    for sk, need in w_gap.items():
        pool = deco_idx.get((sk, 'weapon'), [])
        if not pool:
            return False
        best_pts = max(pts for sr, pts, dn in pool)
        need_slots = (need + best_pts - 1) // best_pts
        if need_slots > len(w_slots_avail):
            return False
    a_gap = {sk: need for sk, need in skill_gap.items() if sk not in WEAPON_SK}
    a_slots_avail = sorted([s for s in a_slots if s > 0], reverse=True)
    for sk, need in a_gap.items():
        pool = deco_idx.get((sk, 'armor'), [])
        if not pool:
            return False
        best_pts = max(pts for sr, pts, dn in pool)
        need_slots = (need + best_pts - 1) // best_pts
        if need_slots > len(a_slots_avail):
            return False
    return True

# ==================== 珠子可行性检查 ====================
def _check_deco_feasible(skills, a_slots, w_slots, fixed_skills, combo_skills,
                         weapon_skills, min_rem_armor, min_rem_weapon=0):
    a_cnt = {1:0, 2:0, 3:0}
    for s in a_slots:
        if s > 0: a_cnt[s] = a_cnt[s] + 1
    w_cnt = {1:0, 2:0, 3:0}
    for s in w_slots:
        if s > 0: w_cnt[s] = w_cnt[s] + 1
    rem = min_rem_armor
    for lv in [1, 2, 3]:
        while rem > 0 and a_cnt[lv] > 0:
            a_cnt[lv] -= 1
            rem -= 1
    if rem > 0:
        return False
    rem_w = min_rem_weapon
    for lv in [1, 2, 3]:
        while rem_w > 0 and w_cnt[lv] > 0:
            w_cnt[lv] -= 1
            rem_w -= 1
    if rem_w > 0:
        return False
    for sk, lv in fixed_skills.items():
        if not _is_slot_skill(sk):
            continue
        if sk.startswith('防具Lv') and sk.endswith('插槽'):
            try:
                n = int(sk[4:-2])
            except ValueError:
                continue
            if a_cnt[n] < lv:
                return False
        elif sk.startswith('武器Lv') and sk.endswith('插槽'):
            try:
                n = int(sk[4:-2])
            except ValueError:
                continue
            if w_cnt[n] < lv:
                return False
        elif sk.startswith('Lv') and sk.endswith('插槽'):
            try:
                n = int(sk[2:-2])
            except ValueError:
                continue
            if a_cnt[n] + w_cnt[n] < lv:
                return False
    all_req = {}
    for sk, need in fixed_skills.items():
        if _is_slot_skill(sk):
            continue
        if sk in NO_DECO_SK:
            continue
        all_req[sk] = need
    if combo_skills:
        for sk, need in combo_skills.items():
            if sk in NO_DECO_SK:
                continue
            armor_need = max(0, need - weapon_skills.get(sk, 0))
            if armor_need > 0:
                all_req[sk] = all_req.get(sk, 0) + armor_need
    for sk, need in all_req.items():
        have = skills.get(sk, 0)
        if have >= need:
            continue
        dtype = 'weapon' if sk in WEAPON_SK else 'armor'
        pool = deco_idx.get((sk, dtype), [])
        if not pool:
            return False
    a_total_need = 0
    a_need = {1:0, 2:0, 3:0}
    for sk, need in all_req.items():
        have = skills.get(sk, 0)
        if have >= need:
            continue
        d = need - have
        dtype = 'weapon' if sk in WEAPON_SK else 'armor'
        if dtype == 'weapon':
            continue
        pool = deco_idx.get((sk, 'armor'), [])
        a_total_need += d
        best = max(pool, key=lambda x: x[1])
        best_pts = best[1]
        best_slot = best[0]
        slots_needed = (d + best_pts - 1) // best_pts
        a_need[best_slot] += slots_needed
    a_total_slots = a_cnt[1] + a_cnt[2] + a_cnt[3]
    if a_total_need > 0:
        a_max_pts = 1
        for sk in all_req:
            if sk not in WEAPON_SK and skills.get(sk, 0) < all_req[sk]:
                pool = deco_idx.get((sk, 'armor'), [])
                if pool:
                    mp = max(pts for sr, pts, dn in pool)
                    if mp > a_max_pts:
                        a_max_pts = mp
        a_slots_needed = (a_total_need + a_max_pts - 1) // a_max_pts
        if a_slots_needed > a_total_slots:
            return False
    a_avail = [0, 0, 0, 0]
    a_use2 = [0, 0, 0, 0]
    for lv in [1, 2, 3]:
        a_avail[lv] = a_cnt[lv]
        a_use2[lv] = a_need[lv]
    for lv in [1, 2]:
        if a_use2[lv] > a_avail[lv]:
            borrow = a_use2[lv] - a_avail[lv]
            a_use2[lv] = a_avail[lv]
            a_use2[lv+1] += borrow
    if a_use2[3] > a_avail[3]:
        return False
    return True

# ==================== 支配检查 ====================
def _dominated_check(item, dom, skill_names):
    # 使用预排序的slots_sorted，避免每次sorted
    d_s = dom.get('slots_sorted')
    i_s = item.get('slots_sorted')
    if d_s is None: d_s = tuple(sorted(dom['slots'], reverse=True))
    if i_s is None: i_s = tuple(sorted(item['slots'], reverse=True))
    for i in range(max(len(d_s), len(i_s))):
        d = d_s[i] if i < len(d_s) else 0
        iv = i_s[i] if i < len(i_s) else 0
        if d < iv: return False
    d_ws = dom.get('wslots_sorted', ())
    i_ws = item.get('wslots_sorted', ())
    for i in range(max(len(d_ws), len(i_ws))):
        d = d_ws[i] if i < len(d_ws) else 0
        iv = i_ws[i] if i < len(i_ws) else 0
        if d < iv: return False
    # 只检查有赤字的技能（dom中技能值<item中时才不支配）
    item_sk = item['skills']
    dom_sk = dom['skills']
    for sk in skill_names:
        if item_sk.get(sk, 0) > dom_sk.get(sk, 0): return False
    return True

# ==================== 候选构建 ====================
def _build_candidates(charm_pool, fixed_skills, combo_skills, quiet=False, extra_skill_names=None, user_weapon_skills=None,
                      protect_no_deco=False, skip_domination=False):
    """构建候选装备列表（与dfs_search分离，允许缓存复用）

    extra_skill_names: 额外技能名集合，用于扩大支配检查和预过滤范围，
    确保追加技能查询时不会误删含目标技能的候选。
    user_weapon_skills: 用户在武器配置区选择的技能（或自动匹配的武器技能）
        - None: 未指定，从 combo_skills 中提取 NO_DECO_SK 作为武器技能（旧行为）
        - {}: 明确没有武器技能，不从 combo_skills 提取
        - {技能: 等级}: 指定了武器技能
    """
    fixed_skills = _normalize_skills_dict(fixed_skills)
    combo_skills = _normalize_skills_dict(combo_skills)
    weapon_skills = {}
    if user_weapon_skills is not None:
        # 新行为：使用传入的武器技能（可以是空字典 {}）
        for sk, lv in user_weapon_skills.items():
            weapon_skills[_normalize_skill_name(sk)] = lv
    elif combo_skills:
        # 旧行为：从 combo_skills 中提取 NO_DECO_SK 作为武器技能
        for sk, lv in combo_skills.items():
            if sk in NO_DECO_SK:
                weapon_skills[sk] = 1

    # 武器技能也可以由防具和护石提供，所以不将它们从armor_fixed中排除
    # weapon_fixed仅用于武器孔位填充优化，不影响候选预过滤
    armor_fixed = dict(fixed_skills)  # 所有技能都参与防具候选筛选
    weapon_fixed = {s: r for s, r in fixed_skills.items() if s in WEAPON_SK}

    all_skill_names = set(fixed_skills.keys())
    if combo_skills:
        all_skill_names.update(combo_skills.keys())
    if extra_skill_names:
        all_skill_names.update(extra_skill_names)

    # 预计算合并技能需求（避免在循环中重复get）
    merged_needs = dict(fixed_skills)
    if combo_skills:
        for s, lv in combo_skills.items():
            merged_needs[s] = max(merged_needs.get(s, 0), lv)

    part_names = ['head', 'body', 'arms', 'waist', 'legs']
    candidates = []
    for pi, pn in enumerate(part_names):
        for a in parts[pn]:
            a_sk = dict(a['skills'])
            # 注意：此处不生成 LvN插槽/防具LvN插槽 技能点。插槽技能表示
            # "配装完成后剩余的空插槽"，由 fill_slots 在叶子按剩余孔位精确校验并写入；
            # 若按初始孔位生成，会把"总插槽"误当已满足，且污染结果里的插槽技能值。
            sk_score = sum(min(v, merged_needs.get(s, 0)) * SKILL_WEIGHT
                          for s, v in a_sk.items() if s in all_skill_names)
            slot_sum = sum(a['slots']) if a['slots'] else 0
            score = sk_score + slot_sum
            candidates.append({
                'name': a['name'], 'part_idx': pi,
                'skills': a_sk, 'slots': a['slots'],
                'slots_sorted': tuple(sorted(a['slots'], reverse=True)),
                'wslots_sorted': (),
                'rarity': a['rarity'], 'score': score,
                'max_slot': max(a['slots']) if a['slots'] else 0,
                'slot_sum': slot_sum, 'w_slot_sum': 0
            })
    for c in charm_pool:
        c_sk = dict(c.get('skills', {}))
        # 护石孔位不生成插槽技能点（空槽语义，见防具候选注释）
        armor_slots = c.get('armor_slots', [])
        weapon_slots = c.get('weapon_slots', [])
        a_sum = sum(armor_slots) if armor_slots else 0
        w_sum = sum(weapon_slots) if weapon_slots else 0
        sk_score = sum(min(v, merged_needs.get(s, 0)) * SKILL_WEIGHT
                      for s, v in c_sk.items() if s in all_skill_names)
        score = sk_score + a_sum + w_sum
        candidates.append({
            'name': c['name'], 'part_idx': 5,
            'skills': c_sk, 'slots': armor_slots,
            'slots_sorted': tuple(sorted(armor_slots, reverse=True)),
            'weapon_slots': weapon_slots,
            'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
            'rarity': 0, 'score': score,
            'max_slot': max(armor_slots + weapon_slots) if (armor_slots or weapon_slots) else 0,
            'slot_sum': a_sum, 'w_slot_sum': w_sum
        })

    # 武器候选池（平权处理，统一进入候选构建）
    # 固定武器过滤：GUI 语义"固定=预筛选武器"——用户显式指定武器技能时
    # （user_weapon_skills 非 None），只保留"包含全部指定技能"的武器进入匹配池
    # （如指定 黑蚀龙之力+霸主之魂 → 只剩 武器[黑蚀龙之力 + 霸主之魂] 一个候选）。
    # 注意仅在用户显式指定时过滤：旧路径（None）从 combo_skills 提取的系列技能
    # 可由防具提供，据此裁剪武器池会误删合法解（旧行为保持不变）。
    _fixed_weapon_filter = None
    if user_weapon_skills is not None and weapon_skills:
        _fixed_weapon_filter = [(s, lv) for s, lv in weapon_skills.items()]
    for c in weapon_pool:
        c_sk = dict(c.get('skills', {}))
        if _fixed_weapon_filter:
            # 武器池技能名已规范化（SERIES_SK/GROUP_SK 常量），直接比较
            if not all(c_sk.get(s, 0) >= lv for s, lv in _fixed_weapon_filter):
                continue
        # 武器孔位不生成插槽技能点（空槽语义，见防具候选注释）
        weapon_slots = c.get('weapon_slots', [])
        armor_slots = c.get('slots', [])
        a_sum = sum(armor_slots) if armor_slots else 0
        w_sum = sum(weapon_slots) if weapon_slots else 0
        sk_score = sum(min(v, merged_needs.get(s, 0)) * SKILL_WEIGHT
                      for s, v in c_sk.items() if s in all_skill_names)
        score = sk_score + a_sum + w_sum
        candidates.append({
            'name': c['name'], 'part_idx': 6,
            'skills': c_sk, 'slots': armor_slots,
            'slots_sorted': tuple(sorted(armor_slots, reverse=True)),
            'weapon_slots': weapon_slots,
            'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
            'rarity': c.get('rarity', 0), 'score': score,
            'max_slot': max(armor_slots + weapon_slots) if (armor_slots or weapon_slots) else 0,
            'slot_sum': a_sum, 'w_slot_sum': w_sum
        })

    # 去重
    merged = {}
    for c in candidates:
        key = (c['part_idx'],
               frozenset(c['skills'].items()),
               tuple(sorted(c['slots'])),
               tuple(sorted(c.get('weapon_slots', ()))))
        if key in merged:
            merged[key]['names'].append(c['name'])
            if c['score'] > merged[key]['score']:
                merged[key]['score'] = c['score']
                merged[key]['max_slot'] = c['max_slot']
                merged[key]['slot_sum'] = c['slot_sum']
                merged[key]['w_slot_sum'] = c['w_slot_sum']
        else:
            merged[key] = {
                'name': c['name'], 'names': [c['name']],
                'part_idx': c['part_idx'], 'skills': c['skills'],
                'slots': c['slots'], 'weapon_slots': c.get('weapon_slots', []),
                'slots_sorted': c.get('slots_sorted', ()),
                'wslots_sorted': c.get('wslots_sorted', ()),
                'rarity': c['rarity'], 'score': c['score'],
                'max_slot': c['max_slot'],
                'slot_sum': c['slot_sum'], 'w_slot_sum': c['w_slot_sum'],
            }
    candidates = list(merged.values())
    merged_count = len(candidates)

    # 预过滤：保留有孔位或包含需求技能的装备
    # 追加查询时 extra_skill_names 中的技能也需纳入，否则仅提供追加技能的防具被误删
    _filter_skills = merged_needs
    if extra_skill_names:
        _filter_skills = set(merged_needs) | extra_skill_names
    filtered = []
    for c in candidates:
        has_skill = any(s in _filter_skills for s in c['skills'])
        has_slot = (c['slot_sum'] + c['w_slot_sum']) > 0
        if has_skill or has_slot:
            filtered.append(c)
    candidates = filtered

    # 部位级支配预剪枝
    # skip_domination=True时跳过支配剪枝（用于追加搜索共享候选池），
    # 保证所有可能提供追加技能的防具都保留在候选中。
    if not skip_domination:
        protection_skills = dict(merged_needs)
        # 需求中的系列技能（黑蚀龙之力/霸主之魂等）必须纳入支配比较：
        # 忽略它们会把"靠系列技能满足约束"的装备（如黑蚀龙护腿β）误判为被支配而剪掉，
        # 导致合法配装从候选池中消失（等同 protect_no_deco 对需求技能的部分行为）。
        for _s in merged_needs:
            if _s in NO_DECO_SK:
                protection_skills.setdefault(_s, 0)
        if extra_skill_names or protect_no_deco:
            protection_skills = dict(merged_needs)
            if extra_skill_names:
                for _s in extra_skill_names:
                    protection_skills.setdefault(_s, 0)
            if protect_no_deco:
                for _c in candidates:
                    for _s in _c['skills']:
                        if _s in NO_DECO_SK:
                            protection_skills.setdefault(_s, 0)
        part_groups = {}
        for c in candidates:
            part_groups.setdefault(c['part_idx'], []).append(c)
        pruned_candidates = []
        for pi in range(7):
            grp = part_groups.get(pi, [])
            if not grp:
                continue
            # 武器候选（pi=6）不做支配剪枝：武器技能多样且数量可控，
            # 剪枝可能误杀提供未被需求系列/组合技能的候选。
            if pi == 6:
                grp.sort(key=lambda x: (-x['score'], -x['max_slot']))
                pruned_candidates.extend(grp)
                continue
            grp.sort(key=lambda x: (-x['score'], -x['max_slot']))
            kept = []
            for item in grp:
                dominated = False
                for dom in kept:
                    if _dominated_check(item, dom, protection_skills):
                        dominated = True
                        break
                if not dominated:
                    kept.append(item)
            pruned_candidates.extend(kept)
        candidates = pruned_candidates

    candidates.sort(key=lambda x: (-x['score'], -x['max_slot']))

    # 改进排序：系列技能装备优先，然后按score降序
    series_req_set = set(s for s, lv in fixed_skills.items() if s in NO_DECO_SK and lv > 0)
    if combo_skills:
        series_req_set.update(s for s, lv in combo_skills.items() if s in NO_DECO_SK and lv > 0)

    def _sort_key(c):
        # 系列技能数（多优先） + score（高优先） + max_slot（高优先）
        series_cnt = sum(1 for s in c['skills'] if s in series_req_set)
        return (-series_cnt, -c['score'], -c['max_slot'])

    candidates.sort(key=_sort_key)

    # 护石可行性过滤：跳过武器技能赤字检查
    # 武器技能(攻击/看破等)也可以由防具提供，不能仅靠武器孔+护石判断可行性
    charm_cands = [c for c in candidates if c['part_idx'] == 5]
    armor_cands = [c for c in candidates if c['part_idx'] != 5]
    charm_cands.sort(key=lambda c: (-c['score']))
    candidates = charm_cands + armor_cands
    if not quiet:
        orig_count = sum(len(parts[p]) for p in part_names) + len(charm_pool)
        print(f"  候选总数:{orig_count} → 去重{merged_count} → 预过滤{len(candidates)}")

    best_by_part = {}
    best_slot_by_part = {}
    candidates_by_part = {}
    for pi in range(7):
        part_cands = [c for c in candidates if c['part_idx'] == pi]
        # 每个部位内按系列技能优先+score降序排列
        part_cands.sort(key=_sort_key)
        candidates_by_part[pi] = part_cands
        if part_cands:
            best_by_part[pi] = part_cands[0]['score']
            best_slot_by_part[pi] = max(c['slot_sum'] + c['w_slot_sum'] for c in part_cands)
        else:
            best_by_part[pi] = 0
            best_slot_by_part[pi] = 0

    part_series_availability = {}
    all_series_in_gear = set()
    for pi in range(6):
        if pi < 5:
            pn = part_names[pi]
            avail = set()
            for a in parts[pn]:
                for sk_name in a.get('skills', {}):
                    if sk_name in NO_DECO_SK:
                        avail.add(sk_name)
            part_series_availability[pi] = avail
            all_series_in_gear |= avail
        else:
            # charm (pi=5) and weapon (pi=6)
            avail = set()
            pool = charm_pool if pi == 5 else weapon_pool
            for c in pool:
                for sk_name in c.get('skills', {}):
                    if sk_name in NO_DECO_SK:
                        avail.add(sk_name)
            part_series_availability[pi] = avail
            all_series_in_gear |= avail

    return (candidates, all_skill_names, weapon_skills, armor_fixed, weapon_fixed,
            best_by_part, best_slot_by_part, candidates_by_part, part_series_availability)


# ==================== 向量化DFS搜索 ====================
def dfs_search(charm_pool, fixed_skills, combo_skills, min_rem_armor,
               max_results=0, timeout_s=10.0, quiet=False, cached_ctx=None,
               min_rem_weapon=0, user_weapon_skills=None, timeout_flag=None):
    """DFS回溯搜索（向量化优化版 v3）

    核心优化（参照网页配装器策略）：
    1. 技能→索引映射：所有需求技能映射为整数索引，用list代替dict做累加
    2. 候选装备预计算技能向量：每件装备的技能贡献转为定长tuple
    3. 精确赤字向量：用list做加减，避免dict.get开销
    4. 逐技能可行性剪枝：每个赤字技能检查剩余部位能否提供
    5. 分数上限剪枝：剩余部位最高分+孔位容量 < 当前赤字 → 剪除
    """
    start_time = time.time()
    _TRACE = globals().get('_TRACE', False)
    _DOM_MID = globals().get('_DOM_MID', False)  # 搜索中间支配检查开关（A/B 对比用）
    # 注意：支配剪枝只保"最优解"语义，会把被支配的合法解剪掉（漏解）。
    # 枚举全部解时必须关闭；需要"仅最优"时由调用方显式开启。
    part_names = ['head', 'body', 'arms', 'waist', 'legs']

    fixed_skills = _normalize_skills_dict(fixed_skills)
    combo_skills = _normalize_skills_dict(combo_skills)
    if user_weapon_skills is not None:
        user_weapon_skills = _normalize_skills_dict(user_weapon_skills)

    if cached_ctx is not None:
        (candidates, all_skill_names, _cached_weapon_skills, armor_fixed, _cached_weapon_fixed,
         best_by_part, best_slot_by_part, candidates_by_part, part_series_availability) = cached_ctx
        # 从当前的 user_weapon_skills 重新计算 weapon_skills 和 weapon_fixed
        if user_weapon_skills is not None:
            weapon_skills = {}
            for sk, lv in user_weapon_skills.items():
                weapon_skills[sk] = lv
            weapon_fixed = {s: r for s, r in fixed_skills.items() if s in WEAPON_SK}
        else:
            weapon_skills = _cached_weapon_skills
            weapon_fixed = _cached_weapon_fixed
    else:
        ctx = _build_candidates(charm_pool, fixed_skills, combo_skills, quiet=quiet,
                                user_weapon_skills=user_weapon_skills,
                                skip_domination=globals().get('_SKIP_DOMINATION', False))
        (candidates, all_skill_names, weapon_skills, armor_fixed, weapon_fixed,
         best_by_part, best_slot_by_part, candidates_by_part, part_series_availability) = ctx

    # ===== 技能→索引映射 =====
    tracked_skills = []
    for s in fixed_skills:
        if s in NO_DECO_SK:
            continue
        tracked_skills.append(s)
    if combo_skills:
        for s in combo_skills:
            if s in NO_DECO_SK:
                continue
            if s not in tracked_skills:
                tracked_skills.append(s)
    n_skills = len(tracked_skills)
    skill_idx = {s: i for i, s in enumerate(tracked_skills)}
    # 支配比较技能集：覆盖全部需求技能（含系列技能，如 黑蚀龙之力/霸主之魂）。
    # tracked_skills 为向量体系排除了 NO_DECO_SK，但支配剪枝若也忽略系列技能，
    # 会把"靠系列技能才满足约束"的候选（如黑蚀龙护腿β）误判为被支配而剪掉。
    dom_skills = list(tracked_skills)
    for _s in list(fixed_skills) + list(combo_skills or {}):
        if _s not in dom_skills and not _s.endswith('插槽'):
            dom_skills.append(_s)

    # ===== 变体等价组映射（参照网页版 ed/fd 聚合：同部位+同稀有度+同需求技能贡献）=====
    # 用于结果展开：需求维度支配会误删 α/β 变体（如黑蚀龙护腿α 被 β 支配，
    # 因需求贡献相同、β 孔更多），导致漏解。这里构建"需求贡献相同"的变体组，
    # 在结果记录时逐个展开输出，补齐 α/β 等合法解（参照网页版 Ba/Yc 展开）。
    _variant_map = {}    # part_idx -> {需求技能贡献签名 -> [变体名]}
    _name_to_armor = {}  # 防具名 -> 防具原始数据
    _demand_sigs = set(fixed_skills.keys())
    if combo_skills:
        _demand_sigs |= set(combo_skills.keys())
    _demand_sigs = {_s for _s in _demand_sigs if not _s.endswith('插槽')}
    for _vpi, _vpn in enumerate(['head', 'body', 'arms', 'waist', 'legs']):
        _vgroups = {}
        for _va in parts[_vpn]:
            _name_to_armor[_va['name']] = _va
            _vsig = tuple(sorted((_s, _lv) for _s, _lv in (_va.get('skills') or {}).items()
                                 if _s in _demand_sigs))
            # 按需求技能贡献分组（忽略稀有度），使需求贡献相同的 α/β、祭典护腿α+
            # 等不同防具在结果展开时能被补齐（需求维度支配会剪掉部分合法防具）
            _vgroups.setdefault(_vsig, []).append(_va['name'])
        _variant_map[_vpi] = _vgroups

    # 需求向量
    need_vec = [0] * n_skills
    for s, i in skill_idx.items():
        need = fixed_skills.get(s, 0)
        if s in weapon_fixed:
            need = max(need, weapon_fixed[s])
        if combo_skills and s in combo_skills:
            need = max(need, combo_skills[s])
        need_vec[i] = need

    # 初始技能向量（武器自带技能）
    init_skills_vec = [0] * n_skills
    for s, lv in weapon_skills.items():
        if s in skill_idx:
            init_skills_vec[skill_idx[s]] = lv

    # 初始赤字向量
    init_deficit = [max(0, need_vec[i] - init_skills_vec[i]) for i in range(n_skills)]
    init_def_score = sum(init_deficit)

    # 武器技能索引集合（提前定义，供候选向量构建使用）
    weapon_sk_idx = frozenset(_i for _i in range(n_skills) if tracked_skills[_i] in WEAPON_SK)
    is_weapon_deco = [False] * n_skills
    for _i in range(n_skills):
        is_weapon_deco[_i] = tracked_skills[_i] in WEAPON_SK

    # ===== 系列技能需求预计算（提前到候选构建之前）=====
    all_series_req = {}
    for ss, lv in fixed_skills.items():
        if ss in NO_DECO_SK:
            all_series_req[ss] = lv
    if combo_skills:
        for ss, lv in combo_skills.items():
            if ss in NO_DECO_SK:
                all_series_req[ss] = max(all_series_req.get(ss, 0), lv)

    # 系列技能→位掩码映射
    series_bit_map = {}
    if all_series_req:
        for b, ss in enumerate(NO_DECO_SK):
            if ss in all_series_req:
                series_bit_map[ss] = b

    # 每个系列在防具5个部位中的总可用件数
    _series_total = {}
    if all_series_req:
        for ss in all_series_req:
            _series_total[ss] = sum(1 for j in range(5) if ss in part_series_availability.get(j, set()))

    # 需求系列技能的位掩码（用OR合并为单个整数）
    req_series_mask = 0
    for ss in all_series_req:
        if ss in series_bit_map:
            req_series_mask |= (1 << series_bit_map[ss])

    # ===== 候选装备预计算技能向量 =====
    # 每件装备转为 (skill_vec, slot_tuple, wslot_tuple, score, max_slot, series_bits, name, names)
    part_cands_vec = {}  # {part_idx: [vec_item, ...]}
    bit_map = {ss: b for b, ss in enumerate(NO_DECO_SK) if ss in all_skill_names}
    for pi in range(7):
        raw_cands = candidates_by_part.get(pi, [])
        vec_list = []
        for c in raw_cands:
            # 技能向量
            sv = [0] * n_skills
            has_wsk = False
            wsk_pts = 0
            nz_indices = []  # 非零技能索引列表
            for s, lv in c['skills'].items():
                if s in skill_idx:
                    idx_val = skill_idx[s]
                    sv[idx_val] = lv
                    nz_indices.append((idx_val, lv))
                    if is_weapon_deco[idx_val]:
                        has_wsk = True
                        wsk_pts += lv
            # 系列技能位掩码
            series_bits = 0
            for s in c['skills']:
                if s in NO_DECO_SK and s in all_skill_names:
                    series_bits |= (1 << bit_map[s])
            # 预计算：是否含需求系列技能件
            has_req_series = bool(series_bits & req_series_mask) if req_series_mask else False
            vec_list.append({
                'name': c['name'], 'names': c.get('names', [c['name']]),
                'part_idx': pi,
                'sv': tuple(sv), 'nz': tuple(nz_indices),  # 非零技能索引
                'slots': c['slots'],
                'slots_sorted': c.get('slots_sorted', ()),
                'weapon_slots': c.get('weapon_slots', []),
                'wslots_sorted': c.get('wslots_sorted', ()),
                'score': c['score'], 'max_slot': c['max_slot'],
                'slot_sum': c['slot_sum'], 'w_slot_sum': c['w_slot_sum'],
                'series_bits': series_bits,
                '_has_wsk': has_wsk,
                'wsk_pts': wsk_pts,
                '_has_req_series': has_req_series,
                'skills': c['skills'],
            })
        # ===== 等价候选桶合并（参考项目 Pd 聚合）=====
        # 需求技能贡献签名 nz（含插槽技能，保证 armor_min 一致）+ 槽型签名完全相同的
        # 候选完全等价（任何解可相互替换），每桶只留分数最高的代表。
        # 含需求系列技能件的候选保留个体（替换会破坏系列件数约束）。
        if len(vec_list) > 8:
            _keep = []
            _buckets = {}
            for v in vec_list:
                if v['_has_req_series']:
                    _keep.append(v)
                else:
                    _key = (frozenset(v['skills'].items()), v['slots_sorted'], v['wslots_sorted'])
                    _b = _buckets.get(_key)
                    if _b is None or v['score'] > _b['score']:
                        _buckets[_key] = v
            vec_list = _keep + list(_buckets.values())
        # ===== 候选级支配过滤（参照项目 Qb：剔除被支配候选）=====
        # A 支配 B：**需求技能**（init_deficit>0）贡献、防具槽位容量、武器槽位容量、
        # 分数全部 >= B。任何用 B 的可行解替换为 A 后所有约束不弱化（需求技能更多/
        # 孔更多/分更高），非需求技能是"白送"不影响约束，故用需求维度而非全技能维度
        # （此前用全技能比较，绝大多数装备都有独有技能，零剔除，树膨胀到 15 亿组合）。
        # 含需求系列技能件的候选保留（替换会破坏系列件数约束）。
        _def_dims = tuple(_i for _i in range(n_skills) if init_deficit[_i] > 0) or tuple(range(n_skills))
        if len(vec_list) > 1:
            _survivors = []
            _has_slot = (pi in (0, 1, 2, 3, 4, 5))  # 防具5部位+护石
            for _b in vec_list:
                if _b['_has_req_series']:
                    _survivors.append(_b)
                    continue
                _dom = False
                for _a in vec_list:
                    if _a is _b or _a['_has_req_series']:
                        continue
                    _a_sv = _a['sv']
                    _b_sv = _b['sv']
                    _ok = True
                    for _i in _def_dims:
                        if _a_sv[_i] < _b_sv[_i]:
                            _ok = False
                            break
                    if not _ok:
                        continue
                    if _a['score'] < _b['score']:
                        continue
                    if _has_slot:
                        _sa = _a['slots_sorted']
                        _sb = _b['slots_sorted']
                        for _n in (1, 2, 3):
                            if sum(1 for s in _sa if s >= _n) < sum(1 for s in _sb if s >= _n):
                                _ok = False
                                break
                        if not _ok:
                            continue
                    _wa = _a['wslots_sorted']
                    _wb = _b['wslots_sorted']
                    if _wa or _wb:
                        for _n in (1, 2, 3):
                            if sum(1 for s in _wa if s >= _n) < sum(1 for s in _wb if s >= _n):
                                _ok = False
                                break
                        if not _ok:
                            continue
                    _dom = True
                    break
                if not _dom:
                    _survivors.append(_b)
            if globals().get('_DIAG', False):
                print(f"   >> Qb part{pi}: {len(vec_list)} -> {len(_survivors)}")
            vec_list = _survivors
        # 按score降序排序：per-candidate上界break依赖"候选分数单调不增"才严格成立
        # （否则低分候选提前触发break会误杀后面的高分候选），且高分优先探索可更早
        # 命中正解、让结果支配剪枝(_DOM_MID)尽早生效。
        vec_list.sort(key=lambda c: c['score'], reverse=True)
        part_cands_vec[pi] = vec_list

    # ===== 全局预检查 =====
    for sk in fixed_skills:
        if sk in NO_DECO_SK:
            continue
        if _is_slot_skill(sk):
            # 插槽技能不需要珠子，由装备孔位满足，不判死
            continue
        if sk in WEAPON_SK:
            pool = deco_idx.get((sk, 'weapon'), [])
        else:
            pool = deco_idx.get((sk, 'armor'), [])
        has_in_gear = any(sk in a['skills'] for p in part_names for a in parts[p])
        has_in_charm = any(sk in c.get('skills', {}) for c in charm_pool)
        if not pool and not has_in_gear and not has_in_charm:
            if not quiet:
                print(f"  预检查: {sk}无珠子且装备中不存在→无解")
            return []

    # 系列/组合技能件数检查
    for ss in fixed_skills:
        if ss not in NO_DECO_SK:
            continue
        need_lv = fixed_skills[ss]
        need_pieces = max(1, need_lv)
        weapon_provided = combo_skills.get(ss, 0) > 0 if combo_skills else False
        avail_pieces = (1 if weapon_provided else 0)
        avail_pieces += sum(1 for p in part_names
                           if any(ss in a.get('skills', {}) for a in parts[p]))
        # 武器候选池中的系列技能也计入可用件数
        avail_pieces += sum(1 for w in weapon_pool
                           if ss in w.get('skills', {}))
        if avail_pieces < need_pieces:
            if not quiet:
                print(f"  预检查: {ss}需要{need_pieces}件但只有{avail_pieces}件→无解")
            return []

    # 初始赤字过大检查
    max_possible_cap = sum(WSLOTS)
    for p in part_names:
        if parts[p]:
            max_a = max(sum(a['slots']) for a in parts[p])
            max_possible_cap += max_a
    if charm_pool:
        max_c = max(sum(c.get('armor_slots', [])) + sum(c.get('weapon_slots', [])) for c in charm_pool)
        max_possible_cap += max_c
    if init_def_score > max_possible_cap:
        if not quiet:
            print(f"  预检查: 初始赤字{init_def_score}>{max_possible_cap}→无解")
        return []

    # 武器技能赤字检查：只检查完全没有珠子且护石也没有的技能
    # 注意：武器技能也可以由防具提供，所以不能仅检查武器孔
    w_deficit = {tracked_skills[i]: init_deficit[i] for i in range(n_skills)
                 if init_deficit[i] > 0 and tracked_skills[i] in WEAPON_SK}
    if w_deficit:
        for s, d in w_deficit.items():
            pool = deco_idx.get((s, 'weapon'), [])
            best_charm_lv = max((c.get('skills', {}).get(s, 0) for c in charm_pool), default=0)
            has_armor_source = any(a.get('skills', {}).get(s, 0) > 0 for p in part_names for a in parts[p])
            if not pool and best_charm_lv < d and not has_armor_source:
                if not quiet:
                    print(f"  预检查: {s}无珠子且护石最高Lv{best_charm_lv}<需{d}且防具也无→无解")
                return []

    # 武器孔位容量预检查：确保纯武器技能赤字能在可用武器孔内装下
    w_total_need = 0
    for i in range(n_skills):
        sk = tracked_skills[i]
        if sk not in WEAPON_SK:
            continue
        d = init_deficit[i]
        if d <= 0:
            continue
        has_armor = any(a.get('skills', {}).get(sk, 0) > 0 for p in part_names for a in parts[p])
        if has_armor:
            continue
        has_charm = any(c.get('skills', {}).get(sk, 0) > 0 for c in charm_pool)
        if has_charm:
            continue
        pool = deco_idx.get((sk, 'weapon'), [])
        if not pool:
            continue
        w_total_need += d
    if w_total_need > 0:
        w_total_slots = sum(WSLOTS) + sum(sum(c.get('weapon_slots', [])) for c in charm_pool)
        w_max_pts = 1
        for sk in tracked_skills:
            if sk in WEAPON_SK:
                pool = deco_idx.get((sk, 'weapon'), [])
                if pool:
                    mp = max(pts for sr, pts, dn in pool)
                    if mp > w_max_pts:
                        w_max_pts = mp
        w_slots_needed = (w_total_need + w_max_pts - 1) // w_max_pts
        if w_slots_needed > w_total_slots:
            if not quiet:
                print(f"  预检查: 武器技能赤字{w_total_need}点需{w_slots_needed}个武器孔，但只有{w_total_slots}个→无解")
            return []

    # ===== 搜索状态 =====
    results = []
    # 配装去重：同一套装备可能被 _try_early_fill 补位路径与叶子路径双计
    # （固定武器下二者填充结果完全相同），按装备件名签名去重，避免重复解。
    _seen_sets = set()
    equipped = [None] * 7
    # 增量维护的全技能dict（含非追踪技能，供fill_slots用），放置/撤销时同步更新，
    # 避免叶子处从6件装备重建
    # 注意：不预置 weapon_skills。weapon_skills 是从 combo 需求推导的"假设武器技能"，
    # 预置后再放置武器会双计（如黑蚀龙之力显示2但实际武器只提供1）。
    # 实际放置的武器技能会在放置时真实累加。
    _cur_all_skills = {}

    # 原地状态变量
    _skills_vec = list(init_skills_vec)  # 当前技能向量
    _deficit = list(init_deficit)        # 当前赤字向量
    _def_score = init_def_score          # 当前赤字总分
    _a_slots = []                        # 当前防具孔位
    _w_slots = list(WSLOTS)              # 当前武器孔位
    _series_count = {}                   # 当前各系列技能件数
    _armor_filled = 0                    # 已装备的防具件数
    _a_slot_sum = 0                      # 防具孔位总容量
    _w_slot_sum = sum(WSLOTS)            # 武器孔位总容量

    # 增量维护的slot计数数组 [0]=count_lv1, [1]=count_lv2, [2]=count_lv3
    # 替代每次遍历_a_slots/_w_slots的O(n)操作
    _a_slot_cnt = [0, 0, 0, 0]  # idx 1~3
    _w_slot_cnt = [0, 0, 0, 0]
    for _s in WSLOTS:
        if 0 < _s <= 3:
            _w_slot_cnt[_s] += 1

    # ===== 系列技能需求列表（提前定义供状态变量和预计算使用）=====
    _req_series_list = list(all_series_req.keys()) if all_series_req else []
    _n_req_series = len(_req_series_list)
    # 系列技能件数增量数组（与_req_series_list对齐）
    _series_have = [0] * _n_req_series if _n_req_series > 0 else []
    # 预计算系列技能→索引映射
    _series_idx_map = {}
    if _n_req_series > 0:
        for _si, _ss in enumerate(_req_series_list):
            _series_idx_map[_ss] = _si
    # 预计算系列技能需求件数
    _series_need_pieces = []
    for _ss in _req_series_list:
        _nlv = all_series_req[_ss]
        _series_need_pieces.append(max(1, _nlv))
    # 武器提供的系列件数
    _series_wprov = [0] * _n_req_series
    if weapon_skills and _n_req_series > 0:
        for _ss, _lv in weapon_skills.items():
            if _ss in _series_idx_map:
                _series_wprov[_series_idx_map[_ss]] = 1

    # 武器技能赤字总量（增量维护，消除DFS内的any()遍历）
    _w_def_total = sum(init_deficit[i] for i in range(n_skills) if is_weapon_deco[i])
    _w_def_total = [_w_def_total]  # list做nonlocal替代

    # ===== 部位重排序：防具按候选数升序，护石与武器放最后 =====
    # 护石放首位会让 DFS 在每个护石分支下遍历全部防具组合，正解护石靠后时
    # 需先遍历完前面护石的全部分支；护石放最后则先剪防具组合树，再试少量护石。
    part_order = sorted(range(7), key=lambda pi: (
        pi == 6,  # weapon (pi=6) goes last
        pi == 5,  # charm (pi=5) second-to-last
        len(part_cands_vec.get(pi, []))
    ))
    if globals().get('_TRACE'):
        print(f"[DBG] part_order={part_order}")
        for _pi in range(7):
            _names = [c['name'] for c in part_cands_vec.get(_pi, [])]
            _hit = [n for n in _names if n in ('踊火护腕α', '踊火护腕α+', '雪狮子王腰甲β+', '雪狮子王腰甲β', '狱焰蛸头盔γ', '冻峰龙铠甲γ', '黑蚀龙护腿β')]
            print(f"[DBG] part{_pi} count={len(_names)} 正解相关={_hit}")
            if _pi == 2:
                print(f"[DBG]   arms前12: {_names[:12]}")

    # 预计算剩余部位的最佳score累计和
    remaining_best_sum = [0] * 8
    for _d in range(7, -1, -1):
        _pi = part_order[_d] if _d < 7 else -1
        if _d < 7:
            remaining_best_sum[_d] = remaining_best_sum[_d + 1] + best_by_part.get(_pi, 0)
        else:
            remaining_best_sum[_d] = 0

    # 预计算每个系列技能在剩余部位中的最大可用件数（逐部位系列件数上界剪枝）
    # remaining_series_max[depth][ss_idx] = 从depth层开始剩余部位能提供的该系列最大件数
    # 注意：武器层（part_idx==6）的贡献由 _series_wprov 预置代表（combo需求推导的
    # "武器必提供1件"假设）。若武器层再计入上界，会把武器件数重复计（宽松、剪枝失效），
    # 导致"缺的件数只能由防具补"的分支无法剪掉、全部跑到叶子才失败（性能爆炸）。
    # 因此：_series_wprov 已假设该系列时武器层上界计0；未假设时才计1（武器仍可提供）。
    remaining_series_max = None
    if _n_req_series > 0:
        remaining_series_max = [[0] * _n_req_series for _ in range(8)]
        for _d in range(6, -1, -1):
            _pi = part_order[_d]
            _r_next = remaining_series_max[_d + 1]
            if _pi == 6:
                remaining_series_max[_d] = [
                    _r_next[_si] + (0 if _series_wprov[_si] else 1)
                    for _si in range(_n_req_series)]
            else:
                _lst = list(_r_next)
                for _si, _ss in enumerate(_req_series_list):
                    for _c in part_cands_vec.get(_pi, []):
                        if _ss in _c['skills']:
                            _lst[_si] += 1  # 每部位最多选1件
                            break
                remaining_series_max[_d] = _lst

    # 预计算每个技能在剩余部位中的最大可用量（逐技能上界剪枝）
    remaining_skill_max = [[0] * n_skills for _ in range(8)]
    for _d in range(6, -1, -1):
        _pi = part_order[_d]
        for _i in range(n_skills):
            _max_in_part = 0
            for _c in part_cands_vec.get(_pi, []):
                _sv = _c['sv']
                if _sv[_i] > _max_in_part:
                    _max_in_part = _sv[_i]
            remaining_skill_max[_d][_i] = remaining_skill_max[_d + 1][_i] + _max_in_part

    # 预计算剩余部位技能总点数的可达上界（每部位取"单件候选技能点数最大"累加）。
    # 逐技能独立取最大（remaining_skill_max）在组合上不可达：选"挑战者最多"的装备
    # 就选不了"耳塞最多"的装备。各部位技能点数最大累加是真实可达的装备总技能上界，
    # 用于约束预支总量，防止 depth 浅层把赤字全部预支掉、无解场景判不死。
    remaining_skill_total = [0] * 8
    for _d in range(6, -1, -1):
        _pi = part_order[_d]
        _mx = 0
        for _c in part_cands_vec.get(_pi, []):
            _s = sum(_c['sv'][_i] for _i in range(n_skills) if _c['sv'][_i] > 0)
            if _s > _mx:
                _mx = _s
        remaining_skill_total[_d] = remaining_skill_total[_d + 1] + _mx

    # 预计算剩余部位各等级slot的最大可用数（精确slot上界剪枝）
    # remaining_slot_by_lv[depth] = (a_lv1, a_lv2, a_lv3, w_lv1, w_lv2, w_lv3)
    # 每个部位只能选1件装备，故各等级槽位数取"单件候选"的最大值，而非全候选累加。
    # 旧版全候选累加会把上界虚高十几倍（如头部位15件候选各2个Lv3孔→30），
    # 使 _greedy_deco_check_with_future 的降级链检查几乎永不触发，无解场景
    # 只能一路搜到叶子 fill_slots 才失败，导致搜索无法快速确认无解。
    # 每部位选"槽位点数最大"的单件候选，累加其完整槽位分布（Lv1/Lv2/Lv3 来自同一件）。
    # 旧版各等级独立取单件最大，仍会把上界虚高（如某件[3]与另一件[2,2]的Lv1最大
    # 都累加），使降级链检查无法反映真实孔位容量、无解场景判不死。
    remaining_slot_by_lv = [[0]*6 for _ in range(8)]
    for _d in range(6, -1, -1):
        _pi = part_order[_d]
        _best_a = [0, 0, 0, 0]  # 单件候选的完整槽位分布（点数最大者）
        _best_a_pts = -1
        for _c in part_cands_vec.get(_pi, []):
            _ca = [0, 0, 0, 0]
            _pa = 0
            for _s in _c['slots']:
                if 0 < _s <= 3:
                    _ca[_s] += 1
                    _pa += _s
            # 点数最大；点数相同取高等级孔多者（更宽松的上界）
            if _pa > _best_a_pts or (_pa == _best_a_pts and (_ca[3], _ca[2], _ca[1]) > (_best_a[3], _best_a[2], _best_a[1])):
                _best_a_pts = _pa
                _best_a = _ca
        _prev = remaining_slot_by_lv[_d + 1]
        for _lv in range(1, 4):
            remaining_slot_by_lv[_d][_lv - 1] = _prev[_lv - 1] + _best_a[_lv]
        # 武器部位槽位固定为 WSLOTS（已由 _w_slot_cnt 初始化计入），
        # 不随候选重复累加——否则武器孔被双重计入、w_cap 虚高导致武器侧判死失效。
        for _lv in range(1, 4):
            remaining_slot_by_lv[_d][_lv + 2] = _prev[_lv + 2]

    # 预计算每个技能的珠子最大等级和slot等级
    best_deco_pts = [0] * n_skills
    best_deco_slot = [0] * n_skills
    # 珠子slot等级（用于贪心珠子检查的slot降级链，无珠子=100）
    deco_weight = [100] * n_skills
    for _i in range(n_skills):
        _sk_name = tracked_skills[_i]
        if _sk_name in WEAPON_SK:
            _pool = deco_idx.get((_sk_name, 'weapon'), [])
        else:
            _pool = deco_idx.get((_sk_name, 'armor'), [])
        if _pool:
            # 选pts最高、且同pts时槽位最小的珠子：
            # 低槽位珠更通用（Lv1可插任何孔），避免把技能误算成必须Lv2/Lv3孔。
            _best_entry = max(_pool, key=lambda x: (x[1], -x[0]))
            best_deco_pts[_i] = _best_entry[1]
            best_deco_slot[_i] = _best_entry[0]
            deco_weight[_i] = _best_entry[0]

    # ===== 赤字slot需求（增量维护，替代def_weight）=====
    # 每个赤字技能需要的slot数 = ceil(deficit / best_pts)
    init_a_slot_demand = 0
    init_w_slot_demand = 0
    slot_demand_per_skill = [0] * n_skills  # 每个技能的slot需求（预计算）
    for _i in range(n_skills):
        _d = init_deficit[_i]
        if _d <= 0:
            continue
        if best_deco_pts[_i] > 0:
            _sn = (_d + best_deco_pts[_i] - 1) // best_deco_pts[_i]
            slot_demand_per_skill[_i] = _sn
            if is_weapon_deco[_i]:
                init_w_slot_demand += _sn
            else:
                init_a_slot_demand += _sn

    # 增量维护的slot需求（用list做nonlocal替代）
    _a_slot_demand = [init_a_slot_demand]
    _w_slot_demand = [init_w_slot_demand]

    # 预计算剩余部位的最大slot总数（用于贪心触发判断）
    remaining_max_slot_sum = [0] * 8
    for _d in range(6, -1, -1):
        _pi = part_order[_d]
        _max_ss = 0
        for _c in part_cands_vec.get(_pi, []):
            _ss = _c['slot_sum'] + _c.get('w_slot_sum', 0)
            if _ss > _max_ss:
                _max_ss = _ss
        remaining_max_slot_sum[_d] = remaining_max_slot_sum[_d + 1] + _max_ss

    # 预计算剩余部位的最大武器槽总数（用于评分break的武器插珠价值估算）
    remaining_wslot_max = [0] * 8
    for _d in range(6, -1, -1):
        _pi = part_order[_d]
        _max_w = 0
        for _c in part_cands_vec.get(_pi, []):
            _sw = sum(_c.get('weapon_slots', []))
            if _sw > _max_w:
                _max_w = _sw
        remaining_wslot_max[_d] = remaining_wslot_max[_d + 1] + _max_w

    # 保留def_weight用于兼容（但不再作为主触发条件）
    init_def_weight = sum(deco_weight[_i] * init_deficit[_i]
                          for _i in range(n_skills) if init_deficit[_i] > 0)
    _def_weight = [init_def_weight]

    # ===== 预计算孔位技能需求（避免每次遍历fixed_skills）=====
    armor_min = {}
    weapon_min = {}
    for _sk, _lv in fixed_skills.items():
        if not _sk.endswith('插槽'):
            continue
        try:
            if _sk.startswith('防具Lv'):
                _n = int(_sk[4:-2])
                armor_min[_n] = armor_min.get(_n, 0) + _lv
            elif _sk.startswith('武器Lv'):
                _n = int(_sk[4:-2])
                weapon_min[_n] = weapon_min.get(_n, 0) + _lv
            elif _sk.startswith('Lv'):
                _n = int(_sk[2:-2])
                armor_min[_n] = armor_min.get(_n, 0) + _lv
        except ValueError:
            pass

    # 预排序：armor_min/weapon_min 搜索期固定，避免 _g6_core 等热路径每次重复排序
    # （profile: sorted 被调用 436 万次，其中大部分来自 _g6_core 的 200 万次调用）
    armor_min_items = tuple(sorted(armor_min.items(), reverse=True))
    weapon_min_items = tuple(sorted(weapon_min.items(), reverse=True))

    # ===== 贪心珠子填充检查（网页配装器ta().b()）=====
    def _greedy_deco_check():
        """O(技能数)贪心检查：赤字能否用珠子填满（用增量slot计数优化）"""
        a_cnt = [_a_slot_cnt[0], _a_slot_cnt[1], _a_slot_cnt[2], _a_slot_cnt[3]]
        w_cnt = [_w_slot_cnt[0], _w_slot_cnt[1], _w_slot_cnt[2], _w_slot_cnt[3]]
        # 预留孔位扣减
        _rem = min_rem_armor
        for _lv in [1, 2, 3]:
            while _rem > 0 and a_cnt[_lv] > 0:
                a_cnt[_lv] -= 1
                _rem -= 1
        if _rem > 0:
            return False
        # 孔位技能需求：最小等级匹配（LvN可消耗>=N的槽位）
        # 就地复用 a_cnt/w_cnt：3 处调用点均为新建列表且拷贝后不再使用原变量
        # （_greedy_deco_check/_greedy_deco_check_with_future/_strict_leaf_check
        #  各新建 a_cnt/w_cnt；_g6_core 由这两个调用方传入新建列表）
        _a_tmp = a_cnt
        _w_tmp = w_cnt
        for _lv, _need in armor_min_items:
            _avail = sum(_a_tmp[_lv:])
            if _avail < _need:
                return False
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _a_tmp[_n])
                _a_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        for _lv, _need in weapon_min_items:
            _avail = sum(_w_tmp[_lv:])
            if _avail < _need:
                return False
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _w_tmp[_n])
                _w_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        # 计算珠子需求（按slot等级分桶）
        a_need = [0, 0, 0, 0]
        w_need = [0, 0, 0, 0]
        for _i in range(n_skills):
            _d = _deficit[_i]
            if _d <= 0:
                continue
            if best_deco_pts[_i] == 0:
                if _is_slot_skill(tracked_skills[_i]):
                    continue  # 插槽技能无珠子，由装备孔位满足
                return False  # 无珠子可用
            _slots_needed = (_d + best_deco_pts[_i] - 1) // best_deco_pts[_i]
            if is_weapon_deco[_i]:
                w_need[best_deco_slot[_i]] += _slots_needed
            else:
                a_need[best_deco_slot[_i]] += _slots_needed
        # slot降级链检查（网页配装器核心）
        _a_r1 = a_cnt[1] - a_need[1]
        _a_r2 = a_cnt[2] - a_need[2] + (_a_r1 if _a_r1 < 0 else 0)
        _a_r3 = a_cnt[3] - a_need[3] + (_a_r2 if _a_r2 < 0 else 0)
        if _a_r3 < 0:
            return False
        _w_r1 = w_cnt[1] - w_need[1]
        _w_r2 = w_cnt[2] - w_need[2] + (_w_r1 if _w_r1 < 0 else 0)
        _w_r3 = w_cnt[3] - w_need[3] + (_w_r2 if _w_r2 < 0 else 0)
        if _w_r3 < 0:
            return False
        return True

    # 预计算merged_fixed（叶子记录用，整个搜索期间不变，避免每叶子重建）
    merged_fixed_once = dict(fixed_skills)
    if combo_skills:
        for s, r in combo_skills.items():
            if s in NO_DECO_SK:
                continue
            merged_fixed_once[s] = max(merged_fixed_once.get(s, 0), r)
    # 武器提供的系列/组合件数（预计算，配合_series_count替代每叶子重数）
    _weapon_series_cnt = {}
    for sk, lv in weapon_skills.items():
        if sk in NO_DECO_SK and lv > 0:
            _weapon_series_cnt[sk] = 1

    # ===== 搜索中间支配表（参照版 e() case5 jf(u,hb)）=====
    # 记录每个已找到结果中各部位装备的"帕累托最小支配集"：
    # 若候选 Q 被支配集中任意装备 E 支配（同部位、E 孔位/技能全面不弱于 Q），
    # 则任何含 Q 的配装把 Q 替换为 E 后不差且约束更易满足，故 Q 不可能出现在最优解中，
    # 直接剪枝跳过（正确性无损）。
    _dom_table = {pi: [] for pi in range(7)}

    def _register_result_dom(equipped):
        # 把新结果的 7 件装备按部位注册进支配表，保持每部位为"最小支配集"
        for _e in equipped:
            if _e is None:
                continue
            _pi = _e['part_idx']
            _lst = _dom_table[_pi]
            # 新装备支配的旧支配装备可移除（新装备能剪的候选更全）
            for _old in list(_lst):
                if _dominated_check(_old, _e, dom_skills):
                    _lst.remove(_old)
            # 新装备若已被某旧支配装备支配则无需加入（旧装备剪枝能力不弱于它）
            _dominated = False
            for _old in _lst:
                if _dominated_check(_e, _old, dom_skills):
                    _dominated = True
                    break
            if not _dominated:
                _lst.append(_e)

    def _try_fill_and_record(incremental=False):
        # 可行性预检前置：incremental路径（depth>=6，6件已全部放置）先用增量严格检查
        # （绝大多数叶子在此被拒），通过后才准备数据，避免每叶子白做重建。
        # incremental=False（_try_early_fill补位路径，增量状态不含补位件）退回重建式检查。
        if incremental:
            if not _strict_leaf_check():
                if _TRACE:
                    print(f"   >> LEAF-FAIL strict_leaf_check")
                return False
            # 增量状态与实际选择严格一致，直接用，无需重建
            # （_a_slots/_w_slots已含武器基底WSLOTS，fill_slots内部会自行复制/排序）
            cur_skills = _cur_all_skills
            a_s = _a_slots
            w_s = _w_slots
        else:
            if any(equipped[j] is None for j in range(7)):
                return False
            # 重建技能dict（用于fill_slots）
            cur_skills = dict(weapon_skills)
            for e in equipped:
                if e:
                    for s, lv in e['skills'].items():
                        cur_skills[s] = cur_skills.get(s, 0) + lv
            a_s, w_s = [], list(WSLOTS)
            for e in equipped:
                if e:
                    a_s.extend(e['slots'])
                    if e.get('weapon_slots') and e.get('part_idx') != 6:
                        w_s.extend(e['weapon_slots'])
            if not _check_deco_feasible(cur_skills, a_s, w_s, merged_fixed_once, {},
                                        weapon_skills, min_rem_armor, min_rem_weapon):
                return False
        filled = fill_slots(cur_skills, a_s, w_s, merged_fixed_once, min_keep_armor=min_rem_armor, min_keep_weapon=min_rem_weapon)
        if filled is None:
            if _TRACE:
                print(f"   >> LEAF-FAIL fill_slots cur_skills={ {k:v for k,v in cur_skills.items() if v>0} } a_s={sorted([x for x in a_s if x>0])} w_s={sorted([x for x in w_s if x>0], reverse=True)}")
            return False
        fs, used, rem_a, rem_w = filled
    
        if incremental:
            # 增量系列计数与当前选择严格一致，直接用
            _sp = _series_count
            _wsp = _weapon_series_cnt
        else:
            # 补位路径：增量状态不含补位件，从equipped重数
            # 只数防具/护石件数；武器件数由 _weapon_series_cnt 提供，避免双计
            _sp = {}
            for e in equipped:
                if e is None or e.get('part_idx') == 6:
                    continue
                for sk in e.get('skills', {}):
                    if sk in NO_DECO_SK:
                        _sp[sk] = _sp.get(sk, 0) + 1
            _wsp = _weapon_series_cnt
    
        for s, r in fixed_skills.items():
            if _is_slot_skill(s):
                continue
            if s in NO_DECO_SK:
                if _sp.get(s, 0) + _wsp.get(s, 0) < r:
                    if _TRACE:
                        print(f"   >> LEAF-FAIL fixed-series {s}: sp={_sp.get(s,0)} wsp={_wsp.get(s,0)} need={r}")
                    return False
                continue
            if fs.get(s, 0) < r:
                if _TRACE:
                    print(f"   >> LEAF-FAIL fixed-skill {s}: have={fs.get(s,0)} need={r}")
                return False
        if combo_skills:
            for s, r in combo_skills.items():
                if s in NO_DECO_SK:
                    if _sp.get(s, 0) + _wsp.get(s, 0) < r:
                        if _TRACE:
                            print(f"   >> LEAF-FAIL combo-series {s}: sp={_sp.get(s,0)} wsp={_wsp.get(s,0)} need={r}")
                        return False
                    continue
                if fs.get(s, 0) < r:
                    if _TRACE:
                        print(f"   >> LEAF-FAIL combo-skill {s}: have={fs.get(s,0)} need={r}")
                    return False
        if min_rem_armor > 0:
            if sum(1 for s in rem_a if s > 0) < min_rem_armor:
                if _TRACE:
                    print(f"   >> LEAF-FAIL min_rem_armor: {sum(1 for s in rem_a if s > 0)} < {min_rem_armor}")
                return False
        # 插槽技能按"空插槽"口径写入：= 配装完成后剩余的可插珠空槽数，
        # 而非装备初始总孔位。通用 LvN插槽 = 防具+武器空槽中等级>=N 的数量。
        for n in (1, 2, 3):
            _cnt_a = sum(1 for s in rem_a if s >= n)
            _cnt_w = sum(1 for s in rem_w if s >= n)
            fs[f'Lv{n}插槽'] = _cnt_a + _cnt_w
            fs[f'防具Lv{n}插槽'] = _cnt_a
            fs[f'武器Lv{n}插槽'] = _cnt_w
        dmg = calc_damage(fs)
        pieces = [e for e in equipped if e]
        clean_skills = {k: v for k, v in fs.items() if not k.endswith('插槽')}
        clean_pieces = []
        for e in pieces:
            pe = dict(e)
            pe['skills'] = {k: v for k, v in e.get('skills', {}).items() if not k.endswith('插槽')}
            clean_pieces.append(pe)
        # 同一套装备只记一次（early_fill 与叶子双计的去重）
        _gear_key = tuple(e['name'] for e in equipped)
        if _gear_key in _seen_sets:
            return True
        _seen_sets.add(_gear_key)
        results.append({'pieces': clean_pieces, 'skills': clean_skills, 'deco_used': used,
                        'pract': dmg, 'rem_a': rem_a, 'rem_w': rem_w})
        # 注册结果装备到支配表，供搜索中间支配剪枝使用
        if _DOM_MID:
            _register_result_dom(equipped)
        # 展开 α/β 等需求贡献相同的变体（补齐需求维度支配误删的合法解，如黑蚀龙护腿α）
        _expand_variants(equipped)
        return True

    def _make_equip_candidate(_a, _pix):
        """把原始防具数据转成与 equipped 兼容的候选 dict。"""
        return {
            'name': _a['name'], 'part_idx': _pix,
            'skills': dict(_a.get('skills') or {}),
            'slots': list(_a['slots']), 'slots_sorted': tuple(sorted(_a['slots'], reverse=True)),
            'weapon_slots': [], 'wslots_sorted': (),
            'rarity': _a['rarity'],
        }

    def _expand_variants(_eq):
        """对已接受解，把 5 防具部位替换为需求贡献相同的等价变体，逐个校验并记录。"""
        _import_itertools = __import__('itertools')
        _armor_pos = [i for i, e in enumerate(_eq)
                      if e and e['part_idx'] in (0, 1, 2, 3, 4)]
        if len(_armor_pos) < 2:
            return
        _lists = []
        for _pos in _armor_pos:
            _nm = _eq[_pos]['name']
            _va = _name_to_armor.get(_nm)
            if not _va:
                continue
            _pi = _eq[_pos]['part_idx']
            _vsig = tuple(sorted((s, lv) for s, lv in (_va.get('skills') or {}).items()
                                 if s in _demand_sigs))
            # 跳过空需求贡献组（无需求技能的防具不会被搜索选中，展开也无意义且会爆炸）
            if not _vsig:
                continue
            _grp = _variant_map.get(_pi, {}).get(_vsig)
            if _grp and len(_grp) > 1:
                _lists.append((_pos, _grp))
        if not _lists:
            return
        for _combo in _import_itertools.product(*[g for _, g in _lists]):
            _new_eq = list(_eq)
            for (_pos, _pix), _nm in zip([(p, _eq[p]['part_idx']) for p, _ in _lists], _combo):
                _a = _name_to_armor[_nm]
                _new_eq[_pos] = _make_equip_candidate(_a, _pix)
            # 用当前解已算好的技能集重新校验变体组合（skill 用原解技能做基底，仅换装备）
            _try_fill_and_record_variant(_new_eq)

    def _try_fill_and_record_variant(_eq):
        """校验并记录一个变体组合（从 equipped 重建，处理 slots/skills 差异）。"""
        if any(_e is None for _e in _eq):
            return
        _ck = dict(weapon_skills)
        _as, _ws = [], list(WSLOTS)
        for _e in _eq:
            if _e:
                for _s, _lv in _e['skills'].items():
                    _ck[_s] = _ck.get(_s, 0) + _lv
                _as.extend(_e.get('slots') or [])
                if _e.get('weapon_slots') and _e.get('part_idx') != 6:
                    _ws.extend(_e.get('weapon_slots') or [])
        _filled = fill_slots(_ck, _as, _ws, merged_fixed_once,
                             min_keep_armor=min_rem_armor, min_keep_weapon=min_rem_weapon)
        if _filled is None:
            return
        _fs2, _used, _rem_a, _rem_w = _filled
        # 系列件数（防具+武器）
        _sp2 = {}
        for _e in _eq:
            if _e is None or _e.get('part_idx') == 6:
                continue
            for _s in _e.get('skills', {}):
                if _s in NO_DECO_SK:
                    _sp2[_s] = _sp2.get(_s, 0) + 1
        for _s, _r in fixed_skills.items():
            if _is_slot_skill(_s):
                continue
            if _s in NO_DECO_SK:
                if _sp2.get(_s, 0) + _weapon_series_cnt.get(_s, 0) < _r:
                    return
                continue
            if _fs2.get(_s, 0) < _r:
                return
        if combo_skills:
            for _s, _r in combo_skills.items():
                if _s in NO_DECO_SK:
                    if _sp2.get(_s, 0) + _weapon_series_cnt.get(_s, 0) < _r:
                        return
                    continue
                if _fs2.get(_s, 0) < _r:
                    return
        if min_rem_armor > 0 and sum(1 for s in _rem_a if s > 0) < min_rem_armor:
            return
        for _n in (1, 2, 3):
            _cnt_a = sum(1 for s in _rem_a if s >= _n)
            _cnt_w = sum(1 for s in _rem_w if s >= _n)
            _fs2[f'Lv{_n}插槽'] = _cnt_a + _cnt_w
        _gk = tuple(_e['name'] for _e in _eq)
        if _gk in _seen_sets:
            return
        _seen_sets.add(_gk)
        _dm = calc_damage(_fs2)
        _pieces2 = [_e for _e in _eq if _e]
        _clean2 = []
        for _e in _pieces2:
            _pe = dict(_e)
            _pe['skills'] = {k: v for k, v in _e.get('skills', {}).items() if not k.endswith('插槽')}
            _clean2.append(_pe)
        results.append({'pieces': _clean2, 'skills': _fs2, 'deco_used': _used,
                        'pract': _dm, 'rem_a': _rem_a, 'rem_w': _rem_w})
        if _DOM_MID:
            _register_result_dom(_eq)

    # ===== 赤字加权总和的增量更新（保留用于兼容）=====

    # 预计算初始有赤字的技能索引列表
    init_deficit_indices = tuple(_i for _i in range(n_skills) if init_deficit[_i] > 0)
    # 预计算插槽技能索引与调试标志（避免每节点 globals/endswith 查询：cProfile 显示
    # _is_slot_skill 138 万次 0.6s、globals 47 万次）
    _slot_skill_idx = frozenset(_i for _i in range(n_skills) if tracked_skills[_i].endswith('插槽'))
    _DIAG = globals().get('_DIAG', False)
    _SOL_NAMES = globals().get('_SOL', {})
    _SOL_NEXT = globals().get('_SOL_NEXT')
    _DIAG_DEPTH_ARR = globals().setdefault('_DIAG_DEPTH', [0] * 10)
    # [CASE1-PROBE] 网页版 case1（当前孔位+珠子直接出结果）缺口统计钩子
    _CASE1_PROBE = globals().get('_CASE1_PROBE', False)
    _CASE1_STAT = globals().get('_CASE1_STAT', {})
    # [AGGRESSIVE-BREAK] 实验：模拟网页版普通防具 f=0 的 M < A 剪枝
    # （忽略候选分与剩余候选分，仅孔位价值 vs 需求）。网页版会漏解，仅用于对比实验。
    _AGGRESSIVE_BREAK = globals().get('_AGGRESSIVE_BREAK', False)

    # 单颗武器珠最多能提供的技能点数（含复合珠副技能），用于武器孔位宽松上界。
    # 复合珠如"属会·铁壁珠【3】"=会心击3+格挡1 可一珠补两个技能，
    # 单技能珠估算（best_deco_pts）会高估孔位需求、误杀正解，这里改用点数上界。
    _w_max_pts_per_slot = max(
        sum(_p for _, _p in _d['skills']) for _d in _get_deco_pool('weapon')
    )
    _w_base_cap = len(WSLOTS) * _w_max_pts_per_slot  # 武器基础槽可容纳的最大武器技能点数

    def _g6_core(a_cnt, w_cnt, rsm, deficit):
        """纯函数：给定槽位计数与赤字，判断剩余需求能否被珠子覆盖（降级链+点数上界）。
        供 _greedy_deco_check_with_future（实时状态）与 _charm_g6_temp（护石临时状态）共用。"""
        # 预留孔位扣减
        _rem = min_rem_armor
        for _lv in [1, 2, 3]:
            while _rem > 0 and a_cnt[_lv] > 0:
                a_cnt[_lv] -= 1
                _rem -= 1
        if _rem > 0:
            return False
        # 孔位技能需求：最小等级匹配（LvN可消耗>=N的槽位）
        # 就地复用 a_cnt/w_cnt：3 处调用点均为新建列表且拷贝后不再使用原变量
        # （_greedy_deco_check/_greedy_deco_check_with_future/_strict_leaf_check
        #  各新建 a_cnt/w_cnt；_g6_core 由这两个调用方传入新建列表）
        _a_tmp = a_cnt
        _w_tmp = w_cnt
        for _lv, _need in armor_min_items:
            _avail = sum(_a_tmp[_lv:])
            if _avail < _need:
                return False
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _a_tmp[_n])
                _a_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        for _lv, _need in weapon_min_items:
            _avail = sum(_w_tmp[_lv:])
            if _avail < _need:
                return False
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _w_tmp[_n])
                _w_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        # 插槽技能占用的槽位已在上面"最小等级匹配"检查中从 _a_tmp/_w_tmp 扣减，
        # 后续降级链检查基于扣减后的剩余槽位，不得再次扣减（此前重复扣减会把
        # 可用槽位扣瘦、误杀有解场景，导致 depth6 该检查被 armor_min 条件绕过）。
        a_cnt = _a_tmp
        w_cnt = _w_tmp
        # 计算珠子需求（防具保留单技能珠槽位估算；武器改用点数上界，
        # 因为复合珠可一珠补多技能——单技能珠估算会高估武器孔位需求、误杀正解，
        # 参照 _strict_leaf_check 的结论，精确求解交给 fill_slots）
        a_need = [0, 0, 0, 0]
        w_def_total = 0
        for _i in init_deficit_indices:
            _d = deficit[_i]
            if _d <= 0:
                continue
            if best_deco_pts[_i] == 0:
                if _i in _slot_skill_idx:
                    continue  # 插槽技能无珠子，由装备孔位满足
                return False
            # 剩余部位自带技能可免费削减赤字（无需珠子槽位）。
            # 旧逻辑用当前赤字直接估算孔位需求，会高估（如挑战者赤字5点、
            # 剩余防具自带3点，实际只需珠2点却算5个槽），导致SLC-FAIL误杀。
            _gap = _d - rsm[_i]
            if _gap <= 0:
                continue
            if is_weapon_deco[_i]:
                w_def_total += _gap
            else:
                _slots_needed = (_gap + best_deco_pts[_i] - 1) // best_deco_pts[_i]
                a_need[best_deco_slot[_i]] += _slots_needed
        # slot降级链检查（防具+武器槽合并：普通技能珠既可插防具孔也可插武器孔；
        # 此前仅查防具孔，depth6 时 rsm=0 全量赤字、武器孔 9 点容量被漏算，误杀有解）
        _t1 = a_cnt[1] + w_cnt[1]
        _t2 = a_cnt[2] + w_cnt[2]
        _t3 = a_cnt[3] + w_cnt[3]
        _a_r1 = _t1 - a_need[1]
        _a_r2 = _t2 - a_need[2] + (_a_r1 if _a_r1 < 0 else 0)
        _a_r3 = _t3 - a_need[3] + (_a_r2 if _a_r2 < 0 else 0)
        if _a_r3 < 0:
            return False
        # 武器点数上界：w_def_total 点赤字 <= 武器槽数 × 单颗珠最大点数（含复合珠）
        if w_def_total > sum(w_cnt[1:]) * _w_max_pts_per_slot:
            return False
        return True

    def _greedy_deco_check_with_future(depth):
        """增强版贪心检查：考虑剩余部位提供的slot（用增量slot计数优化）"""
        rsl = remaining_slot_by_lv[depth] if depth <= 6 else [0]*6
        # 用增量维护的slot计数替代遍历
        a_cnt = [_a_slot_cnt[0], _a_slot_cnt[1], _a_slot_cnt[2], _a_slot_cnt[3]]
        w_cnt = [_w_slot_cnt[0], _w_slot_cnt[1], _w_slot_cnt[2], _w_slot_cnt[3]]
        # 加上剩余部位的slot（上界估计）
        a_cnt[1] += rsl[0]; a_cnt[2] += rsl[1]; a_cnt[3] += rsl[2]
        w_cnt[1] += rsl[3]; w_cnt[2] += rsl[4]; w_cnt[3] += rsl[5]
        rsm = remaining_skill_max[depth] if depth < 7 else [0] * n_skills
        # 预支总量受"剩余部位技能总点数上界"约束：逐技能独立取最大在组合上不可达
        # （选"挑战者最多"的装备就选不了"耳塞最多"的装备），超出部分按比例缩减为
        # 珠子需求，否则 depth 浅层把所有赤字预支掉、降级链永不触发、无解场景判不死。
        # _pre_total 仅用于 depth0 的总量缩减（depth6 层被调用65万+次，避免浪费）。
        if depth == 0:
            _pre_total = 0
            for _i in init_deficit_indices:
                _d = _deficit[_i]
                if _d > 0 and best_deco_pts[_i] > 0 and _i not in _slot_skill_idx:
                    _pre_total += min(_d, rsm[_i])
            _cap_total = remaining_skill_total[0]
            if _pre_total > _cap_total > 0:
                # 仅在 depth0 缩减：剩余部位多时逐技能独立取最大虚高最严重；
                # 深层剩余部位少、逐技能最大接近真实，缩减会误杀（有解场景验证过）。
                _scale = _cap_total / _pre_total
                rsm = [rsm[_i] * _scale for _i in range(n_skills)]
        return _g6_core(a_cnt, w_cnt, rsm, _deficit)

    def _charm_g6_temp(Q):
        """护石放置前临时判死：模拟护石槽位/技能对 depth6 状态的影响，纯读不改状态。
        与放置后 G6(6) 检查等价（槽位计数相同、赤字减去护石技能贡献），因此安全；
        提前判死可避免对必死护石做 nz/place/undo 全套操作。"""
        _ta = [_a_slot_cnt[0], _a_slot_cnt[1], _a_slot_cnt[2], _a_slot_cnt[3]]
        _tw = [_w_slot_cnt[0], _w_slot_cnt[1], _w_slot_cnt[2], _w_slot_cnt[3]]
        for _s in Q['slots']:
            if 0 < _s <= 3:
                _ta[_s] += 1
        for _s in Q.get('weapon_slots', []):
            if 0 < _s <= 3:
                _tw[_s] += 1
        _td = list(_deficit)
        for _i, _lv in Q['nz']:
            if _td[_i] > 0:
                _td[_i] -= _lv
                if _td[_i] < 0:
                    _td[_i] = 0
        return _g6_core(_ta, _tw, remaining_skill_max[6], _td)

    def _strict_leaf_check():
        """叶子严格可行性检查（增量状态版）。
        与_greedy_deco_check_with_future相同的增量数据，但：
        1. depth=6无剩余部位上界；2. 额外验证总点数需求不超总槽数
           （_check_deco_feasible的a_max_pts逻辑，贪心检查缺失这一项）。
        严格度对齐_check_deco_feasible，避免偏松导致昂贵的fill_slots调用翻倍。"""
        a_cnt = [_a_slot_cnt[0], _a_slot_cnt[1], _a_slot_cnt[2], _a_slot_cnt[3]]
        w_cnt = [_w_slot_cnt[0], _w_slot_cnt[1], _w_slot_cnt[2], _w_slot_cnt[3]]
        _rem = min_rem_armor
        for _lv in [1, 2, 3]:
            while _rem > 0 and a_cnt[_lv] > 0:
                a_cnt[_lv] -= 1
                _rem -= 1
        if _rem > 0:
            if _TRACE:
                print(f"   >> SLC-FAIL min_rem_armor a_cnt={a_cnt}")
            return False
        _rem_w = min_rem_weapon
        for _lv in [1, 2, 3]:
            while _rem_w > 0 and w_cnt[_lv] > 0:
                w_cnt[_lv] -= 1
                _rem_w -= 1
        if _rem_w > 0:
            if _TRACE:
                print(f"   >> SLC-FAIL min_rem_weapon w_cnt={w_cnt}")
            return False
        # 孔位技能需求：最小等级匹配（LvN可消耗>=N的槽位）
        # 就地复用 a_cnt/w_cnt：3 处调用点均为新建列表且拷贝后不再使用原变量
        # （_greedy_deco_check/_greedy_deco_check_with_future/_strict_leaf_check
        #  各新建 a_cnt/w_cnt；_g6_core 由这两个调用方传入新建列表）
        _a_tmp = a_cnt
        _w_tmp = w_cnt
        for _lv, _need in armor_min_items:
            _avail = sum(_a_tmp[_lv:])
            if _avail < _need:
                if _TRACE:
                    print(f"   >> SLC-FAIL armor_min lv={_lv} need={_need} avail={_avail} a_tmp={_a_tmp}")
                return False
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _a_tmp[_n])
                _a_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        for _lv, _need in weapon_min_items:
            _avail = sum(_w_tmp[_lv:])
            if _avail < _need:
                return False
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _w_tmp[_n])
                _w_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        # 扣除孔位预留槽，避免与珠子需求的降级链检查重复计数同一槽位
        for _lv, _need in armor_min_items:
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _a_tmp[_n])
                _a_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        for _lv, _need in weapon_min_items:
            _rem = _need
            for _n in range(3, _lv - 1, -1):
                _take = min(_rem, _w_tmp[_n])
                _w_tmp[_n] -= _take
                _rem -= _take
                if _rem == 0:
                    break
        a_cnt = _a_tmp
        w_cnt = _w_tmp
        # 注：孔位可行性完全交给fill_slots精确求解，这里不做任何基于
        # best_deco_slot/best_deco_pts的单技能独立估算检查。那些估算不支持
        # 组合珠（一颗组合珠同时补多技能，如属会·铁壁珠），会高估孔位需求、
        # 误杀正解。参照网页配装器：搜索中间不做孔位预检，叶子精确求解。
        return True

    def _try_early_fill(depth):
        """提前填充：用剩余部位的候选填充未选部位，优先选含系列技能件的"""
        if depth >= 7:
            return _try_fill_and_record(incremental=True)
        temp_equipped = []
        for d in range(depth, 7):
            pi = part_order[d]
            cands = part_cands_vec.get(pi, [])
            if not cands:
                return False
            # 优先选含需求系列技能件的候选，否则选score最高
            best = None
            for c in cands:
                if c.get('_has_req_series', False):
                    best = c
                    break
            if best is None:
                best = max(cands, key=lambda x: x['score'])
            equipped[pi] = best
            temp_equipped.append(pi)
        success = _try_fill_and_record()
        for pi in temp_equipped:
            equipped[pi] = None
        return success

    # depth4 放置第5件防具后预检得到的"可行护石子集"，供 depth5 循环复用
    # （避免 depth5 对 26 个护石重复判死；无可行护石的分支在 depth4 直接剪掉）
    _charm_ok_qt = []

    def _dfs(depth):
        """按部位递归DFS（v3优化版 - 精准剪枝）

        核心优化：
        1. 赤字=0时直接提前填充，不遍历候选
        2. 赤字>0时只递归能减少赤字或提供系列技能件的候选
        3. 额外尝试1个最佳纯孔位候选（用于珠子填充路径）
        """
        nonlocal _def_score, _armor_filled, _a_slot_sum, _w_slot_sum

        if max_results > 0 and len(results) >= max_results:
            return
        if time.time() - start_time > timeout_s:
            if timeout_flag is not None:
                timeout_flag[0] = True
            return
        if _DIAG:
            _diag_cnt = _DIAG_DEPTH_ARR
            _diag_cnt[min(depth, 9)] += 1

        cur_def_score = _def_score
        # [TRACE-START]
        _trace_on = _TRACE
        _sol_names = _SOL_NAMES
        _cur_trace_parts = {}
        if _trace_on:
            for _d2 in range(depth):
                _pi2 = part_order[_d2]
                _eq2 = equipped[_pi2]
                if _eq2 is not None and part_order[_d2] < 5:
                    _cur_trace_parts[part_order[_d2]] = _eq2['name']
            # 仅当已填部位全部命中正解时启用（严格前缀匹配）
            _trace_prefix_ok = (_cur_trace_parts and
                                all(v == _sol_names.get(k) for k, v in _cur_trace_parts.items()))
            _trace_on = _trace_prefix_ok
            if _trace_on:
                _dbg = [_cur_trace_parts.get(pp) for pp in range(5)]
                print(f"[DFS] depth={depth} order={[part_order[x] for x in range(depth)]} eq={_dbg} def={cur_def_score} aSlot={_a_slot_sum} wSlot={_w_slot_sum} series_have={_series_have}")
        # [TRACE-END]

        # ===== 系列技能件数快速检查（增量数组+逐部位上界剪枝）=====
        if _n_req_series > 0 and remaining_series_max is not None:
            rsm_s = remaining_series_max[depth] if depth < 7 else [0]*_n_req_series
            for _si in range(_n_req_series):
                have_pieces = _series_wprov[_si] + _series_have[_si]
                need_pieces = _series_need_pieces[_si]
                if have_pieces < need_pieces:
                    need_cnt = need_pieces - have_pieces
                    if rsm_s[_si] < need_cnt:
                        if _trace_on and _sol_names.get(part_order[depth]) == _SOL_NEXT:
                            pass
                        return

        # ===== 赤字=0时检查系列技能是否满足，满足则提前填充 =====
        if cur_def_score == 0:
            series_ok = True
            if _n_req_series > 0:
                for _si in range(_n_req_series):
                    if _series_wprov[_si] + _series_have[_si] < _series_need_pieces[_si]:
                        series_ok = False
                        break
            if series_ok:
                if depth >= 7:
                    if _try_fill_and_record(incremental=True):
                        # 达标即返回（对齐网页版 e 生成器：赤字=0后直接填珠返回，
                        # 不再放更多防具），大幅缩减搜索树；变体由 _expand_variants 补齐
                        return
                else:
                    if _try_early_fill(depth):
                        return
                # 提前填充失败（如剩余无法满足系列件数）则继续探索其他组合

        # ===== 逐技能上界剪枝（仅检查有赤字的技能）=====
        if depth < 6:
            rsm = remaining_skill_max[depth]
            rsl = remaining_slot_by_lv[depth]
            for _i in init_deficit_indices:
                _d = _deficit[_i]
                if _d <= 0:
                    continue
                from_gear = rsm[_i]
                if from_gear >= _d:
                    continue
                remain_gap = _d - from_gear
                if best_deco_pts[_i] == 0:
                    if _is_slot_skill(tracked_skills[_i]):
                        continue  # 插槽技能无珠子，由装备孔位满足
                    if _trace_on:
                        print(f"   >> PRUNE(skill-noddeo) depth={depth} skill={tracked_skills[_i]} d={_d} from_gear={from_gear} cur={_cur_trace_parts}")
                    return
                slots_needed = (remain_gap + best_deco_pts[_i] - 1) // best_deco_pts[_i]
                _bs = best_deco_slot[_i]
                if is_weapon_deco[_i]:
                    # 用增量维护的slot计数替代遍历
                    # 简化：统计>=_bs的slot数
                    cur_w_cnt = sum(_w_slot_cnt[_bs:4])
                    rem_w = rsl[3] + rsl[4] + rsl[5]
                    if cur_w_cnt + rem_w < slots_needed:
                        return
                else:
                    cur_a_cnt = sum(_a_slot_cnt[_bs:4])
                    rem_a = rsl[0] + rsl[1] + rsl[2]
                    if cur_a_cnt + rem_a < slots_needed:
                        return

        # ===== 中间珠子补位检查（参照网页版 Rb，无条件执行）=====
        # 需求含插槽技能时 total_demand>0 会触发；但普通技能赤字（如攻击7+看破7+
        # 挑战者5+连击5...）total_demand=0，旧逻辑导致 Rb 从不执行，无解场景只能
        # 深挖到叶子 fill_slots 才失败（30s 超时）。现在每层无条件用"已选孔位 +
        # 剩余部位最大孔位"判断剩余需求能否被珠子补满，失败即整棵子树无解。
        # 注：数据中无复合珠，单技能珠估算不会高估孔位需求、不会误杀正解。
        cur_a_demand = _a_slot_demand[0]
        cur_w_demand = _w_slot_demand[0]
        cur_slot_total = _a_slot_sum + _w_slot_sum
        total_demand = cur_a_demand + cur_w_demand
        if depth < 6:
            if not _greedy_deco_check_with_future(depth):
                return
        elif depth == 6:
            # depth6（最后一件武器尚未选择）时：贪心检查判死（插槽需求双重扣减
            # 已修复，不再误杀有解），把填不满珠子的武器组合在武器层剪掉，避免
            # 大量无效叶子 fill_slots（此前因 armor_min 绕过，52.7s 里 30 万+
            # 叶子全走 fill_slots）。
            if not _greedy_deco_check_with_future(6):
                return
            max_future_slot = remaining_max_slot_sum[depth]
            # 武器候选只有1个（固定武器）时，early_fill 与叶子路径完全重复
            # （同一武器+同一填充，结果由 _seen_sets 去重），跳过可省一半 fill_slots
            # 开销；多候选时仍提前填充最快的武器以尽早收集方案。
            if len(part_cands_vec.get(part_order[depth], [])) <= 1:
                pass
            elif total_demand > 0 and total_demand <= cur_slot_total + max_future_slot:
                if _try_early_fill(depth):
                    pass  # 不提前return，继续遍历其他候选以收集更多方案
        elif total_demand == 0 and depth >= 7:
            _try_fill_and_record(incremental=True)
            return

        # ===== 全部7件装备时尝试填充 =====
        if depth >= 7:
            series_ok = True
            if _n_req_series > 0:
                for _si in range(_n_req_series):
                    if _series_wprov[_si] + _series_have[_si] < _series_need_pieces[_si]:
                        series_ok = False
                        break
            if series_ok:
                if _trace_on:
                    _leaf = {p: (equipped[p]['name'] if equipped[p] else None) for p in range(5)}
                    if all(_leaf.get(k) == v for k, v in _sol_names.items()):
                        print(f"   >> REACHED-LEAF depth=7 equipped={_leaf} a_slot_sum={_a_slot_sum} w_slot_sum={_w_slot_sum}")
                _try_fill_and_record(incremental=True)
            return

        # ===== 获取当前部位候选 =====
        part_idx = part_order[depth]
        part_cands = part_cands_vec.get(part_idx, [])
        if not part_cands:
            return

        is_charm_or_weapon = (part_idx in (5, 6))
        cur_w_def = _w_def_total[0]

        # ===== 遍历该部位候选装备 =====
        best_slot_only_tried = False  # 是否已尝试过纯孔位候选
        _n_results = len(results)
        _trace_is_sol = False
        # depth5 护石层复用 depth4 预检得到的可行护石子集（已全部判死通过）
        _loop_cands = _charm_ok_qt if (depth == 5 and _charm_ok_qt) else part_cands
        for Q in _loop_cands:
            if _trace_on and part_idx < 5 and _sol_names.get(part_idx) == Q['name']:
                _trace_is_sol = True
                _cur = {k: _cur_trace_parts.get(k) for k in range(5)}
                print(f"   > TRY-SOLUTION part={part_idx} Q={Q['name']} score={Q['score']} def={cur_def_score} aSlot={_a_slot_sum} wSlot={_w_slot_sum} cur={_cur} series_have={_series_have}")
            if max_results > 0 and _n_results >= max_results:
                return

            # per-candidate上界break（参照网页版评分阈值剪枝 D.P.f*Pa+M < A）
            # 网页版量纲统一为加权分：左边 Q['score']/remaining_best_sum 已含 SKILL_WEIGHT，
            # 孔位价值同样按加权折算（每孔至少可装1点技能珠，故 ×SKILL_WEIGHT），
            # 右边 cur_def_score（未加权技能点赤字）×SKILL_WEIGHT 对齐，避免量纲错配。
            q_score = Q['score']
            remaining_after = remaining_best_sum[depth + 1] if depth < 6 else 0
            # 网页版 Q.i*L 剪枝：剩余未放置部位数（5防具+护石，武器单独处理）
            _remaining_part_count = (6 - depth) if depth < 6 else 0
            # 阈值扣除可由武器槽插珠覆盖的武器技能赤字：装备 score 不含武器技能价值
            # （武器技能靠插珠补足，插珠价值不在 score 量纲内），若不扣除会把
            # "仅武器技能"场景下所有护石候选 break 掉导致 0 结果。
            _gear_def_pts = cur_def_score - _w_def_total[0]
            _wslots_total = _w_slot_sum + (remaining_wslot_max[depth] if depth < 7 else 0)
            _w_cover_pts = min(_w_def_total[0], _wslots_total * _w_max_pts_per_slot)
            # 孔位价值：已选孔位按每孔1点技能折算（保守上界，含武器孔——武器孔仅服务
            # 武器技能赤字，其价值由 _w_cover_pts 单独核算，此处按1点计不放大）。
            # 注意：不能把未来孔位计入 left（会虚增上界使无解场景剪枝失效）。
            if _AGGRESSIVE_BREAK and depth < 5:
                # 网页版普通防具 f=0 → M < A 剪枝模拟（忽略候选分/剩余候选分）
                if cur_slot_total * SKILL_WEIGHT < _gear_def_pts * SKILL_WEIGHT + _w_cover_pts:
                    if _trace_is_sol:
                        print(f"   >> KILL BREAK-AGG part={part_idx} slot_total={cur_slot_total} gear_def={_gear_def_pts} w_cover={_w_cover_pts}")
                    break
            elif q_score * _remaining_part_count + cur_slot_total * SKILL_WEIGHT < _gear_def_pts * SKILL_WEIGHT + _w_cover_pts:
                if _trace_is_sol:
                    print(f"   >> KILL BREAK part={part_idx} q_score={q_score} rem_parts={_remaining_part_count} slot_total={cur_slot_total} gear_def={_gear_def_pts} w_cover={_w_cover_pts}")
                break

            # 护石武器技能覆盖预检查（深度6 w-pts 检查的提前上界版）：
            # 当前武器技能赤字 cur_w_def 须能在"武器基础槽 + 护石武器槽"容量内被覆盖。
            # 减去护石自身武器技能点贡献上界（wsk_pts 是赤字减少的上界，故不误杀）。
            # 不满足 → 该护石在任意武器下都判死，直接跳过放置与递归（96% 的护石在此被剪）。
            if part_idx == 5 and cur_w_def > 0:
                if cur_w_def - Q['wsk_pts'] > _w_base_cap + len(Q.get('weapon_slots', [])) * _w_max_pts_per_slot:
                    continue

            # 护石放置前临时判死（_charm_g6_temp，等价于放置后 G6(6) 检查）：
            # 在 nz/place/undo 全套操作之前模拟护石槽位与技能对 depth6 状态的影响，
            # 必死的护石直接跳过（护石层占全树节点大头，省掉无效放置-递归-撤销开销）。
            # depth5 循环候选来自 depth4 预检缓存（_charm_ok_qt），已全部判死通过，跳过。
            if part_idx == 5 and Q.get('nz') and not _charm_ok_qt:
                if not _charm_g6_temp(Q):
                    if _trace_is_sol:
                        print(f"   >> KILL charm-g6-temp part=5 Q={Q['name']}")
                    continue

            q_has_req_series = Q.get('_has_req_series', False)

            # ===== 搜索中间支配检查（参照版 case5 jf(u,hb)）=====
            # 候选被某已记录结果中的同部位装备支配 → 该候选不可能出现在最优解中，剪枝。
            # q_has_req_series 保护：候选提供需求系列技能件而支配装备未提供时，
            # 替换会减少系列件数导致约束不满足，故这类候选不参与支配剪枝。
            _dom_lst = _dom_table[part_idx]
            if _DOM_MID and _dom_lst and not q_has_req_series:
                _dom_by_result = False
                for _E in _dom_lst:
                    if _dominated_check(Q, _E, dom_skills):
                        _dom_by_result = True
                        break
                if _dom_by_result:
                    if _trace_is_sol:
                        print(f"   >> KILL dominated-by-result part={part_idx} Q={Q['name']}")
                    continue

            # ===== 增量计算选Q后的赤字变化（用nz遍历）=====
            nz = Q['nz']
            q_max_slot = Q['max_slot']
            new_def_score = cur_def_score
            new_a_demand = _a_slot_demand[0]
            new_w_demand = _w_slot_demand[0]
            new_w_def = cur_w_def
            changed = []
            contributes = False
            for i, lv in nz:
                old_sk = _skills_vec[i]
                _skills_vec[i] = old_sk + lv
                old_def = _deficit[i]
                if old_def > 0:
                    old_demand = slot_demand_per_skill[i]
                    new_have = _skills_vec[i]
                    if new_have >= need_vec[i]:
                        _deficit[i] = 0
                        new_def_score -= old_def
                        if old_demand > 0:
                            if is_weapon_deco[i]:
                                new_w_demand -= old_demand
                            else:
                                new_a_demand -= old_demand
                        if is_weapon_deco[i]:
                            new_w_def -= old_def
                        contributes = True
                    else:
                        new_def = need_vec[i] - new_have
                        _deficit[i] = new_def
                        new_def_score += new_def - old_def
                        if best_deco_pts[i] > 0:
                            new_demand = (new_def + best_deco_pts[i] - 1) // best_deco_pts[i]
                            demand_delta = new_demand - old_demand
                            if demand_delta != 0:
                                if is_weapon_deco[i]:
                                    new_w_demand += demand_delta
                                else:
                                    new_a_demand += demand_delta
                        if is_weapon_deco[i]:
                            new_w_def += new_def - old_def
                        contributes = True
                changed.append((i, old_sk, old_def))

            # ===== 分支条件（精准剪枝）=====
            if cur_def_score > 0:
                if not contributes:
                    if not q_has_req_series:
                        # 纯孔位候选：只尝试1个，且需通过贪心检查
                        if best_slot_only_tried:
                            for i, old_sk, old_def in changed:
                                _skills_vec[i] = old_sk
                                _deficit[i] = old_def
                            if _trace_is_sol:
                                print(f"   >> KILL slot-only-already part={part_idx}")
                            continue
                        if q_max_slot == 0:
                            if _trace_is_sol:
                                print(f"   >> KILL slot-only-no-slot part={part_idx}")
                            for i, old_sk, old_def in changed:
                                _skills_vec[i] = old_sk
                                _deficit[i] = old_def
                            continue
                        best_slot_only_tried = True
                        if not _greedy_deco_check_with_future(depth):
                            # 贪心检查按"每技能独立最优珠"估算孔位，不支持组合珠
                            # （如属会·铁壁珠一颗同时贡献2技能），会高估孔位需求，
                            # 从而误杀正解。此处不KILL，继续递归，由叶子严格检查/
                            # fill_slots精确求解兜底。每层best_slot_only_tried已限1个。
                            if _trace_is_sol:
                                print(f"   >> NOTE slot-only-greedy-uncertain part={part_idx} (continue)")
                    else:
                        # 含系列技能件但不减少赤字：系列件数已满足时，
                        # 该候选只剩孔位价值 → 退化为纯孔位候选处理，
                        # 只尝试1个（分数最高），且需通过贪心检查。
                        series_still_needed = False
                        if _n_req_series > 0:
                            item_skills_q = Q['skills']
                            for s in item_skills_q:
                                if s in _series_idx_map:
                                    _si2 = _series_idx_map[s]
                                    if _series_wprov[_si2] + _series_have[_si2] < _series_need_pieces[_si2]:
                                        series_still_needed = True
                                        break
                        if not series_still_needed:
                            if best_slot_only_tried:
                                for i, old_sk, old_def in changed:
                                    _skills_vec[i] = old_sk
                                    _deficit[i] = old_def
                                if _trace_is_sol:
                                    print(f"   >> KILL slot-only-already part={part_idx}")
                                continue
                            if q_max_slot == 0:
                                if _trace_is_sol:
                                    print(f"   >> KILL slot-only-no-slot part={part_idx}")
                                for i, old_sk, old_def in changed:
                                    _skills_vec[i] = old_sk
                                    _deficit[i] = old_def
                                continue
                            best_slot_only_tried = True
                            if not _greedy_deco_check_with_future(depth):
                                # 同上面：贪心检查高估孔位需求（不支持组合珠），
                                # 不KILL，继续递归由叶子严格检查/fill_slots兜底。
                                if _trace_is_sol:
                                    print(f"   >> NOTE slot-only-greedy-uncertain part={part_idx} (continue)")

            if True:
                # ===== 原地放置装备 =====
                equipped[part_idx] = Q
                _def_score = new_def_score
                old_a_demand_val = _a_slot_demand[0]
                old_w_demand_val = _w_slot_demand[0]
                _a_slot_demand[0] = new_a_demand
                _w_slot_demand[0] = new_w_demand
                old_w_def_val = _w_def_total[0]
                _w_def_total[0] = new_w_def

                # 更新孔位
                item_slots = Q['slots']
                item_wslots = Q.get('weapon_slots', []) if Q.get('part_idx') != 6 else []
                a_len = len(_a_slots)
                w_len = len(_w_slots)
                _a_slots.extend(item_slots)
                _w_slots.extend(item_wslots)
                slot_sum_a = sum(item_slots)
                slot_sum_w = sum(item_wslots)
                _a_slot_sum += slot_sum_a
                _w_slot_sum += slot_sum_w
                # 增量更新slot计数
                for _s in item_slots:
                    if 0 < _s <= 3:
                        _a_slot_cnt[_s] += 1
                for _s in item_wslots:
                    if 0 < _s <= 3:
                        _w_slot_cnt[_s] += 1
                if _CASE1_PROBE:
                    # 网页版 case1：当前孔位+珠子直接补满剩余需求则出结果并剪枝。
                    # 必要条件（宽松上界）：当前孔位总数 >= 剩余技能点(装备+武器)+预留空槽。
                    # 满足者即"本可提前出结果"却继续展开的节点。
                    _need_slots = new_def_score + _w_def_total[0] + _a_slot_demand[0] + _w_slot_demand[0]
                    if new_def_score > 0 and _a_slot_sum + _w_slot_sum >= _need_slots:
                        _CASE1_STAT[depth] = _CASE1_STAT.get(depth, 0) + 1
                # 增量更新全技能dict（叶子记录用，撤销时回退）
                item_skills = Q['skills']
                _changed_all_sk = []
                for _sk2, _lv2 in item_skills.items():
                    _old_v = _cur_all_skills.get(_sk2, 0)
                    _cur_all_skills[_sk2] = _old_v + _lv2
                    _changed_all_sk.append((_sk2, _old_v))

                # 更新系列件数（增量数组+dict兼容）
                # 注意：武器（part_idx==6）不计入防具件数。武器提供的系列/组合技能
                # 由预计算的 _series_wprov/_weapon_series_cnt 单独计入，避免双计。
                changed_series = []
                changed_series_idx = []
                for s in item_skills:
                    if part_idx != 6 and s in NO_DECO_SK and s not in SLOT_SKILLS:
                        old_c = _series_count.get(s, 0)
                        _series_count[s] = old_c + 1
                        changed_series.append((s, old_c))
                        # 更新增量数组
                        if s in _series_idx_map:
                            _si2 = _series_idx_map[s]
                            changed_series_idx.append(_si2)
                            _series_have[_si2] += 1
                if part_idx < 5:
                    _armor_filled += 1

                # ===== 递归前系列可行性预检查 =====
                # 避免进入下一层_dfs才发现系列不足（减少函数调用开销）
                should_recurse = True
                if _n_req_series > 0 and remaining_series_max is not None:
                    next_depth = depth + 1
                    rsm_next = remaining_series_max[next_depth] if next_depth < 7 else [0]*_n_req_series
                    for _si in range(_n_req_series):
                        have_pieces = _series_wprov[_si] + _series_have[_si]
                        need_pieces = _series_need_pieces[_si]
                        if have_pieces < need_pieces:
                            need_cnt = need_pieces - have_pieces
                            if rsm_next[_si] < need_cnt:
                                should_recurse = False
                                break

                if _trace_is_sol:
                    print(f"   > ACCEPT-SOLUTION part={part_idx} Q={Q['name']} should_recurse={should_recurse} -> recurse")
                if should_recurse and depth == 4:
                    # 护石池可行性预检：5件防具已定，遍历护石池确认至少一个可行护石。
                    # 无解场景 ~96.5% 的 depth4 分支无可行护石，可省掉整层 _dfs(5)
                    # （10万+ 次调用及前缀检查/循环开销）。判死用与 depth5 完全相同的
                    # _charm_g6_temp（对无 nz 的纯槽位护石同样等价于放置后 depth6 判死），
                    # 并按 depth5 的武器技能覆盖检查预过滤；可行子集缓存到 _charm_ok_qt，
                    # depth5 循环直接复用（遍历时跳过重复判死）。
                    _charm_ok_qt.clear()
                    for _q in part_cands_vec.get(5, ()):
                        if _w_def_total[0] > 0 and _w_def_total[0] - _q['wsk_pts'] > _w_base_cap + len(_q.get('weapon_slots', [])) * _w_max_pts_per_slot:
                            continue
                        if _charm_g6_temp(_q):
                            _charm_ok_qt.append(_q)
                    if not _charm_ok_qt:
                        if _trace_is_sol:
                            print(f"   >> KILL charm-pool-empty part=4")
                        should_recurse = False
                if should_recurse and depth == 5:
                    # 护石层提前执行 depth6 判死检查：当前防具+护石组合在武器层必死
                    # （孔位降级链/武器技能赤字无法覆盖）时，无需进入武器层 _dfs(6)。
                    # 全树 depth6 节点 65.9 万（82%），多数在此被判死；提前检查可
                    # 消除 ~65 万次 _dfs(6) 函数入口及其函数体前缀开销。
                    if not _greedy_deco_check_with_future(6):
                        should_recurse = False
                if should_recurse:
                    _dfs(depth + 1)
                _n_results = len(results)

                # ===== 原地撤销 =====
                if part_idx < 5:
                    _armor_filled -= 1
                for _sk2, _old_v in _changed_all_sk:
                    if _old_v == 0:
                        _cur_all_skills.pop(_sk2, None)
                    else:
                        _cur_all_skills[_sk2] = _old_v
                for _si2 in changed_series_idx:
                    _series_have[_si2] -= 1
                for s, old_c in changed_series:
                    if old_c == 0:
                        _series_count.pop(s, None)
                    else:
                        _series_count[s] = old_c
                for _s in item_slots:
                    if 0 < _s <= 3:
                        _a_slot_cnt[_s] -= 1
                for _s in item_wslots:
                    if 0 < _s <= 3:
                        _w_slot_cnt[_s] -= 1
                _a_slot_sum -= slot_sum_a
                _w_slot_sum -= slot_sum_w
                del _a_slots[a_len:]
                del _w_slots[w_len:]
                _a_slot_demand[0] = old_a_demand_val
                _w_slot_demand[0] = old_w_demand_val
                _w_def_total[0] = old_w_def_val
                _def_score = cur_def_score
                equipped[part_idx] = None

            # 撤销技能向量更新
            for i, old_sk, old_def in changed:
                _skills_vec[i] = old_sk
                _deficit[i] = old_def

    _dfs(0)

    if not quiet:
        print(f"  DFS完成: {len(results)}方案, 耗时{time.time()-start_time:.3f}秒")
    if max_results == 0 or len(results) < max_results:
        results.sort(key=lambda x: -x['pract'])
    return results


def _qs_cache():
    d = getattr(_quick_skill_cache_tl, 'd', None)
    if d is None:
        d = {}
        _quick_skill_cache_tl.d = d
    return d

def _quick_skill_upper_bound(sk, cached_ctx, wslots, user_weapon_skills=None):
    uw_key = tuple(sorted((user_weapon_skills or {}).items()))
    cache_key = (sk, tuple(wslots), uw_key)
    if cache_key in _qs_cache():
        return _qs_cache()[cache_key]

    """快速计算技能sk的理论可追加上界（预筛用）

    基于候选装备列表的乐观估计：
    - 每个防具部位取含sk的最高等级，求和
    - 护石取含sk的最高等级
    - 所有孔位（含武器孔）按最优珠子换算sk等级
    - 武器洗练该技能时额外 +1（武器是该技能来源之一）
    返回值是理论上限，实际可能因固定技能约束而更低。
    """
    (candidates, all_skill_names, weapon_skills, armor_fixed, weapon_fixed,
     best_by_part, best_slot_by_part, candidates_by_part, part_series_availability) = cached_ctx

    # 1. 装备+护石部分：各部位最高sk等级之和
    gear_max = 0
    for pi in range(7):
        cands = candidates_by_part.get(pi, [])
        if not cands:
            continue
        part_max = max((c['skills'].get(sk, 0) for c in cands), default=0)
        gear_max += part_max

    # 2. 珠子部分：总孔位容量能插多少sk珠子
    # 找出sk的最优珠子（slot最小、pts最大）
    best_deco = None
    for dtype in ('armor', 'weapon'):
        pool = deco_idx.get((sk, dtype), [])
        for slot_req, pts, dname in pool:
            if best_deco is None or pts > best_deco[1] or (pts == best_deco[1] and slot_req < best_deco[0]):
                best_deco = (slot_req, pts, dname)
    deco_max = 0
    if best_deco:
        slot_req, pts, _ = best_deco
        # 乐观估计总孔位：所有候选的最大孔位之和 + 武器孔
        total_slots = sum(best_slot_by_part.get(pi, 0) for pi in range(7)) + sum(wslots)
        # 简化：假设所有孔位都>=slot_req（乐观）
        deco_max = (total_slots // slot_req) * pts

    # 3. 武器洗练提供的等级（武器是该技能的来源之一，非固定该技能）
    weapon_add = (user_weapon_skills or {}).get(sk, 0)
    result = gear_max + deco_max + weapon_add
    _qs_cache()[cache_key] = result
    return result


# ==================== 追加技能查询（v3优化版）====================
def query_extra_stream(fixed_skills, combo_skills, min_rem_armor, charm_pool, mode='normal', fav_skills=None, dis_skills=None, min_rem_weapon=0, user_weapon_skills=None):
    """逐技能扫描生成器：流式 yield 进度，避免长查询被代理超时切断。

    yield 顺序：
      {'type':'start', 'total', 'baseline_dmg', 'baseline_wcr', 'slot_info', 'slot_max'}
      {'type':'progress', 'done', 'total', 'skill', 'lv', 'cap', 'delta', 'tag', 'wcr', 'cur_lv'(?)}
      {'type':'done', 'result': {...完整结果...}}
    """
    fixed_skills = _normalize_skills_dict(fixed_skills)
    combo_skills = _normalize_skills_dict(combo_skills)
    if user_weapon_skills is not None:
        user_weapon_skills = _normalize_skills_dict(user_weapon_skills)

    series_names = ['巨戟龙的默示录', '火龙之力', '凶爪龙之力', '黑蚀龙之力',
                    '泡狐龙之力', '煌雷龙之力', '海龙之涡雷',
                    '冻峰龙的反叛', '锁刃龙的饥饿']

    fixed_set = set(fixed_skills.keys())
    if combo_skills:
        fixed_set.update(combo_skills.keys())

    fav_skills = fav_skills or set()
    dis_skills = dis_skills or set()

    # 根据模式过滤技能
    def _pass(sk):
        if mode == 'favorite':
            return sk in fav_skills
        if mode == 'disabled':
            return sk not in dis_skills
        return True

    # 真实可追加技能池：只保留确实能在装备/珠子/护石中出现的技能
    # 这一步避免追加模式把整套 SKILL_CAPS 全量扫描一遍，导致长时间无效查询。
    available_skill_pool = set()
    for p in ['head', 'body', 'arms', 'waist', 'legs']:
        for armor in parts[p]:
            available_skill_pool.update(armor.get('skills', {}).keys())
    for charm in charm_pool:
        available_skill_pool.update(charm.get('skills', {}).keys())
    for (sk, dtype), decos in deco_idx.items():
        if decos:
            available_skill_pool.add(sk)

    output_skills = []
    for sk in sorted(available_skill_pool):
        if _is_slot_skill(sk):
            continue
        if sk in fixed_set:
            continue
        if sk in SERIES_SK or sk in GROUP_SK:
            continue
        if sk not in SKILL_CAPS:
            continue
        if not _pass(sk):
            continue
        output_skills.append(sk)

    # 系列技能单独处理
    for ss in series_names:
        if ss not in fixed_set and _pass(ss):
            output_skills.append(ss)

    # 组合技能单独处理
    for cs in GROUP_SK:
        if cs not in fixed_set and cs in SKILL_CAPS and _pass(cs):
            output_skills.append(cs)

    under_max = []
    for sk, lv in fixed_skills.items():
        if _is_slot_skill(sk):
            continue
        cap = SKILL_CAPS.get(sk, 99)
        if lv < cap:
            under_max.append((sk, lv, cap))
    if combo_skills:
        for sk, lv in combo_skills.items():
            if _is_slot_skill(sk):
                continue
            cap = SKILL_CAPS.get(sk, 99)
            if lv < cap and sk not in [s for s, _, _ in under_max]:
                under_max.append((sk, lv, cap))

    seen = set()
    final_output = []
    for sk in output_skills:
        if sk not in seen and sk not in fixed_set:
            seen.add(sk)
            final_output.append(sk)

    series_max_pieces = {}
    for ss in series_names:
        parts_with = sum(1 for p in ['head','body','arms','waist','legs']
                       if any(ss in a.get('skills', {}) for a in parts[p]))
        # 武器洗练该系列技能时提供1件（武器是系列技能的来源之一，非固定该技能）
        weapon_pieces = 1 if (user_weapon_skills and ss in user_weapon_skills) else 0
        series_max_pieces[ss] = min(parts_with + weapon_pieces, 5)

    baseline_skills = dict(fixed_skills)
    if combo_skills:
        baseline_skills.update(combo_skills)
    # 武器提供的技能计入基线伤害（武器洗练提供1级）
    if user_weapon_skills:
        for _sk, _lv in user_weapon_skills.items():
            baseline_skills[_sk] = max(baseline_skills.get(_sk, 0), _lv)
    baseline_dmg = calc_damage(baseline_skills)
    baseline_wcr = calc_weighted_crit(baseline_skills)

    # 基线搜索
    t0_base = time.time()
    base_res = dfs_search(charm_pool, fixed_skills, combo_skills, min_rem_armor,
                          max_results=1, quiet=True, timeout_s=5.0, min_rem_weapon=min_rem_weapon,
                          user_weapon_skills=user_weapon_skills)
    base_dt = time.time() - t0_base
    slot_info_armor = {'Lv1': 0, 'Lv2': 0, 'Lv3': 0}
    slot_info_weapon = {'Lv1': 0, 'Lv2': 0, 'Lv3': 0}
    if base_res:
        best_base = base_res[0]
        for s in best_base.get('rem_a', []):
            if s >= 1: slot_info_armor['Lv1'] += 1
            if s >= 2: slot_info_armor['Lv2'] += 1
            if s >= 3: slot_info_armor['Lv3'] += 1
        for s in best_base.get('rem_w', []):
            if s >= 1: slot_info_weapon['Lv1'] += 1
            if s >= 2: slot_info_weapon['Lv2'] += 1
            if s >= 3: slot_info_weapon['Lv3'] += 1
    slot_info = {'Lv1': slot_info_armor['Lv1'] + slot_info_weapon['Lv1'],
                 'Lv2': slot_info_armor['Lv2'] + slot_info_weapon['Lv2'],
                 'Lv3': slot_info_armor['Lv3'] + slot_info_weapon['Lv3']}
    if base_res:
        print(f"  [基线] 完成({base_dt:.2f}s) 剩余: 防具Lv1x{slot_info_armor['Lv1']} Lv2x{slot_info_armor['Lv2']} Lv3x{slot_info_armor['Lv3']} 武器Lv1x{slot_info_weapon['Lv1']} Lv2x{slot_info_weapon['Lv2']} Lv3x{slot_info_weapon['Lv3']}")
    else:
        print(f"  [基线] 完成({base_dt:.2f}s) 无方案")

    # === 参考项目核心优化：从基线配装直接计算追加技能上界 ===
    # 对于基线配装已经能达到的技能等级，直接计算，跳过DFS
    # 只有基线配装无法确定的技能才需要DFS搜索
    _base_skill_from_gear = {}  # 基线配装中各技能的装备贡献
    _base_deco_slots = []  # 基线配装剩余防具孔位列表
    _base_deco_wslots = []  # 基线配装剩余武器孔位列表
    if base_res:
        _best_base = base_res[0]
        for _pc in _best_base.get('pieces', []):
            if _pc and isinstance(_pc, dict):
                for _sk, _lv in _pc.get('skills', {}).items():
                    _base_skill_from_gear[_sk] = _base_skill_from_gear.get(_sk, 0) + _lv
        _base_deco_slots = [s for s in _best_base.get('rem_a', []) if s > 0]
        _base_deco_wslots = [s for s in _best_base.get('rem_w', []) if s > 0]

    def _slot_based_upper(sk):
        """从基线配装计算技能sk的可达上界（装备贡献+珠子贡献+武器贡献）"""
        gear = _base_skill_from_gear.get(sk, 0)
        # 计算珠子贡献：剩余孔位能插多少该技能的珠子
        deco_contrib = 0
        for dtype in ('armor', 'weapon'):
            pool = deco_idx.get((sk, dtype), [])
            if not pool:
                continue
            # 找最优珠子（slot最小、pts最大）
            best_d = min(pool, key=lambda d: (d[0], -d[1]))
            slot_req, pts = best_d[0], best_d[1]
            slots = _base_deco_slots if dtype == 'armor' else _base_deco_wslots
            deco_contrib += sum(pts for s in slots if s >= slot_req)
        weapon = (user_weapon_skills or {}).get(sk, 0)
        return gear + deco_contrib + weapon

    # 构建候选缓存（包含所有可能的追加技能名，避免cached_ctx漏删候选）
    _extra_sn = set(final_output) | set(sk for sk, _, _ in under_max)
    # 关键优化：额外包含所有系列技能名，确保查询系列技能时不会误删候选
    _extra_sn.update(series_names)
    cached_ctx = _build_candidates(charm_pool, fixed_skills, combo_skills, quiet=False, extra_skill_names=_extra_sn,
                                   user_weapon_skills=user_weapon_skills)

    # 追加/升级技能搜索使用按目标技能构建的专属候选池（见下方_run_skill_job），
    # 保证评分/支配剪枝针对目标技能；cached_ctx仅用于基线/上界计算。

    # 提取cached_ctx中的part_series_availability（用于系列技能预检查）
    (_, _, _, _, _,
     _, _, _, part_series_availability) = cached_ctx

    # 动态计算DFS超时：候选越多，超时越长
    cand_count = len(cached_ctx[0])
    if cand_count <= 30:
        dfs_timeout = 0.1
    elif cand_count <= 100:
        dfs_timeout = 0.5
    else:
        dfs_timeout = 1.0
    # 系列技能追加搜索：候选池较大时0.05s超时易误判无解，
    # 需给足超时以保证能搜到可行方案（系列技能数量少，开销可控）。
    series_timeout = max(dfs_timeout, 1.5)

    # 孔位信息：基线剩余 + 理论上限(6)
    slot_max = {'Lv1': 6, 'Lv2': 6, 'Lv3': 6}
    print(f"  [孔位] 基线剩余: Lv1x{slot_info['Lv1']} Lv2x{slot_info['Lv2']} Lv3x{slot_info['Lv3']} (上限6)")

    skill_max = {}
    # 孔位二分化共6步（防具/武器各Lv1/2/3），计入总进度
    total = len(final_output) + len(under_max) + 6
    _done = 0

    # 流式：推送起始信息（含基线数据与孔位）
    no_solution = not base_res
    yield {
        'type': 'start', 'total': total,
        'baseline_dmg': round(baseline_dmg, 1),
        'baseline_wcr': round(baseline_wcr, 1),
        'slot_info': slot_info, 'slot_max': slot_max,
        'slot_info_armor': slot_info_armor, 'slot_info_weapon': slot_info_weapon,
        'no_solution': no_solution,
    }

    # 基线配装无解时：无任何追加/升级空间，直接结束，不做"原地追加"的误导。
    if no_solution:
        yield {
            'type': 'done',
            'result': {
                'result_text': '当前技能组与预留孔无法构成有效配装（无解），因此无追加技能空间。',
                'baseline_dmg': round(baseline_dmg, 1),
                'baseline_wcr': round(baseline_wcr, 1),
                'upgrade_skills': [],
                'extra_skills': [],
                'slot_info': slot_info,
                'slot_max': slot_max,
                'slot_info_armor': slot_info_armor, 'slot_info_weapon': slot_info_weapon,
                'slot_max_actual': {'armor': {'Lv1': 0, 'Lv2': 0, 'Lv3': 0},
                                    'weapon': {'Lv1': 0, 'Lv2': 0, 'Lv3': 0}},
                'no_solution': True,
            }
        }
        return

    # === 追加技能查询使用快速模式（跳过fill_slots优化循环）===
    global _FEASIBILITY_ONLY
    _FEASIBILITY_ONLY = True

    # 计算各侧各等级空孔可保留的实际最大数（供"孔位追加"使用）。
    # 与普通技能追加平权：使用 dfs_timeout + cached_ctx + _FEASIBILITY_ONLY 同一套可行性搜索；
    # 二分上界按各侧实际孔数收紧，并利用 Lv1>=Lv2>=Lv3 的单调性逐级收窄，减少无效搜索。
    def _side_slot_bound(side, n):
        if side == 'weapon':
            return sum(1 for s in WSLOTS if s >= n)
        total = 0
        for _p in ('head', 'body', 'arms', 'waist', 'legs'):
            if parts[_p]:
                total += max(sum(1 for s in a['slots'] if s >= n) for a in parts[_p])
        return total
    slot_max_actual = {'armor': {'Lv1': 0, 'Lv2': 0, 'Lv3': 0},
                       'weapon': {'Lv1': 0, 'Lv2': 0, 'Lv3': 0}}
    # 计算时剔除已存在的组合式孔位技能（如 BASE_FIXED_MIN 的 Lv1插槽），
    # 使上限表示"该侧该等级总共可保留的最大数"，与面板"当前→最大"的显示一致。
    _base_for_slot = {k: v for k, v in fixed_skills.items()
                      if not (k.startswith('Lv') and k.endswith('插槽'))}
    for _side, _prefix in (('armor', '防具Lv'), ('weapon', '武器Lv')):
        _prev_max = None
        for _sl in (1, 2, 3):
            # 用户规格：预留孔位上限为6，二分上界封顶6即可（同时减少搜索次数）
            _hi = min(_side_slot_bound(_side, _sl), 6)
            if _prev_max is not None:
                _hi = min(_hi, _prev_max)
            _lo, _best = 0, 0
            while _lo <= _hi:
                _mid = (_lo + _hi) // 2
                _tfix = dict(_base_for_slot)
                _tfix[f'{_prefix}{_sl}插槽'] = _mid
                _res = dfs_search(charm_pool, _tfix, combo_skills, min_rem_armor,
                                  max_results=1, quiet=True, timeout_s=dfs_timeout, cached_ctx=cached_ctx,
                                  min_rem_weapon=min_rem_weapon)
                if _res:
                    _best = _mid
                    _lo = _mid + 1
                else:
                    _hi = _mid - 1
            slot_max_actual[_side][f'Lv{_sl}'] = _best
            _prev_max = _best
            _done += 1
            print(f"  [孔位] {_prefix}{_sl} 可保留最大 {_best} 个 [{_done}/{total}]")
            yield {'type': 'progress', 'done': _done, 'total': total,
                   'skill': f'{_prefix}{_sl}插槽', 'lv': _best, 'cap': 6,
                   'delta': 0, 'tag': 'slot', 'wcr': 0}

    # === 未满级固定技能升级 + 追加技能：顺序扫描 ===
    # 纯 Python 搜索受 GIL 限制，线程并行无法加速反而会因 CPU 争抢导致墙钟超时误判，
    # 因此顺序执行。内部两处缓存改为线程本地（threading.local），为后续可能的
    # 多进程并行预留安全基础。
    # 准确性优先：统一使用充足超时，避免因超时导致假阴性
    _to_dfs = max(dfs_timeout, 1.0)  # 追加搜索至少1s，确保复杂技能组也能搜完
    _to_series = series_timeout
    _to_upgrade = max(0.5, 1.0)  # 升级搜索也至少1s
    def _run_skill_job(kind, sk, cur_lv=None, cap=None):
        """处理单个技能（升级/追加），返回 (skill_max元组, 进度dict, 日志字符串)。"""
        t0 = time.time()
        if kind == 'upgrade':
            best = cur_lv
            if sk in NO_DECO_SK:
                # 系列/组合技能升级：等级由防具件数决定，升序单调搜索，
                # 专属候选池protect_no_deco避免支配剪枝误删带该技能的防具。
                if sk in series_max_pieces:
                    cap = min(cap, series_max_pieces[sk])
                series_available = any(sk in part_series_availability.get(pi, set()) for pi in range(5))
                if series_available and cur_lv < cap:
                    _u_build_fixed = dict(fixed_skills)
                    _u_build_fixed[sk] = cap
                    _u_ctx = _build_candidates(charm_pool, _u_build_fixed, combo_skills, quiet=True,
                                               extra_skill_names={sk},
                                               user_weapon_skills=user_weapon_skills,
                                               protect_no_deco=True)
                    for lv in range(cur_lv + 1, cap + 1):
                        test_fixed = dict(fixed_skills)
                        test_fixed[sk] = lv
                        _tflag = [False]
                        res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                         max_results=1, quiet=True, timeout_s=_to_series,
                                         cached_ctx=_u_ctx,
                                         min_rem_weapon=min_rem_weapon,
                                         user_weapon_skills=user_weapon_skills,
                                         timeout_flag=_tflag)
                        if not res and _tflag[0]:
                            _tflag = [False]
                            res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                             max_results=1, quiet=True, timeout_s=_to_series * 3,
                                             cached_ctx=_u_ctx,
                                             min_rem_weapon=min_rem_weapon,
                                             user_weapon_skills=user_weapon_skills,
                                             timeout_flag=_tflag)
                        if res:
                            best = lv
                        else:
                            break
            else:
                # 升序单调搜索：专属候选池（目标技能参与评分/保护），首次失败即停
                _u_build_fixed = dict(fixed_skills)
                _u_build_fixed[sk] = cap
                _u_ctx = _build_candidates(charm_pool, _u_build_fixed, combo_skills, quiet=True,
                                           extra_skill_names={sk},
                                           user_weapon_skills=user_weapon_skills)
                for lv in range(cur_lv + 1, cap + 1):
                    test_fixed = dict(fixed_skills)
                    test_fixed[sk] = lv
                    _tflag = [False]
                    res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                     max_results=1, quiet=True, timeout_s=_to_upgrade,
                                     cached_ctx=_u_ctx,
                                     min_rem_weapon=min_rem_weapon,
                                     user_weapon_skills=user_weapon_skills,
                                     timeout_flag=_tflag)
                    if not res and _tflag[0]:
                        _tflag = [False]
                        res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                         max_results=1, quiet=True, timeout_s=_to_upgrade * 4,
                                         cached_ctx=_u_ctx,
                                         min_rem_weapon=min_rem_weapon,
                                         user_weapon_skills=user_weapon_skills,
                                         timeout_flag=_tflag)
                    if res:
                        best = lv
                    else:
                        break
            test_s = dict(baseline_skills)
            test_s[sk] = best
            best_dmg = calc_damage(test_s)
            best_wcr = calc_weighted_crit(test_s)
            dt = time.time() - t0
            status = "Lv%d" % best if best > cur_lv else "不可升级"
            log = f"{sk}(升级{cur_lv}→{cap}): {status} ({dt:.2f}s)"
            return (best, cap, best_dmg, best_dmg - baseline_dmg, 'upgrade', best_wcr), {
                'type': 'progress', 'skill': sk, 'lv': best, 'cap': cap, 'delta': round(best_dmg - baseline_dmg, 1),
                'tag': 'upgrade', 'wcr': round(best_wcr, 1), 'cur_lv': cur_lv
            }, log

        # 追加技能
        cap = SKILL_CAPS.get(sk, 99)
        if sk in series_names:
            actual_cap = cap
            if sk in series_max_pieces:
                actual_cap = min(actual_cap, series_max_pieces[sk])
            # 武器提供的件数（武器洗练该系列技能=1件）与当前基线等级
            weapon_pieces = (user_weapon_skills or {}).get(sk, 0)
            base_lv = baseline_skills.get(sk, 0)
            best = base_lv  # 默认保持当前等级（武器1件 + 防具当前件数）
            # 可用性：防具中存在 或 武器已提供
            series_available = weapon_pieces > 0 or any(sk in part_series_availability.get(pi, set()) for pi in range(5))
            if series_available and actual_cap > base_lv:
                # 升序单调搜索（与普通追加一致）：每技能最多1次UNSAT穷举。
                # 专属候选池：把目标系列技能传入fixed_skills参与评分/保护，
                # protect_no_deco避免带系列技能的防具被支配剪掉。
                _s_build_fixed = dict(fixed_skills)
                _s_build_fixed[sk] = actual_cap
                _s_ctx = _build_candidates(charm_pool, _s_build_fixed, combo_skills, quiet=True,
                                           extra_skill_names={sk},
                                           user_weapon_skills=user_weapon_skills,
                                           protect_no_deco=True)
                for lv in range(base_lv + 1, actual_cap + 1):
                    test_fixed = dict(fixed_skills)
                    test_fixed[sk] = lv
                    _tflag = [False]
                    res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                     max_results=1, quiet=True, timeout_s=_to_series,
                                     cached_ctx=_s_ctx,
                                     min_rem_weapon=min_rem_weapon,
                                     user_weapon_skills=user_weapon_skills,
                                     timeout_flag=_tflag)
                    if not res and _tflag[0]:
                        _tflag = [False]
                        res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                         max_results=1, quiet=True, timeout_s=_to_series * 3,
                                         cached_ctx=_s_ctx,
                                         min_rem_weapon=min_rem_weapon,
                                         user_weapon_skills=user_weapon_skills,
                                         timeout_flag=_tflag)
                    if res:
                        best = lv
                    else:
                        break  # 首次失败：件数需求单调，更高等级全部不可行
            test_s = dict(baseline_skills)
            test_s[sk] = best
            best_dmg = calc_damage(test_s)
            best_wcr = calc_weighted_crit(test_s)
            dt = time.time() - t0
            log = f"{sk}: Lv{best}/{actual_cap} ({dt:.2f}s)"
            return (best, actual_cap, best_dmg, best_dmg - baseline_dmg, 'extra', best_wcr), {
                'type': 'progress', 'skill': sk, 'lv': best, 'cap': actual_cap, 'delta': round(best_dmg - baseline_dmg, 1),
                'tag': 'extra', 'wcr': round(best_wcr, 1)
            }, log

        # === 快速上界预筛 ===
        upper = _quick_skill_upper_bound(sk, cached_ctx, WSLOTS, user_weapon_skills)
        start_lv = min(cap, upper)
        blv = baseline_skills.get(sk, 0)
        # 基线已满（或超过理论上界）：无追加空间，直接跳过，避免无谓的可行性搜索
        if blv >= start_lv:
            best = 0
            test_s = dict(baseline_skills)
            test_s[sk] = best
            best_dmg = calc_damage(test_s)
            best_wcr = calc_weighted_crit(test_s)
            dt = time.time() - t0
            log = f"{sk}: 基线已满，跳过 ({dt:.2f}s)"
            return (best, cap, best_dmg, best_dmg - baseline_dmg, 'extra', best_wcr), {
                'type': 'progress', 'skill': sk, 'lv': best, 'cap': cap, 'delta': round(best_dmg - baseline_dmg, 1),
                'tag': 'extra', 'wcr': round(best_wcr, 1)
            }, log

        # === 参考项目核心优化：从基线配装直接计算可达等级 ===
        # 如果基线配装能达到cap，直接返回，跳过DFS
        # 如果基线配装能达到>blv的等级，用它作为DFS起点（从该等级向下扫描）
        _slot_upper = _slot_based_upper(sk)
        if _slot_upper >= cap:
            # 基线配装已达到cap，直接返回
            best = cap
            test_s = dict(baseline_skills)
            test_s[sk] = best
            best_dmg = calc_damage(test_s)
            best_wcr = calc_weighted_crit(test_s)
            dt = time.time() - t0
            log = f"{sk}: Lv{best}/{cap} (基线直接达到, {dt:.2f}s)"
            return (best, cap, best_dmg, best_dmg - baseline_dmg, 'extra', best_wcr), {
                'type': 'progress', 'skill': sk, 'lv': best, 'cap': cap, 'delta': round(best_dmg - baseline_dmg, 1),
                'tag': 'extra', 'wcr': round(best_wcr, 1)
            }, log
        # 注意：_slot_based_upper 仅是基线配装这一种配置的可达等级（下界参考），
        # 不是全局上界——换其他防具可能达到更高等级，因此不能用来压低搜索起点。
        # 仅 _slot_upper >= cap 时可安全直返（基线配装已达满级，而cap是技能上限）。
        # 预检查：技能是否有珠子或装备能提供；若在候选池里完全没有来源，直接跳过。
        has_deco = bool(deco_idx.get((sk, 'armor'), []) or deco_idx.get((sk, 'weapon'), []))
        has_in_gear = any(sk in a.get('skills', {}) for p in ['head','body','arms','waist','legs'] for a in parts[p])
        has_in_charm = any(sk in c.get('skills', {}) for c in charm_pool)
        if not (has_deco or has_in_gear or has_in_charm):
            best = 0
            test_s = dict(baseline_skills)
            test_s[sk] = best
            best_dmg = calc_damage(test_s)
            best_wcr = calc_weighted_crit(test_s)
            dt = time.time() - t0
            log = f"{sk}: 直接跳过（无来源）({dt:.2f}s)"
            return (best, cap, best_dmg, best_dmg - baseline_dmg, 'extra', best_wcr), {
                'type': 'progress', 'skill': sk, 'lv': best, 'cap': cap, 'delta': round(best_dmg - baseline_dmg, 1),
                'tag': 'extra', 'wcr': round(best_wcr, 1)
            }, log
        # === 升序单调搜索（参考项目核心算法）===
        # 从 blv+1 向上逐级做 L=1 存在性搜索，首次失败即停：
        # 技能需求单调（更高等级需求是更低等级的超集），首次失败的等级之上全部不可行。
        # 关键收益：每个技能最多 1 次 UNSAT 穷举（降序扫描则是 cap-max 次，
        # 每次耗满超时），而 SAT 测试找到第一套方案即止，速度快得多。
        best = blv  # 默认保持基线等级
        if start_lv > blv:
            # 直接用针对目标技能重建的候选池（cached_ctx的支配剪枝不了解目标技能，
            # 会剪掉提供该技能的防具，先试cached_ctx纯属浪费超时）。
            # 保护集只含当前目标技能：其他追加技能与本次可行性无关，
            # 宽保护会阻止支配剪枝、候选膨胀、搜索树爆炸。
            # 单调性停止仅在"穷举证明无解"时生效；超时未搜完不算UNSAT，
            # 需加长超时重试，避免假阴性压低结果。
            _build_fixed = dict(fixed_skills)
            _build_fixed[sk] = start_lv
            _ctx = _build_candidates(charm_pool, _build_fixed, combo_skills, quiet=True,
                                     extra_skill_names={sk},
                                     user_weapon_skills=user_weapon_skills)
            for lv in range(blv + 1, start_lv + 1):
                test_fixed = dict(fixed_skills)
                test_fixed[sk] = lv
                _tflag = [False]
                res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                 max_results=1, quiet=True, timeout_s=_to_dfs,
                                 cached_ctx=_ctx,
                                 min_rem_weapon=min_rem_weapon,
                                 user_weapon_skills=user_weapon_skills,
                                 timeout_flag=_tflag)
                if not res and _tflag[0]:
                    # 超时未搜完≠无解：加长超时重试一次，避免单调性剪枝误判
                    _tflag = [False]
                    res = dfs_search(charm_pool, test_fixed, combo_skills, min_rem_armor,
                                     max_results=1, quiet=True, timeout_s=_to_dfs * 4,
                                     cached_ctx=_ctx,
                                     min_rem_weapon=min_rem_weapon,
                                     user_weapon_skills=user_weapon_skills,
                                     timeout_flag=_tflag)
                if res:
                    best = lv
                else:
                    break  # 首次失败（已穷举或重试后仍无解）：更高等级全部不可行
        test_s = dict(baseline_skills)
        test_s[sk] = best
        best_dmg = calc_damage(test_s)
        best_wcr = calc_weighted_crit(test_s)
        dt = time.time() - t0
        status = f"上限{upper}" if start_lv < cap else ""
        log = f"{sk}: Lv{best}/{cap} {status}({dt:.2f}s)"
        return (best, cap, best_dmg, best_dmg - baseline_dmg, 'extra', best_wcr), {
            'type': 'progress', 'skill': sk, 'lv': best, 'cap': cap, 'delta': round(best_dmg - baseline_dmg, 1),
            'tag': 'extra', 'wcr': round(best_wcr, 1)
        }, log

    jobs = [('upgrade', sk, cur_lv, cap) for sk, cur_lv, cap in under_max]
    jobs += [('extra', sk) for sk in final_output]

    # 顺序执行：纯 Python 搜索受 GIL 限制，线程并行无法加速反而会引入
    # 墙钟超时误判（CPU 争抢导致可行性搜索提前超时返回假无解）。
    for _job in jobs:
        _sm, _prog, _log = _run_skill_job(*_job)
        skill_max[_job[1]] = _sm
        _done += 1
        _prog['done'] = _done
        _prog['total'] = total
        print(f"  [{_done}/{total}] {_log}")
        yield _prog

    # 恢复完整模式
    _FEASIBILITY_ONLY = False

    lines = []
    lines.append(f"基线伤害（仅固定+组合技能）: {baseline_dmg:.1f}")
    lines.append(f"基线加权会心: {baseline_wcr:.1f}%")
    lines.append("")

    upgrade_skills = []
    up_items = []
    for sk, cur_lv, cap in under_max:
        if sk in skill_max:
            ml, cap2, dmg, delta, _, wcr = skill_max[sk]
            if ml > cur_lv:
                up_items.append((sk, cur_lv, ml, cap2, dmg, delta, wcr))
    up_items.sort(key=lambda x: -x[5])
    if up_items:
        lines.append("【固定技能升级空间】（当前等级→可升级到 | 独立伤害 | 增幅 | 加权会心）")
        lines.append("-" * 75)
        for sk, cur, ml, cap, dmg, delta, wcr in up_items:
            sign = "+" if delta >= 0 else ""
            lines.append(f"  {sk:<14s} | Lv{cur:>2d}→Lv{ml:>2d}/{cap:<2d} | 伤害 {dmg:>7.1f} | {sign}{delta:.1f} | 会心 {wcr:.1f}%")
            upgrade_skills.append({
                'skill': sk, 'current_lv': cur, 'max_lv': ml, 'cap': cap,
                'damage': round(dmg, 1), 'delta': round(delta, 1), 'wcr': round(wcr, 1)
            })
        lines.append("")

    lines.append("【追加技能】（技能名 | 最高等级 | 独立伤害 | 伤害增幅 | 加权会心）")
    lines.append("-" * 75)
    extra_skills = []
    out_items = []
    for sk in final_output:
        if sk in skill_max:
            ml, cap, dmg, delta, tag, wcr = skill_max[sk]
            if tag == 'extra':
                out_items.append((sk, ml, cap, dmg, delta, wcr))
    out_items.sort(key=lambda x: -x[4])
    for sk, ml, cap, dmg, delta, wcr in out_items:
        sign = "+" if delta >= 0 else ""
        lines.append(f"  {sk:<14s} | Lv{ml:>2d}/{cap:<2d} | 伤害 {dmg:>7.1f} | {sign}{delta:.1f} | 会心 {wcr:.1f}%")
        extra_skills.append({
            'skill': sk, 'max_lv': ml, 'cap': cap,
            'damage': round(dmg, 1), 'delta': round(delta, 1), 'wcr': round(wcr, 1)
        })
    lines.append("")

    lines.append("【孔位最大化】（将孔位作为技能搜索，防具/武器独立求解）")
    lines.append(f"  防具孔: Lv1 {slot_info_armor['Lv1']}个→最大{slot_max_actual['armor']['Lv1']}个 · Lv2 {slot_info_armor['Lv2']}个→最大{slot_max_actual['armor']['Lv2']}个 · Lv3 {slot_info_armor['Lv3']}个→最大{slot_max_actual['armor']['Lv3']}个")
    lines.append(f"  武器孔: Lv1 {slot_info_weapon['Lv1']}个→最大{slot_max_actual['weapon']['Lv1']}个 · Lv2 {slot_info_weapon['Lv2']}个→最大{slot_max_actual['weapon']['Lv2']}个 · Lv3 {slot_info_weapon['Lv3']}个→最大{slot_max_actual['weapon']['Lv3']}个")

    result_text = '\n'.join(lines)
    yield {
        'type': 'done',
        'result': {
            'result_text': result_text,
            'baseline_dmg': round(baseline_dmg, 1),
            'baseline_wcr': round(baseline_wcr, 1),
            'upgrade_skills': upgrade_skills,
            'extra_skills': extra_skills,
            'slot_info': slot_info,
            'slot_max': slot_max,
            'slot_info_armor': slot_info_armor, 'slot_info_weapon': slot_info_weapon,
            'slot_max_actual': slot_max_actual,
        }
    }

