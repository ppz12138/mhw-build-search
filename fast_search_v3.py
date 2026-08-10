#!/usr/bin/env python3
"""MHWilds 快速配装搜索 v3 — 位掩码+向量化的DFS搜索

核心优化（参照网页配装器策略）：
1. 技能→索引映射，用tuple替代dict做技能累加
2. 候选装备预计算技能向量，消除DFS内的dict.get开销
3. 精确赤字向量，逐技能检查可行性
4. 分数上限剪枝+技能可行性剪枝
"""
import json, time, itertools, sys, os, functools, threading

DATA = os.path.dirname(os.path.abspath(__file__))

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
    """孔位技能：组合式(LvN插槽) 或 分侧式(防具LvN插槽/武器LvN插槽)"""
    if not sk.endswith('插槽'):
        return False
    return sk.startswith('Lv') or sk.startswith('防具Lv') or sk.startswith('武器Lv')

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
        slots = limit_break(a.get('slots', [0,0,0]), r)
        parts[p].append({'name': a['name'], 'rarity': r, 'skills': sk, 'slots': slots})

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
    # 快速上界检查：top n_slots珠子的赤字贡献总和 < 赤字总量 → 无解
    _total_deficit = sum(w_fixed.values())
    _deco_contribs = sorted((sum(min(pts, w_fixed.get(sk, 0)) for sk, pts in d['skills'] if sk in w_fixed)
                             for d in cand_decos), reverse=True)
    if sum(_deco_contribs[:n_slots]) < _total_deficit:
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
    slot_skill_needs = {}
    side_slot_needs = []  # [(side, lv, count)]
    for sk, lv in fixed_skills.items():
        try:
            if sk.startswith('防具Lv') and sk.endswith('插槽'):
                side_slot_needs.append(('armor', int(sk[4:-2]), lv))
            elif sk.startswith('武器Lv') and sk.endswith('插槽'):
                side_slot_needs.append(('weapon', int(sk[4:-2]), lv))
            elif sk.startswith('Lv') and sk.endswith('插槽'):
                n = int(sk[2:-2])
                slot_skill_needs[n] = lv
        except ValueError:
            pass
    total_slot_keep = sum(slot_skill_needs.values())
    armor_keep_extra = sum(c for side, n, c in side_slot_needs if side == 'armor')
    weapon_keep_extra = sum(c for side, n, c in side_slot_needs if side == 'weapon')
    # 预留槽位隔离：孔位技能需求（LvN插槽/分侧预留）的槽位先从可插池移除，
    # 防止赤字贪心/美化插珠把低阶珠插进预留槽（如Lv1珠占用预留Lv1槽）导致最终校验失败
    reserved_a, reserved_w = [], []
    for _n, _cnt in slot_skill_needs.items():
        # 预留优先隔离武器侧高阶槽，与预检查的扣减方向一致
        for _ in range(_cnt):
            if _n in w:
                w.remove(_n); reserved_w.append(_n)
            elif _n in a:
                a.remove(_n); reserved_a.append(_n)
    for _side, _n, _cnt in side_slot_needs:
        _arr, _res = (a, reserved_a) if _side == 'armor' else (w, reserved_w)
        for _ in range(_cnt):
            if _n in _arr:
                _arr.remove(_n); _res.append(_n)
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
    all_rem = a + reserved_a + w_rem + reserved_w
    for n, need_cnt in slot_skill_needs.items():
        avail = sum(1 for s in all_rem if s == n)
        if avail < need_cnt:
            return None
        fs[f'Lv{n}插槽'] = need_cnt
    # 分侧孔位需求验证
    for side, n, need_cnt in side_slot_needs:
        arr = (a + reserved_a) if side == 'armor' else (w_rem + reserved_w)
        avail = sum(1 for s in arr if s == n)
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
    # 孔位技能需求：各等级精确匹配（预留LvN只消耗LvN槽位，不降级）
    for sk, lv in fixed_skills.items():
        if sk.startswith('防具Lv') and sk.endswith('插槽'):
            try:
                n = int(sk[4:-2])
            except ValueError:
                continue
            if a_cnt[n] < lv:
                return False
            continue
        if sk.startswith('武器Lv') and sk.endswith('插槽'):
            try:
                n = int(sk[4:-2])
            except ValueError:
                continue
            if w_cnt[n] < lv:
                return False
            continue
        if sk.startswith('Lv') and sk.endswith('插槽'):
            try:
                n = int(sk[2:-2])
            except ValueError:
                continue
            if a_cnt[n] + w_cnt[n] < lv:
                return False
    # 扣除孔位预留槽，避免与珠子需求的降级链检查重复计数同一槽位
    _slot_needs = {}
    _side_needs = []
    for sk, lv in fixed_skills.items():
        if sk.startswith('防具Lv') and sk.endswith('插槽'):
            try:
                _side_needs.append(('armor', int(sk[4:-2]), lv))
            except ValueError:
                pass
        elif sk.startswith('武器Lv') and sk.endswith('插槽'):
            try:
                _side_needs.append(('weapon', int(sk[4:-2]), lv))
            except ValueError:
                pass
        elif sk.startswith('Lv') and sk.endswith('插槽'):
            try:
                _slot_needs[int(sk[2:-2])] = lv
            except ValueError:
                pass
    for _n, _cnt in _slot_needs.items():
        # 预留优先消耗高阶槽（低阶槽更稀缺，只能放低阶珠，留给珠子需求）
        _take = min(_cnt, w_cnt[_n])
        w_cnt[_n] -= _take
        a_cnt[_n] -= (_cnt - _take)
    for _side, _n, _cnt in _side_needs:
        if _side == 'armor':
            a_cnt[_n] -= _cnt
        else:
            w_cnt[_n] -= _cnt
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
    w_total_need = 0
    a_total_need = 0
    a_need = {1:0, 2:0, 3:0}
    for sk, need in all_req.items():
        have = skills.get(sk, 0)
        if have >= need: continue
        d = need - have
        dtype = 'weapon' if sk in WEAPON_SK else 'armor'
        if dtype == 'weapon':
            pool = deco_idx.get((sk, 'weapon'), [])
            if not pool:
                return False
            w_total_need += d
        else:
            pool = deco_idx.get((sk, 'armor'), [])
            if not pool:
                return False
            a_total_need += d
            best = max(pool, key=lambda x: x[1])
            best_pts = best[1]
            best_slot = best[0]
            slots_needed = (d + best_pts - 1) // best_pts
            a_need[best_slot] += slots_needed
    w_total_slots = w_cnt[1] + w_cnt[2] + w_cnt[3]
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
    weapon_skills = {}
    if user_weapon_skills is not None:
        # 新行为：使用传入的武器技能（可以是空字典 {}）
        for sk, lv in user_weapon_skills.items():
            weapon_skills[sk] = lv
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
            a_sk = a['skills']
            sk_score = sum(min(v, merged_needs.get(s, 0))
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
        c_sk = c.get('skills', {})
        sk_score = sum(min(v, merged_needs.get(s, 0))
                      for s, v in c_sk.items() if s in all_skill_names)
        armor_slots = c.get('armor_slots', [])
        weapon_slots = c.get('weapon_slots', [])
        a_sum = sum(armor_slots) if armor_slots else 0
        w_sum = sum(weapon_slots) if weapon_slots else 0
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
        protection_skills = merged_needs
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
        for pi in range(6):
            grp = part_groups.get(pi, [])
            if not grp:
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
    for pi in range(6):
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
    for pi in range(5):
        pn = part_names[pi]
        avail = set()
        for a in parts[pn]:
            for sk_name in a.get('skills', {}):
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
    part_names = ['head', 'body', 'arms', 'waist', 'legs']

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
        ctx = _build_candidates(charm_pool, fixed_skills, combo_skills, quiet=quiet, user_weapon_skills=user_weapon_skills)
        (candidates, all_skill_names, weapon_skills, armor_fixed, weapon_fixed,
         best_by_part, best_slot_by_part, candidates_by_part, part_series_availability) = ctx

    # ===== 技能→索引映射 =====
    # 只追踪需要通过珠子/装备满足的技能（排除孔位技能和系列/组合技能）
    tracked_skills = []
    for s in fixed_skills:
        if _is_slot_skill(s):
            continue
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
    for pi in range(6):
        raw_cands = candidates_by_part.get(pi, [])
        vec_list = []
        for c in raw_cands:
            # 技能向量
            sv = [0] * n_skills
            has_wsk = False
            nz_indices = []  # 非零技能索引列表
            for s, lv in c['skills'].items():
                if s in skill_idx:
                    idx_val = skill_idx[s]
                    sv[idx_val] = lv
                    nz_indices.append((idx_val, lv))
                    if is_weapon_deco[idx_val]:
                        has_wsk = True
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
                '_has_req_series': has_req_series,
                'skills': c['skills'],
            })
        # ===== 系列技能预过滤（核心优化）=====
        # 对防具部位(pi<5)：含需求系列技能的候选全部保留，
        # 不含的只保留1个最佳纯slot候选（减少无效遍历）
        if pi < 5 and req_series_mask:
            series_cands = [v for v in vec_list if v['_has_req_series']]
            non_series = [v for v in vec_list if not v['_has_req_series']]
            # 强制系列检查：如果该部位不选系列候选，剩余部位能否满足所有系列需求？
            # 若不能，则该部位必须选系列候选，跳过非系列候选
            mandatory_series = False
            if all_series_req:
                for _ss, _lv in all_series_req.items():
                    _need = _lv
                    _wprov = 1 if (weapon_skills and weapon_skills.get(_ss, 0) > 0) else 0
                    other_max = _wprov
                    for _pj in range(5):
                        if _pj != pi and _ss in part_series_availability.get(_pj, set()):
                            other_max += 1
                    if other_max < _need:
                        mandatory_series = True
                        break
            if non_series and not mandatory_series:
                # 有需求技能贡献的非系列候选必须全保留（其技能在槽位紧张时无法用珠替代），
                # 仅对纯槽位件（无技能贡献）保留槽位最优的1个
                contrib = [v for v in non_series if v['nz']]
                pure = [v for v in non_series if not v['nz']]
                series_cands.extend(contrib)
                if pure:
                    best_slot_only = max(pure, key=lambda x: (x['slot_sum'] + x.get('w_slot_sum', 0), x['score']))
                    series_cands.append(best_slot_only)
            vec_list = series_cands
        # ===== 无贡献防具桶合并（参考项目核心优化）=====
        # 不提供任何被追踪技能、不含需求系列技能的候选，按槽型签名分桶，
        # 每桶只留1个代表（分数最高）。可行性只取决于槽位与技能贡献，
        # 同桶成员完全等价，可大幅缩减候选数与搜索树。
        if pi < 5 and len(vec_list) > 8:
            _keep = []
            _buckets = {}
            for v in vec_list:
                if v['nz'] or v['_has_req_series']:
                    _keep.append(v)
                else:
                    _key = (v['slots_sorted'], v['wslots_sorted'])
                    _b = _buckets.get(_key)
                    if _b is None or v['score'] > _b['score']:
                        _buckets[_key] = v
            vec_list = _keep + list(_buckets.values())
        part_cands_vec[pi] = vec_list

    # ===== 全局预检查 =====
    for sk in fixed_skills:
        if _is_slot_skill(sk):
            continue
        if sk in NO_DECO_SK:
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
            # 只在珠子和护石都没有该技能时才判定无解
            has_armor_source = any(a.get('skills', {}).get(s, 0) > 0 for a in armors)
            if not pool and best_charm_lv < d and not has_armor_source:
                if not quiet:
                    print(f"  预检查: {s}无珠子且护石最高Lv{best_charm_lv}<需{d}且防具也无→无解")
                return []

    # ===== 搜索状态 =====
    results = []
    equipped = [None] * 6
    # 增量维护的全技能dict（含非追踪技能，供fill_slots用），放置/撤销时同步更新，
    # 避免叶子处从6件装备重建
    _cur_all_skills = dict(weapon_skills)

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
    _sf_keys = set()  # 系列优先路径已收录方案的装备指纹（用于回退DFS后去重）
    _sf_results = []  # 系列优先路径已找到的方案（回退DFS后合并保留）

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

    # ===== 系列优先搜索（移至预计算之前，跳过不必要的remaining_*计算）=====
    if _n_req_series > 0:
        # 预计算每个(部位, 系列)是否有对应候选 — 用2D数组替代dict
        _sf_avail_arr = [[False] * _n_req_series for _ in range(5)]
        for _pi5 in range(5):
            for _si5, _ss5 in enumerate(_req_series_list):
                _sf_avail_arr[_pi5][_si5] = any(_ss5 in _c5['skills']
                    for _c5 in part_cands_vec.get(_pi5, []))
        # 预计算 remaining_avail[d][si] = 从部位d到4中含系列si的部位数
        _sf_rem_avail = [[0] * _n_req_series for _ in range(6)]
        for _d in range(4, -1, -1):
            for _si in range(_n_req_series):
                _sf_rem_avail[_d][_si] = _sf_rem_avail[_d+1][_si] + (1 if _sf_avail_arr[_d][_si] else 0)
        # 预计算每个候选的系列技能索引列表（避免每次dict查找）
        for _pi5 in range(5):
            for _c5 in part_cands_vec.get(_pi5, []):
                _c5['_si_list'] = [_series_idx_map[s] for s in _c5['skills'] if s in _series_idx_map]

        # 预计算后缀上界（供_sf_bt剪枝）：
        #   _sf_suf_sk[d][i] = 部位d..4候选对追踪技能i的最大可贡献和（跨部位独立取max，偏松安全）
        #   _sf_suf_slots[d] = 部位d..4候选最大槽位数和
        _sf_suf_sk = [[0] * n_skills for _ in range(6)]
        _sf_suf_slots = [0] * 6
        for _d in range(4, -1, -1):
            _sf_suf_sk[_d] = list(_sf_suf_sk[_d + 1])
            _part_max_sv = [0] * n_skills
            _part_max_slots = 0
            for _c5 in part_cands_vec.get(_d, []):
                _sv5 = _c5['sv']
                for _i5 in range(n_skills):
                    if _sv5[_i5] > _part_max_sv[_i5]:
                        _part_max_sv[_i5] = _sv5[_i5]
                _sl5 = sum(1 for _x5 in _c5['slots'] if 0 < _x5 <= 3)
                if _sl5 > _part_max_slots:
                    _part_max_slots = _sl5
            for _i5 in range(n_skills):
                _sf_suf_sk[_d][_i5] += _part_max_sv[_i5]
            _sf_suf_slots[_d] = _sf_suf_slots[_d + 1] + _part_max_slots
        # 各追踪技能的需求与可珠补标记
        _sf_need = [0] * n_skills
        for _i5 in range(n_skills):
            _sk5 = tracked_skills[_i5]
            _r5 = fixed_skills.get(_sk5, 0)
            if combo_skills:
                _r5 = max(_r5, combo_skills.get(_sk5, 0))
            if _sk5 in NO_DECO_SK or _is_slot_skill(_sk5):
                _r5 = 0  # 系列/槽位技能由专门机制处理
            _sf_need[_i5] = _r5
        _sf_deco_ok = [bool(deco_idx.get((tracked_skills[_i5], 'weapon' if is_weapon_deco[_i5] else 'armor'), []))
                       for _i5 in range(n_skills)]
        _sf_ch_sk_max = {}
        for _ch in part_cands_vec.get(5, []):
            for _s5, _lv5 in _ch.get('skills', {}).items():
                if _s5 in skill_idx and not _is_slot_skill(_s5):
                    if _lv5 > _sf_ch_sk_max.get(skill_idx[_s5], 0):
                        _sf_ch_sk_max[skill_idx[_s5]] = _lv5
        _sf_ch_slots_max = max((sum(1 for _x5 in _ch.get('slots', []) if 0 < _x5 <= 3)
                                for _ch in part_cands_vec.get(5, [])), default=0)
        _sf_w_deco_need_min = 0  # 武器珠最小占用（仅计数，偏松）
        for _i5 in range(n_skills):
            if is_weapon_deco[_i5] and _sf_need[_i5] > 0:
                _pool5 = deco_idx.get((tracked_skills[_i5], 'weapon'), [])
                _best5 = max((pts for sr, pts, nm in _pool5), default=1) if _pool5 else 1
                _sf_w_deco_need_min += (_sf_need[_i5] + _best5 - 1) // _best5
        _sf_keep = sum(_lv for _sk, _lv in fixed_skills.items() if _is_slot_skill(_sk))

        # ===== 前缀上界剪枝的预计算 =====
        # 上界状态（已承诺+候选+后缀max+最强护石max）对_check_deco_feasible单调：
        # 若上界状态都不可行，则该前缀的任何真实补全都不可行（含预留槽/槽位等级）
        _sf_fixed_merged = dict(fixed_skills)
        if combo_skills:
            for _s, _r in combo_skills.items():
                if _s in NO_DECO_SK:
                    continue
                _sf_fixed_merged[_s] = max(_sf_fixed_merged.get(_s, 0), _r)
        # 后缀各等级防具槽数量上界（每部位取候选最大值后求和，偏松安全）
        _sf_suf_acnt = [[0, 0, 0, 0] for _ in range(6)]
        for _d in range(4, -1, -1):
            _sf_suf_acnt[_d] = list(_sf_suf_acnt[_d + 1])
            _pm = [0, 0, 0, 0]
            for _c5 in part_cands_vec.get(_d, []):
                _cnt5 = [0, 0, 0, 0]
                for _x5 in _c5['slots']:
                    if 0 < _x5 <= 3:
                        _cnt5[_x5] += 1
                for _lv in range(1, 4):
                    if _cnt5[_lv] > _pm[_lv]:
                        _pm[_lv] = _cnt5[_lv]
            for _lv in range(1, 4):
                _sf_suf_acnt[_d][_lv] += _pm[_lv]
        # 护石槽位上界（跨护石按等级取max，偏松安全）
        _sf_ch_acnt_max = [0, 0, 0, 0]
        for _ch in part_cands_vec.get(5, []):
            _cnt5 = [0, 0, 0, 0]
            for _x5 in _ch.get('slots', []):
                if 0 < _x5 <= 3:
                    _cnt5[_x5] += 1
            for _lv in range(1, 4):
                if _cnt5[_lv] > _sf_ch_acnt_max[_lv]:
                    _sf_ch_acnt_max[_lv] = _cnt5[_lv]
        _sf_ub_skills_base = dict(weapon_skills)
        for _i5 in range(n_skills):
            _sk5 = tracked_skills[_i5]
            if _is_slot_skill(_sk5) or _sk5 in NO_DECO_SK:
                continue
            _ub5 = weapon_skills.get(_sk5, 0) + _sf_ch_sk_max.get(_i5, 0)
            _sf_ub_skills_base[_sk5] = max(_sf_ub_skills_base.get(_sk5, 0), _ub5)

        _sf_combos = []
        _sf_eq = [None] * 5
        _sf_sc = [0] * _n_req_series
        _sf_sv_acc = [0] * n_skills
        _sf_slot_acc = [0]
        _sf_a_cnt_acc = [0, 0, 0, 0]  # 已承诺防具槽按等级计数

        def _sf_bt(d):
            if d == 5:
                for _si in range(_n_req_series):
                    if _series_wprov[_si] + _sf_sc[_si] < _series_need_pieces[_si]:
                        return
                _sf_combos.append(list(_sf_eq))
                return
            # 剪枝：剩余部位能否满足所有系列需求（用预计算数组O(1)）
            for _si in range(_n_req_series):
                _need = _series_need_pieces[_si] - _series_wprov[_si] - _sf_sc[_si]
                if _need > 0 and _sf_rem_avail[d][_si] < _need:
                    return
            for c in part_cands_vec.get(d, []):
                if not c.get('_has_req_series', False):
                    # 非系列候选逐个尝试（vec_list已把纯槽位件折叠为1个代表，
                    # 其余均为有技能贡献者，不能再只试一个代表，否则误杀可行解）
                    _ok = True
                    for _si in range(_n_req_series):
                        _need = _series_need_pieces[_si] - _series_wprov[_si] - _sf_sc[_si]
                        if _need > 0 and _sf_rem_avail[d+1][_si] < _need:
                            _ok = False
                            break
                    if not _ok:
                        continue
                # 技能可达上界剪枝：无珠可补的技能必须由装备+护石凑齐
                _sv_c = c['sv']
                _bad = False
                for _i5 in range(n_skills):
                    _r5 = _sf_need[_i5]
                    if _r5 <= 0:
                        continue
                    _have = weapon_skills.get(tracked_skills[_i5], 0) + _sf_sv_acc[_i5] + _sv_c[_i5]
                    if _have >= _r5:
                        continue
                    if not _sf_deco_ok[_i5] and _have + _sf_suf_sk[d+1][_i5] + _sf_ch_sk_max.get(_i5, 0) < _r5:
                        _bad = True
                        break
                if _bad:
                    continue
                # 槽位预算剪枝：珠需求总量(不含武器珠)+预留 ≤ 槽位供给上界
                _dem = 0
                for _i5 in range(n_skills):
                    _r5 = _sf_need[_i5]
                    if _r5 <= 0 or not _sf_deco_ok[_i5]:
                        continue
                    _have = weapon_skills.get(tracked_skills[_i5], 0) + _sf_sv_acc[_i5] + _sv_c[_i5]
                    _gap = _r5 - _have - _sf_suf_sk[d+1][_i5] - _sf_ch_sk_max.get(_i5, 0)
                    if _gap > 0:
                        _dem += _gap
                _supply = _sf_slot_acc[0] + sum(1 for _x5 in c['slots'] if 0 < _x5 <= 3) \
                    + _sf_suf_slots[d+1] + _sf_ch_slots_max + sum(WSLOTS) - _sf_w_deco_need_min
                if _dem + _sf_keep > _supply:
                    continue
                # 前缀上界可行性剪枝：用严格校验器（含预留槽/槽位等级/降级链）
                # 检验"已承诺+当前候选+后缀上界+最强护石"的虚拟最优状态。
                # 该状态对真实补全单调占优，不过则整棵子树无解。
                # 仅在中后段深度执行（浅层上界过松，调用开销大于收益）
                _sf_c_acnt = [0, 0, 0, 0]
                for _x5 in c['slots']:
                    if 0 < _x5 <= 3:
                        _sf_c_acnt[_x5] += 1
                if d >= 2:
                    _ub_sk = dict(_sf_ub_skills_base)
                    for _i5 in range(n_skills):
                        _sk5 = tracked_skills[_i5]
                        if _is_slot_skill(_sk5) or _sk5 in NO_DECO_SK:
                            continue
                        _v5 = weapon_skills.get(_sk5, 0) + _sf_sv_acc[_i5] + _sv_c[_i5] \
                            + _sf_suf_sk[d + 1][_i5] + _sf_ch_sk_max.get(_i5, 0)
                        if _v5 > _ub_sk.get(_sk5, 0):
                            _ub_sk[_sk5] = _v5
                    _ub_a = []
                    for _lv in range(1, 4):
                        _n5 = _sf_a_cnt_acc[_lv] + _sf_c_acnt[_lv] + _sf_suf_acnt[d + 1][_lv] + _sf_ch_acnt_max[_lv]
                        _ub_a.extend([_lv] * _n5)
                    if not _check_deco_feasible(_ub_sk, _ub_a, list(WSLOTS), _sf_fixed_merged, {},
                                                weapon_skills, min_rem_armor, min_rem_weapon):
                        continue
                # 更新系列计数（用预计算的_si_list）
                _ch = c['_si_list']
                for _si in _ch:
                    _sf_sc[_si] += 1
                for _i5 in range(n_skills):
                    _sf_sv_acc[_i5] += _sv_c[_i5]
                _sf_slot_acc[0] += sum(1 for _x5 in c['slots'] if 0 < _x5 <= 3)
                for _lv in range(1, 4):
                    _sf_a_cnt_acc[_lv] += _sf_c_acnt[_lv]
                _sf_eq[d] = c
                _sf_bt(d + 1)
                _sf_eq[d] = None
                for _si in _ch:
                    _sf_sc[_si] -= 1
                for _i5 in range(n_skills):
                    _sf_sv_acc[_i5] -= _sv_c[_i5]
                _sf_slot_acc[0] -= sum(1 for _x5 in c['slots'] if 0 < _x5 <= 3)
                for _lv in range(1, 4):
                    _sf_a_cnt_acc[_lv] -= _sf_c_acnt[_lv]

        _sf_bt(0)

        if _sf_combos:
            # 聚合签名去重：可行性与排序只取决于组合的(追踪技能合计, 槽位合计,
            # 系列件数)，同签名组合完全等价，仅保留分数最高代表（比按分数截断安全）
            _sf_dedup = {}
            for _combo in _sf_combos:
                _sv_sum = [0] * n_skills
                _a_cnt = [0, 0, 0, 0]
                for _c9 in _combo:
                    _sv9 = _c9['sv']
                    for _i9 in range(n_skills):
                        _sv_sum[_i9] += _sv9[_i9]
                    for _s9 in _c9['slots']:
                        if 0 < _s9 <= 3:
                            _a_cnt[_s9] += 1
                _key = (tuple(_sv_sum), tuple(_a_cnt),
                        tuple(sum(1 for _c9 in _combo if _ss9 in _c9['skills'])
                              for _ss9 in _req_series_list))
                _sc9 = sum(_c9['score'] for _c9 in _combo)
                _prev = _sf_dedup.get(_key)
                if _prev is None or _sc9 > _prev[0]:
                    _sf_dedup[_key] = (_sc9, _combo)
            if len(_sf_dedup) < len(_sf_combos) and not quiet:
                print(f"  系列优先搜索: {len(_sf_combos)}组合 → 签名去重{len(_sf_dedup)}")
            _sf_combos = [_v[1] for _v in _sf_dedup.values()]

            _sf_charms = part_cands_vec.get(5, [])

            _merged_fixed = dict(fixed_skills)
            if combo_skills:
                for _s, _r in combo_skills.items():
                    if _s in NO_DECO_SK:
                        continue
                    _merged_fixed[_s] = max(_merged_fixed.get(_s, 0), _r)

            _sf_data = []
            for _sf_combo in _sf_combos:
                _cur = dict(weapon_skills)
                _a_s = []
                _w_s = list(WSLOTS)
                for _pi6 in range(5):
                    for _s, _lv in _sf_combo[_pi6]['skills'].items():
                        _cur[_s] = _cur.get(_s, 0) + _lv
                    _a_s.extend(_sf_combo[_pi6]['slots'])
                    if _sf_combo[_pi6].get('weapon_slots'):
                        _w_s.extend(_sf_combo[_pi6]['weapon_slots'])
                _sf_data.append((_sf_combo, _cur, _a_s, _w_s))

            _charm_extra = []
            for _ch in _sf_charms:
                _ch_slots = _ch.get('slots', [])
                _ch_wslots = _ch.get('weapon_slots', [])
                _charm_extra.append((_ch, _ch_slots, _ch_wslots))

            # 预计算护石能力上界（用于安全预检）：
            # - 技能维度：各非系列需求技能，护石能给的最大点数（跨护石取 max => 高估，安全）
            # - 孔位维度：单件护石能提供的最大 armor/weapon 孔
            # 该假想"最强护石"能力 >= 任何真实护石。若某防具组合配上它仍无法通过
            # _check_deco_feasible（严格含预留孔/插槽层级），则配任何真实护石也必无解，
            # 可直接跳过该防具组合的整个护石循环。
            _charm_max_skill = {}
            for _ch in _sf_charms:
                for _s, _lv in _ch.get('skills', {}).items():
                    if _s not in NO_DECO_SK and not _is_slot_skill(_s):
                        if _lv > _charm_max_skill.get(_s, 0):
                            _charm_max_skill[_s] = _lv
            _charm_max_a = []
            _charm_max_w = []
            _charm_max_total = -1
            for _ch in _sf_charms:
                _c_a = _ch.get('slots', [])
                _c_w = _ch.get('weapon_slots', [])
                _tot = sum(_c_a) + sum(_c_w)
                if _tot > _charm_max_total:
                    _charm_max_total = _tot
                    _charm_max_a = _c_a
                    _charm_max_w = _c_w

            for _sf_combo, _base_cur, _base_a, _base_w in _sf_data:
                if max_results > 0 and len(results) >= max_results:
                    break
                if (len(results) & 63) == 0 and time.time() - start_time > timeout_s:
                    if timeout_flag is not None:
                        timeout_flag[0] = True
                    break
                # 安全预检：最强护石都不行 => 任何真实护石都不行，跳过整个护石循环
                if _charm_max_skill or _charm_max_a or _charm_max_w:
                    _super_cur = dict(_base_cur)
                    for _s2, _lv2 in _charm_max_skill.items():
                        _super_cur[_s2] = _super_cur.get(_s2, 0) + _lv2
                    _super_a = _base_a + _charm_max_a
                    _super_w = _base_w + (_charm_max_w or [])
                    if not _check_deco_feasible(_super_cur, _super_a, _super_w, _merged_fixed, {},
                                                weapon_skills, min_rem_armor, min_rem_weapon):
                        continue
                for _sf_ch, _ch_slots, _ch_wslots in _charm_extra:
                    if max_results > 0 and len(results) >= max_results:
                        break
                    _cur = dict(_base_cur)
                    for _s, _lv in _sf_ch['skills'].items():
                        _cur[_s] = _cur.get(_s, 0) + _lv
                    _a_s = _base_a + _ch_slots
                    _w_s = _base_w + _ch_wslots if _ch_wslots else _base_w

                    if not _check_deco_feasible(_cur, _a_s, _w_s, _merged_fixed, {},
                                                weapon_skills, min_rem_armor, min_rem_weapon):
                        continue
                    filled = fill_slots(_cur, _a_s, _w_s, _merged_fixed, min_keep_armor=min_rem_armor, min_keep_weapon=min_rem_weapon)
                    if filled is None:
                        continue
                    fs, used, rem_a, rem_w = filled
                    _ok = True
                    for _s, _r in fixed_skills.items():
                        if _is_slot_skill(_s):
                            continue
                        if _s in NO_DECO_SK:
                            continue
                        if fs.get(_s, 0) < _r:
                            _ok = False; break
                    if _ok and combo_skills:
                        for _s, _r in combo_skills.items():
                            if _s in NO_DECO_SK:
                                continue
                            if fs.get(_s, 0) < _r:
                                _ok = False; break
                    if not _ok:
                        continue
                    if min_rem_armor > 0:
                        if sum(1 for _s in rem_a if _s > 0) < min_rem_armor:
                            continue
                    for _s in rem_a + rem_w:
                        if _s > 0:
                            for _n in range(1, _s + 1):
                                _k = f'Lv{_n}插槽'
                                fs[_k] = fs.get(_k, 0) + 1
                    _dmg = calc_damage(fs)
                    _pieces = list(_sf_combo) + [_sf_ch]
                    results.append({'pieces': _pieces, 'skills': fs, 'deco_used': used,
                                    'pract': _dmg, 'rem_a': rem_a, 'rem_w': rem_w})

            if not quiet:
                print(f"  系列优先搜索: {len(_sf_combos)}组合, {len(results)}方案, 耗时{time.time()-start_time:.3f}秒")
            # 系列优先路径的枚举不完整（无系列件每部位只试一个代表+Top2000截断），
            # 收满max_results才可直接返回；否则回退常规DFS补全（常规DFS的系列
            # 剪枝是完备的），最后按装备指纹去重。
            if max_results > 0 and len(results) >= max_results:
                results.sort(key=lambda x: -x['pract'])
                return results
            if results:
                _sf_keys = set(tuple(sorted((p.get('name', ''), str(p.get('part_idx', ''))) for p in r['pieces'])) for r in results)
                _sf_results = list(results)
                results = []
            else:
                _sf_keys = set()
                _sf_results = []
        elif not _sf_combos:
            if not quiet:
                print(f"  系列优先搜索: 无有效组合, 耗时{time.time()-start_time:.3f}秒")

    # 武器技能赤字总量（增量维护，消除DFS内的any()遍历）
    _w_def_total = sum(init_deficit[i] for i in range(n_skills) if is_weapon_deco[i])
    _w_def_total = [_w_def_total]  # list做nonlocal替代

    # ===== 部位重排序：护石优先（候选最少+决定武器技能），然后按候选数升序 =====
    part_order = sorted(range(6), key=lambda pi: (
        pi != 5,  # charm (pi=5) goes first
        len(part_cands_vec.get(pi, []))
    ))

    # 预计算剩余部位的最佳score累计和
    remaining_best_sum = [0] * 7
    for _d in range(5, -1, -1):
        _pi = part_order[_d]
        remaining_best_sum[_d] = remaining_best_sum[_d + 1] + best_by_part.get(_pi, 0)

    # 预计算每个系列技能在剩余部位中的最大可用件数（逐部位系列件数上界剪枝）
    # remaining_series_max[depth][ss_idx] = 从depth层开始剩余部位能提供的该系列最大件数
    remaining_series_max = None
    if _n_req_series > 0:
        remaining_series_max = [[0] * _n_req_series for _ in range(7)]
        for _d in range(5, -1, -1):
            _pi = part_order[_d]
            for _si, _ss in enumerate(_req_series_list):
                _max_in_part = 0
                for _c in part_cands_vec.get(_pi, []):
                    if _ss in _c['skills']:
                        _max_in_part = 1  # 每部位最多选1件
                        break
                remaining_series_max[_d][_si] = remaining_series_max[_d + 1][_si] + _max_in_part

    # 预计算每个技能在剩余部位中的最大可用量（逐技能上界剪枝）
    remaining_skill_max = [[0] * n_skills for _ in range(7)]
    for _d in range(5, -1, -1):
        _pi = part_order[_d]
        for _i in range(n_skills):
            _max_in_part = 0
            for _c in part_cands_vec.get(_pi, []):
                _sv = _c['sv']
                if _sv[_i] > _max_in_part:
                    _max_in_part = _sv[_i]
            remaining_skill_max[_d][_i] = remaining_skill_max[_d + 1][_i] + _max_in_part

    # 预计算剩余部位各等级slot的最大可用数（精确slot上界剪枝）
    # remaining_slot_by_lv[depth] = (a_lv1, a_lv2, a_lv3, w_lv1, w_lv2, w_lv3)
    remaining_slot_by_lv = [[0]*6 for _ in range(7)]
    for _d in range(5, -1, -1):
        _pi = part_order[_d]
        _best_a = [0, 0, 0, 0]  # idx 1~3
        _best_w = [0, 0, 0, 0]
        for _c in part_cands_vec.get(_pi, []):
            for _s in _c['slots']:
                if 0 < _s <= 3:
                    _best_a[_s] = max(_best_a[_s], 1)  # 每部位最多取1件
            for _s in _c.get('weapon_slots', []):
                if 0 < _s <= 3:
                    _best_w[_s] = max(_best_w[_s], 1)
        _prev = remaining_slot_by_lv[_d + 1]
        for _lv in range(1, 4):
            remaining_slot_by_lv[_d][_lv - 1] = _prev[_lv - 1] + _best_a[_lv]
            remaining_slot_by_lv[_d][_lv + 2] = _prev[_lv + 2] + _best_w[_lv]

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
            # 选pts最高的珠子（给技能等级最多的）
            _best_entry = max(_pool, key=lambda x: x[1])
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
    remaining_max_slot_sum = [0] * 7
    for _d in range(5, -1, -1):
        _pi = part_order[_d]
        _max_ss = 0
        for _c in part_cands_vec.get(_pi, []):
            _ss = _c['slot_sum'] + _c.get('w_slot_sum', 0)
            if _ss > _max_ss:
                _max_ss = _ss
        remaining_max_slot_sum[_d] = remaining_max_slot_sum[_d + 1] + _max_ss

    # 保留def_weight用于兼容（但不再作为主触发条件）
    init_def_weight = sum(deco_weight[_i] * init_deficit[_i]
                          for _i in range(n_skills) if init_deficit[_i] > 0)
    _def_weight = [init_def_weight]

    # ===== 预计算孔位技能需求（避免每次遍历fixed_skills）=====
    _slot_skill_needs = []  # [(lv, count), ...] 如 [(1, 3)] 表示需要3个Lv1插槽
    _side_slot_needs = []   # [(side, lv, count), ...] 分侧孔位需求，如 ('armor', 1, 3)
    for _sk, _lv in fixed_skills.items():
        try:
            if _sk.startswith('防具Lv') and _sk.endswith('插槽'):
                _n = int(_sk[4:-2])
                _side_slot_needs.append(('armor', _n, _lv))
            elif _sk.startswith('武器Lv') and _sk.endswith('插槽'):
                _n = int(_sk[4:-2])
                _side_slot_needs.append(('weapon', _n, _lv))
            elif _sk.startswith('Lv') and _sk.endswith('插槽'):
                _n = int(_sk[2:-2])
                _slot_skill_needs.append((_n, _lv))
        except ValueError:
            pass

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
        # 孔位技能需求：各等级精确匹配（预留LvN只消耗LvN槽位，不降级）
        for _n, _need in _slot_skill_needs:
            if a_cnt[_n] + w_cnt[_n] < _need:
                return False
        for _side, _n, _need in _side_slot_needs:
            _cnt = a_cnt if _side == 'armor' else w_cnt
            if _cnt[_n] < _need:
                return False
        # 计算珠子需求（按slot等级分桶）
        a_need = [0, 0, 0, 0]
        w_need = [0, 0, 0, 0]
        for _i in range(n_skills):
            _d = _deficit[_i]
            if _d <= 0:
                continue
            if best_deco_pts[_i] == 0:
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

    def _try_fill_and_record(incremental=False):
        # 可行性预检前置：incremental路径（depth>=6，6件已全部放置）先用增量严格检查
        # （绝大多数叶子在此被拒），通过后才准备数据，避免每叶子白做重建。
        # incremental=False（_try_early_fill补位路径，增量状态不含补位件）退回重建式检查。
        if incremental:
            if not _strict_leaf_check():
                return False
            # 增量状态与实际选择严格一致，直接用，无需重建
            # （_a_slots/_w_slots已含武器基底WSLOTS，fill_slots内部会自行复制/排序）
            cur_skills = _cur_all_skills
            a_s = _a_slots
            w_s = _w_slots
        else:
            if any(equipped[j] is None for j in range(6)):
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
                    if e.get('weapon_slots'):
                        w_s.extend(e['weapon_slots'])
            if not _check_deco_feasible(cur_skills, a_s, w_s, merged_fixed_once, {},
                                        weapon_skills, min_rem_armor, min_rem_weapon):
                return False
        filled = fill_slots(cur_skills, a_s, w_s, merged_fixed_once, min_keep_armor=min_rem_armor, min_keep_weapon=min_rem_weapon)
        if filled is None:
            return False
        fs, used, rem_a, rem_w = filled
    
        if incremental:
            # 增量系列计数与当前选择严格一致，直接用
            _sp = _series_count
            _wsp = _weapon_series_cnt
        else:
            # 补位路径：增量状态不含补位件，从equipped重数
            _sp = {}
            for e in equipped:
                if e is None:
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
                    return False
                continue
            if fs.get(s, 0) < r: return False
        if combo_skills:
            for s, r in combo_skills.items():
                if s in NO_DECO_SK:
                    if _sp.get(s, 0) + _wsp.get(s, 0) < r:
                        return False
                    continue
                if fs.get(s, 0) < r: return False
        if min_rem_armor > 0:
            if sum(1 for s in rem_a if s > 0) < min_rem_armor: return False
        for s in rem_a + rem_w:
            if s > 0:
                for n in range(1, s + 1):
                    k = f'Lv{n}插槽'
                    fs[k] = fs.get(k, 0) + 1
        dmg = calc_damage(fs)
        pieces = [e for e in equipped if e]
        results.append({'pieces': pieces, 'skills': fs, 'deco_used': used,
                        'pract': dmg, 'rem_a': rem_a, 'rem_w': rem_w})
        return True

    # ===== 赤字加权总和的增量更新（保留用于兼容）=====

    # 预计算初始有赤字的技能索引列表
    init_deficit_indices = tuple(_i for _i in range(n_skills) if init_deficit[_i] > 0)

    def _greedy_deco_check_with_future(depth):
        """增强版贪心检查：考虑剩余部位提供的slot（用增量slot计数优化）"""
        rsl = remaining_slot_by_lv[depth] if depth < 6 else [0]*6
        # 用增量维护的slot计数替代遍历
        a_cnt = [_a_slot_cnt[0], _a_slot_cnt[1], _a_slot_cnt[2], _a_slot_cnt[3]]
        w_cnt = [_w_slot_cnt[0], _w_slot_cnt[1], _w_slot_cnt[2], _w_slot_cnt[3]]
        # 加上剩余部位的slot（上界估计）
        a_cnt[1] += rsl[0]; a_cnt[2] += rsl[1]; a_cnt[3] += rsl[2]
        w_cnt[1] += rsl[3]; w_cnt[2] += rsl[4]; w_cnt[3] += rsl[5]
        # 预留孔位扣减
        _rem = min_rem_armor
        for _lv in [1, 2, 3]:
            while _rem > 0 and a_cnt[_lv] > 0:
                a_cnt[_lv] -= 1
                _rem -= 1
        if _rem > 0:
            return False
        # 孔位技能需求：各等级精确匹配（预留LvN只消耗LvN槽位，不降级）
        for _n, _need in _slot_skill_needs:
            if a_cnt[_n] + w_cnt[_n] < _need:
                return False
        for _side, _n, _need in _side_slot_needs:
            _cnt = a_cnt if _side == 'armor' else w_cnt
            if _cnt[_n] < _need:
                return False
        # 扣除孔位预留槽，避免与珠子需求的降级链检查重复计数同一槽位
        for _n, _need in _slot_skill_needs:
            # 预留优先消耗高阶槽（低阶槽更稀缺，只能放低阶珠，留给珠子需求）
            _take = min(_need, w_cnt[_n])
            w_cnt[_n] -= _take
            a_cnt[_n] -= (_need - _take)
        for _side, _n, _need in _side_slot_needs:
            _cnt = a_cnt if _side == 'armor' else w_cnt
            _cnt[_n] -= _need
        # 计算珠子需求
        a_need = [0, 0, 0, 0]
        w_need = [0, 0, 0, 0]
        for _i in range(n_skills):
            _d = _deficit[_i]
            if _d <= 0:
                continue
            if best_deco_pts[_i] == 0:
                return False
            _slots_needed = (_d + best_deco_pts[_i] - 1) // best_deco_pts[_i]
            if is_weapon_deco[_i]:
                w_need[best_deco_slot[_i]] += _slots_needed
            else:
                a_need[best_deco_slot[_i]] += _slots_needed
        # slot降级链检查
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
            return False
        _rem_w = min_rem_weapon
        for _lv in [1, 2, 3]:
            while _rem_w > 0 and w_cnt[_lv] > 0:
                w_cnt[_lv] -= 1
                _rem_w -= 1
        if _rem_w > 0:
            return False
        for _n, _need in _slot_skill_needs:
            if a_cnt[_n] + w_cnt[_n] < _need:
                return False
        for _side, _n, _need in _side_slot_needs:
            _cnt = a_cnt if _side == 'armor' else w_cnt
            if _cnt[_n] < _need:
                return False
        # 扣除孔位预留槽，避免与珠子需求的降级链检查重复计数同一槽位
        for _n, _need in _slot_skill_needs:
            # 预留优先消耗高阶槽（低阶槽更稀缺，只能放低阶珠，留给珠子需求）
            _take = min(_need, w_cnt[_n])
            w_cnt[_n] -= _take
            a_cnt[_n] -= (_need - _take)
        for _side, _n, _need in _side_slot_needs:
            _cnt = a_cnt if _side == 'armor' else w_cnt
            _cnt[_n] -= _need
        a_need = [0, 0, 0, 0]
        w_need = [0, 0, 0, 0]
        a_total_need = 0
        a_max_pts = 1
        for _i in range(n_skills):
            _d = _deficit[_i]
            if _d <= 0:
                continue
            if best_deco_pts[_i] == 0:
                return False
            _slots_needed = (_d + best_deco_pts[_i] - 1) // best_deco_pts[_i]
            if is_weapon_deco[_i]:
                w_need[best_deco_slot[_i]] += _slots_needed
            else:
                a_need[best_deco_slot[_i]] += _slots_needed
                a_total_need += _d
                if best_deco_pts[_i] > a_max_pts:
                    a_max_pts = best_deco_pts[_i]
        if a_total_need > 0:
            if (a_total_need + a_max_pts - 1) // a_max_pts > a_cnt[1] + a_cnt[2] + a_cnt[3]:
                return False
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

    def _try_early_fill(depth):
        """提前填充：用剩余部位的候选填充未选部位，优先选含系列技能件的"""
        if depth >= 6:
            return _try_fill_and_record(incremental=True)
        temp_equipped = []
        for d in range(depth, 6):
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
                best = cands[0]
            equipped[pi] = best
            temp_equipped.append(pi)
        success = _try_fill_and_record()
        for pi in temp_equipped:
            equipped[pi] = None
        return success

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
        if (len(results) & 255) == 0 and time.time() - start_time > timeout_s:
            if timeout_flag is not None:
                timeout_flag[0] = True
            return

        cur_def_score = _def_score

        # ===== 系列技能件数快速检查（增量数组+逐部位上界剪枝）=====
        if _n_req_series > 0 and remaining_series_max is not None:
            rsm_s = remaining_series_max[depth] if depth < 6 else [0]*_n_req_series
            for _si in range(_n_req_series):
                have_pieces = _series_wprov[_si] + _series_have[_si]
                need_pieces = _series_need_pieces[_si]
                if have_pieces < need_pieces:
                    need_cnt = need_pieces - have_pieces
                    if rsm_s[_si] < need_cnt:
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
                if depth >= 6:
                    _try_fill_and_record(incremental=True)
                else:
                    _try_early_fill(depth)
                return

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

        # ===== 贪心珠子检查 + 提前填充 =====
        cur_a_demand = _a_slot_demand[0]
        cur_w_demand = _w_slot_demand[0]
        cur_slot_total = _a_slot_sum + _w_slot_sum
        total_demand = cur_a_demand + cur_w_demand
        if total_demand > 0 and depth < 6:
            max_future_slot = remaining_max_slot_sum[depth]
            if total_demand <= cur_slot_total + max_future_slot:
                if _greedy_deco_check_with_future(depth):
                    if _try_early_fill(depth):
                        return
        elif total_demand == 0 and depth >= 6:
            _try_fill_and_record(incremental=True)
            return

        # ===== 全部6件装备时尝试填充 =====
        if depth >= 6:
            series_ok = True
            if _n_req_series > 0:
                for _si in range(_n_req_series):
                    if _series_wprov[_si] + _series_have[_si] < _series_need_pieces[_si]:
                        series_ok = False
                        break
            if series_ok:
                _try_fill_and_record(incremental=True)
            return

        # ===== 获取当前部位候选 =====
        part_idx = part_order[depth]
        part_cands = part_cands_vec.get(part_idx, [])
        if not part_cands:
            return

        is_charm_slot = (part_idx == 5)
        cur_w_def = _w_def_total[0]
        _wslots_cap = sum(WSLOTS) * 2  # 预计算

        # ===== 遍历该部位候选装备 =====
        best_slot_only_tried = False  # 是否已尝试过纯孔位候选
        _n_results = len(results)
        for Q in part_cands:
            if max_results > 0 and _n_results >= max_results:
                return

            # per-candidate上界break
            q_score = Q['score']
            remaining_after = remaining_best_sum[depth + 1] if depth < 5 else 0
            if q_score + remaining_after + cur_slot_total < cur_def_score:
                break

            # 护石武器技能检查
            if is_charm_slot and cur_w_def > 0:
                if not Q.get('_has_wsk', False) and cur_w_def > _wslots_cap:
                    continue

            # ===== 增量计算选Q后的赤字变化（用nz遍历）=====
            nz = Q['nz']
            q_max_slot = Q['max_slot']
            q_has_req_series = Q.get('_has_req_series', False)
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
                            continue
                        if q_max_slot == 0:
                            for i, old_sk, old_def in changed:
                                _skills_vec[i] = old_sk
                                _deficit[i] = old_def
                            continue
                        best_slot_only_tried = True
                        if not _greedy_deco_check_with_future(depth):
                            for i, old_sk, old_def in changed:
                                _skills_vec[i] = old_sk
                                _deficit[i] = old_def
                            continue
                    else:
                        # 含系列技能件但不减少赤字：只在系列件数还不够时才递归
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
                            for i, old_sk, old_def in changed:
                                _skills_vec[i] = old_sk
                                _deficit[i] = old_def
                            continue

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
                item_wslots = Q.get('weapon_slots', [])
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
                # 增量更新全技能dict（叶子记录用，撤销时回退）
                item_skills = Q['skills']
                _changed_all_sk = []
                for _sk2, _lv2 in item_skills.items():
                    _old_v = _cur_all_skills.get(_sk2, 0)
                    _cur_all_skills[_sk2] = _old_v + _lv2
                    _changed_all_sk.append((_sk2, _old_v))

                # 更新系列件数（增量数组+dict兼容）
                changed_series = []
                changed_series_idx = []
                for s in item_skills:
                    if s in NO_DECO_SK and s not in SLOT_SKILLS:
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

    # 与系列优先路径的结果去重（同装备组合只保留一条）；
    # 系列优先路径已找到的方案必须保留（回退DFS可能因剪枝找不到）
    if _sf_keys or _sf_results:
        _seen = set(_sf_keys)
        _dedup = list(_sf_results)
        for r in results:
            _k = tuple(sorted((p.get('name', ''), str(p.get('part_idx', ''))) for p in r['pieces']))
            if _k in _seen:
                continue
            _seen.add(_k)
            _dedup.append(r)
        results = _dedup

    if not quiet:
        print(f"  DFS完成: {len(results)}方案, 耗时{time.time()-start_time:.3f}秒")
    if max_results == 0 or len(results) < max_results:
        results.sort(key=lambda x: -x['pract'])
    return results


def _generate_weapon_equipment(fixed_skills, combo_skills, disabled_weapon_skills=None,
                               fixed_series=None, fixed_group=None):
    """生成武器装备列表：每个武器技能组合是一个独立装备

    武器可以带：
    - 1个系列技能（如火龙之力、黑蚀龙之力等）
    - 1个组合技能（如霸主之魂）
    - 两者都带

    fixed_series/fixed_group: 用户固定（预筛选）的武器技能。
    固定武器技能 ≠ 技能需求：固定=预筛选武器（只带该技能的武器进入匹配池），
    不是把该技能作为配装硬性需求。
    - 固定系列：只生成带该系列的武器（组合留空时×需求相关的组合）
    - 固定组合：只生成带该组合的武器（系列留空时×需求相关的系列）
    - 两者固定：只剩1把武器（由调用方直接搜索，不走枚举）
    - 均留空：生成所有"用户已需求"技能相关的组合（未需求系列与无技能等价，不生成）

    返回武器装备列表，每个装备的part_idx=6
    """

    disabled = disabled_weapon_skills or set()

    # 获取需求的系列技能和组合技能
    need_series = {}
    for s, lv in fixed_skills.items():
        if s in NO_DECO_SK:
            need_series[s] = max(need_series.get(s, 0), lv)
    if combo_skills:
        for s, lv in combo_skills.items():
            if s in NO_DECO_SK:
                need_series[s] = max(need_series.get(s, 0), lv)

    # 系列技能候选：
    # - 固定系列：只保留该系列（预筛选缩小武器池，节省组合时间）
    # - 留空：只保留"用户已需求"的系列（武器系列只有在与需求系列一致时
    #   才有意义——它贡献1件帮助凑满需求，作为"补充"）。未需求的系列武器与"无技能"
    #   武器等价（惰性件数不奖励伤害），故不再生成，以大幅缩减候选数、加速自动匹配。
    # 尝试所有有效等级（Lv2=效果I, Lv4=效果II）
    series_candidates = []
    if fixed_series:
        if fixed_series not in disabled:
            priority = 1
            series_candidates.append((fixed_series, 2, priority))
            series_candidates.append((fixed_series, 4, priority))
    else:
        for s in SERIES_SK:
            if s in disabled:
                continue
            if s not in need_series:
                continue  # 只保留与需求系列一致的武器
            priority = 1
            series_candidates.append((s, 2, priority))
            series_candidates.append((s, 4, priority))

    # 组合技能候选：
    # - 固定组合：只保留该组合（预筛选缩小武器池）
    # - 留空：只保留"用户已需求"的组合技能（同理，作为组件补充件数）
    group_candidates = []
    if fixed_group:
        if fixed_group not in disabled:
            group_candidates.append((fixed_group, 3, 1))
    else:
        for s in GROUP_SK:
            if s in disabled:
                continue
            if s not in need_series:
                continue
            priority = 1
            group_candidates.append((s, 3, priority))
    
    # 构造所有武器装备组合
    weapon_equipments = []
    
    # 计算武器孔位（固定）
    weapon_slots = list(WSLOTS)
    w_slot_sum = sum(weapon_slots)
    
    # 只带系列技能（固定组合时武器必须带固定组合，跳过只带系列的武器）
    if not fixed_group:
        for s_name, s_lv, priority in series_candidates:
            skills = {s_name: 1}  # 武器只提供1级
            name = f"武器[{s_name} Lv{s_lv}]"
            weapon_equipments.append({
                'name': name,
                'part_idx': 6,
                'skills': skills,
                'slots': [],
                'slots_sorted': (),
                'weapon_slots': weapon_slots,
                'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
                'rarity': 0,
                'score': priority * 100 + s_lv,
                'max_slot': max(weapon_slots) if weapon_slots else 0,
                'slot_sum': 0,
                'w_slot_sum': w_slot_sum,
                '_is_weapon': True,
                '_weapon_series': s_name,
                '_weapon_series_level': s_lv,
                '_weapon_group': None,
            })

    # 只带组合技能（固定系列时武器必须带固定系列，跳过只带组合的武器）
    if not fixed_series:
        for g_name, g_lv, priority in group_candidates:
            skills = {g_name: 1}
            name = f"武器[{g_name} Lv{g_lv}]"
            weapon_equipments.append({
                'name': name,
                'part_idx': 6,
                'skills': skills,
                'slots': [],
                'slots_sorted': (),
                'weapon_slots': weapon_slots,
                'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
                'rarity': 0,
                'score': priority * 100 + g_lv,
                'max_slot': max(weapon_slots) if weapon_slots else 0,
                'slot_sum': 0,
                'w_slot_sum': w_slot_sum,
                '_is_weapon': True,
                '_weapon_series': None,
                '_weapon_series_level': None,
                '_weapon_group': g_name,
            })

    # 同时带系列+组合
    for s_name, s_lv, s_priority in series_candidates:
        for g_name, g_lv, g_priority in group_candidates:
            skills = {s_name: 1, g_name: 1}
            name = f"武器[{s_name} Lv{s_lv} + {g_name} Lv{g_lv}]"
            combined_priority = min(s_priority, g_priority)
            weapon_equipments.append({
                'name': name,
                'part_idx': 6,
                'skills': skills,
                'slots': [],
                'slots_sorted': (),
                'weapon_slots': weapon_slots,
                'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
                'rarity': 0,
                'score': combined_priority * 1000 + s_lv + g_lv,
                'max_slot': max(weapon_slots) if weapon_slots else 0,
                'slot_sum': 0,
                'w_slot_sum': w_slot_sum,
                '_is_weapon': True,
                '_weapon_series': s_name,
                '_weapon_series_level': s_lv,
                '_weapon_group': g_name,
            })
    
    # 添加一个"无武器技能"的装备（武器不带任何技能）
    # 固定技能时武器池预筛选为只带固定技能的武器，无技能武器不在池中
    if not fixed_series and not fixed_group:
        weapon_equipments.append({
            'name': '武器[无技能]',
            'part_idx': 6,
            'skills': {},
            'slots': [],
            'slots_sorted': (),
            'weapon_slots': weapon_slots,
            'wslots_sorted': tuple(sorted(weapon_slots, reverse=True)),
            'rarity': 0,
            'score': 0,  # 最低优先级
            'max_slot': max(weapon_slots) if weapon_slots else 0,
            'slot_sum': 0,
            'w_slot_sum': w_slot_sum,
            '_is_weapon': True,
            '_weapon_series': None,
            '_weapon_group': None,
        })
    
    return weapon_equipments


def dfs_search_auto_weapon(charm_pool, fixed_skills, combo_skills, min_rem_armor,
                           max_results=5, timeout_s=10.0, quiet=True,
                           disabled_weapon_skills=None, total_timeout=30.0,
                           min_rem_weapon=0, user_weapon_skills=None):
    """武器技能自动匹配最优：将武器视为独立装备，参与DFS搜索选择最优方案。
    
    核心思想：武器 = 独立装备部位（part_idx=6），每个武器技能组合是一个独立装备。
    搜索时程序自动选择最优的武器装备（无技能/系列/组合/系列+组合）。
    
    user_weapon_skills: 用户在武器配置区选择的技能（如果已指定则直接使用）
    """
    start_time = time.time()

    # 拆分固定技能：固定武器技能≠技能需求，固定=预筛选武器
    # （只带该技能的武器进入匹配池，武器提供1件该技能，不作为配装硬性需求）
    fixed_series = None
    fixed_group = None
    for sk in (user_weapon_skills or {}):
        if sk in SERIES_SK:
            fixed_series = sk
        elif sk in GROUP_SK:
            fixed_group = sk

    # 系列+组合都固定：预筛选后武器池只剩1把武器，直接搜索（无需枚举组合）
    if fixed_series and fixed_group:
        results = dfs_search(charm_pool, fixed_skills, combo_skills, min_rem_armor,
                             max_results=max_results, timeout_s=timeout_s, quiet=quiet,
                             min_rem_weapon=min_rem_weapon, user_weapon_skills=user_weapon_skills)
        for r in results:
            r['_auto_weapon_series'] = fixed_series
            r['_auto_weapon_group'] = fixed_group
        return results, fixed_series, fixed_group

    # ===== 预构建通用候选池（只构建一次）=====
    if not quiet:
        print('  预构建候选池...')
    t0 = time.time()
    cached_ctx = _build_candidates(charm_pool, fixed_skills, combo_skills, quiet=quiet, user_weapon_skills={})
    if not quiet:
        print(f'  候选池构建耗时: {time.time() - t0:.3f}s')

    # 生成武器装备列表（预筛选：固定技能只生成带该技能的武器，缩小匹配池）
    weapon_equipments = _generate_weapon_equipment(fixed_skills, combo_skills, disabled_weapon_skills,
                                                   fixed_series=fixed_series, fixed_group=fixed_group)
    
    if not quiet:
        print(f"  生成{len(weapon_equipments)}种武器装备")

    # ===== 枚举每个武器候选，各搜少量方案，合并后按伤害排序 =====
    # 用户理念：武器与防具/护石平权，自动匹配时每个方案独立选择最优武器，
    # 而非先固定一把最优武器再搜索（否则所有方案共用同一武器，非平权）。
    all_results = []
    per_weapon_limit = max(1, min(20, max_results))  # 每个武器候选贡献的方案上限
    for weq in weapon_equipments:
        if time.time() - start_time > total_timeout:
            if not quiet:
                print(f"  自动匹配武器技能: 总超时({total_timeout}s)，返回已收集方案")
            break

        # ===== 武器作为"平权组件"参与搜索（不做约束）=====
        # 用户理念：武器技能与防具/护石平权，是组件而非约束。
        # 因此武器提供的系列/组合技能只作为"1件补充"（帮助凑已需求的系列件数 /
        # 提供武器孔），但【不】把武器自身的系列需求等级当作硬性需求强加给配装
        # （避免"宽裕技能组因自动匹配武器额外引入系列需求而只剩寥寥数方案"）。
        candidate_weapon_skills = {}
        new_combo = dict(combo_skills) if combo_skills else {}

        if weq['_weapon_series']:
            # 武器仅提供1件该系列；是否真正激活由配装总件数决定，不强加需求
            candidate_weapon_skills[weq['_weapon_series']] = 1
        if weq['_weapon_group']:
            candidate_weapon_skills[weq['_weapon_group']] = 1

        try:
            results = dfs_search(charm_pool, fixed_skills, new_combo, min_rem_armor,
                                 max_results=per_weapon_limit, timeout_s=timeout_s, quiet=quiet,
                                 min_rem_weapon=min_rem_weapon, user_weapon_skills=candidate_weapon_skills,
                                 cached_ctx=cached_ctx)
        except Exception:
            results = []

        if not results:
            continue

        if not quiet:
            parts = []
            if weq['_weapon_series']:
                parts.append(f"{weq['_weapon_series']}")
            if weq['_weapon_group']:
                parts.append(f"{weq['_weapon_group']}")
            ws_str = ' + '.join(parts) if parts else '无技能'
            print(f"    {ws_str}: {len(results)}方案 最高{results[0].get('pract', 0):.1f}")

        # 标记该方案实际使用的武器（每个方案独立选择）
        for r in results:
            if weq['_weapon_series']:
                r['_auto_weapon_series'] = weq['_weapon_series']
            if weq['_weapon_group']:
                r['_auto_weapon_group'] = weq['_weapon_group']
        all_results.extend(results)

    if not all_results:
        # 所有武器技能都搜不到方案，回退到无武器技能搜索
        results = dfs_search(charm_pool, fixed_skills, combo_skills, min_rem_armor,
                             max_results=max_results, timeout_s=timeout_s, quiet=quiet,
                             min_rem_weapon=min_rem_weapon, user_weapon_skills=user_weapon_skills)
        return results, None, None

    # 合并排序：按伤害降序（各方案的武器可能不同）
    all_results.sort(key=lambda x: -x.get('pract', 0))
    # 去重：防具+护石组合 + 武器组合均相同时保留伤害最高的
    seen = set()
    unique = []
    for r in all_results:
        pieces_key = tuple(sorted((p.get('name') for p in r.get('pieces', []))))
        wkey = (r.get('_auto_weapon_series'), r.get('_auto_weapon_group'))
        key = (pieces_key, wkey)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    best_results = unique[:max_results] if max_results > 0 else unique

    # 最优方案（第一个）的武器作为整体展示信息
    top_r = best_results[0]
    best_weapon_series = top_r.get('_auto_weapon_series')
    best_weapon_group = top_r.get('_auto_weapon_group')

    if not quiet:
        parts = []
        if best_weapon_series:
            parts.append(f"{best_weapon_series}")
        if best_weapon_group:
            parts.append(f"{best_weapon_group}")
        ws_str = ' + '.join(parts) if parts else '无技能'
        print(f"  自动匹配武器技能: {ws_str}，返回{len(best_results)}方案（各方案独立选择武器）")

    return best_results, best_weapon_series, best_weapon_group


_quick_skill_cache_tl = threading.local()
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
    for pi in range(6):
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
        total_slots = sum(best_slot_by_part.get(pi, 0) for pi in range(6)) + sum(wslots)
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

