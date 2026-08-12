#!/usr/bin/env python3
"""MHWilds 配装搜索 GUI 服务器

使用 Python 内置 http.server 模块，无需额外依赖。
运行: python3 gui_server.py [端口]
默认端口: 8765
"""
import json
import sys
import os
import time
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

import fast_search_v3 as fs
from calc_v8_final import (
    PLAN_CFGS, BASE_FIXED_MIN, DAMAGE_SKILLS,
    make_core_for_plan, verify_series, search_for_plan,
    run_all_plans, print_summary, print_results_detail
)

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(SERVER_DIR, 'index.html')
SKILLS_DATA_PATH = os.path.join(SERVER_DIR, 'skills_data.json')

_lock = threading.Lock()

_SKILLS_DATA = None
def _load_skills_data():
    global _SKILLS_DATA
    if _SKILLS_DATA is None:
        try:
            with open(SKILLS_DATA_PATH, 'r', encoding='utf-8') as f:
                _SKILLS_DATA = json.load(f)
        except Exception:
            _SKILLS_DATA = {}
    return _SKILLS_DATA


def _build_skill_caps():
    """从skills_data.json自动生成所有技能的上限等级"""
    sd = _load_skills_data()
    caps = {}
    for category_key in ['武器技能', '防具技能']:
        for name, info in sd.get(category_key, {}).items():
            lv = info.get('max_lv', 0)
            if lv > 0:
                caps[name] = lv
    # 系列技能: 霸主之魂上限3，其余系列上限4
    for name in sd.get('系列技能', {}):
        if name == '说明':
            continue
        if name == '霸主之魂':
            caps[name] = 3
        else:
            caps[name] = 4
    # 组合技能上限3
    for name in sd.get('组合技能', {}):
        if name == '说明':
            continue
        caps[name] = 3
    return caps


def _build_skill_categories():
    """从skills_data.json自动构建分类"""
    sd = _load_skills_data()
    categories = {}

    # 攻击·会心: 直接与伤害/会心相关
    categories['攻击·会心'] = [
        '攻击', '看破', '超会心', '弱点特效', '挑战者', '连击', '无伤',
        '攻击守势', '巧击', '因祸得福', '精神抖擞', '无我之境', '力量解放',
        '攻势', '逆袭', '急袭', '怨恨', '拔刀术【技】', '拔刀术【力】'
    ]

    # 属性·特殊: 属性攻击强化及特殊弹
    categories['属性·特殊'] = [
        '龙属性攻击强化', '火属性攻击强化', '水属性攻击强化', '冰属性攻击强化',
        '雷属性攻击强化', '会心击【属性】', '会心击【特殊】',
        '属性吸收', '属性变换', '锁刃刺击',
        '蓄力大师', '集中', '夺取耐力', '击晕术',
        '炮术', '高速变形', '吹笛名人', '飞燕',
        '通常弹·通常箭强化', '贯穿弹·龙之箭强化', '散弹·刚射强化',
        '弹道强化', '速射强化', '首发迅击', '强四射击', '特殊射击强化',
        '炮弹装填', '蓄击强化'
    ]

    # 锋利度: 武器锋利度相关
    categories['锋利度'] = [
        '匠', '利刃', '刚刃打磨', '心眼', '钝器能手', '达人艺', '砥石使用高速化'
    ]

    # 防御·生存: 防御和生存技能
    categories['防御·生存'] = [
        '格挡性能', '格挡强化', '防御', '精灵加护', '缓冲', '耳塞',
        '回避性能', '回避距离提升', '火场怪力', '纳刀术', '减轻胆怯'
    ]

    # 辅助·回复: 辅助和回复技能
    categories['辅助·回复'] = [
        '广域化', '满足感', '快吃', '体术', '跑者', '强化持续',
        '体力回复量提升', '回复速度', '耐力急速回复', '饥饿耐性',
        '整备', '炸弹客', '最爱蘑菇', '道具使用强化', '威吓',
        '适应环境', '环境利用知识', '植生学', '地质学', '破坏王', '钻研'
    ]

    # 异常属性·耐性: 属性强化和耐性
    categories['异常属性·耐性'] = [
        '毒伤害强化', '毒属性强化', '麻痹属性强化', '睡眠属性强化', '爆破属性强化',
        '毒瓶追加', '麻痹瓶追加', '睡眠瓶追加', '爆破瓶追加', '减气瓶追加',
        '火耐性', '水耐性', '雷耐性', '冰耐性', '龙耐性',
        '毒耐性', '麻痹耐性', '睡眠耐性', '昏厥耐性', '裂伤耐性',
        '束缚耐性', '爆破异常耐性', '恶臭耐性', '属性异常耐性',
        '防御力下降耐性', '风压耐性', '耐震', '适应水域·油泥',
        '闪光强化', '指示随从', '猎人生活'
    ]

    # 系列技能: 全部系列
    series_skills = []
    for name in sd.get('系列技能', {}):
        if name != '说明':
            series_skills.append(name)
    categories['系列技能'] = series_skills

    # 组合技能
    combo_skills = []
    for name in sd.get('组合技能', {}):
        if name != '说明':
            combo_skills.append(name)
    categories['组合技能'] = combo_skills

    # 武器技能中的专属技能
    extra_weapon = ['拔刀术【技】', '拔刀术【力】', '格挡性能', '格挡强化']
    for s in extra_weapon:
        if s not in categories.get('防御·生存', []):
            if s in sd.get('武器技能', {}):
                # 已在其他分类中
                pass

    # 破坏王、怨恨等归到攻击类或其他
    misc = []
    all_placed = set()
    for cat_skills in categories.values():
        all_placed.update(cat_skills)

    for name in sd.get('防具技能', {}):
        if name != '说明' and name not in all_placed:
            misc.append(name)
    for name in sd.get('武器技能', {}):
        if name != '说明' and name not in all_placed:
            if name not in ['攻击守势', '拔刀术【技】', '拔刀术【力】']:
                misc.append(name)
    categories['其他'] = misc

    return categories


# 自动构建技能上限和分类
SKILL_CAPS = _build_skill_caps()
SKILL_CATEGORIES = _build_skill_categories()


def _plan_result_to_dict(best, plan_cfg, t_used, attempts, verified, sch_t, requirement_skills=None):
    """将搜索结果转换为可JSON序列化的字典"""
    if not best:
        return None
    weapon_sk = plan_cfg[1]
    armors = [p for p in best['pieces'] if p.get('part_idx', 5) < 5]
    charms = [p for p in best['pieces'] if p.get('part_idx', 5) >= 5]
    part_names_cn = ['头', '身', '手', '腰', '腿']

    armor_list = []
    for p in armors:
        pi = p.get('part_idx', 0)
        armor_list.append({
            'part': part_names_cn[pi] if pi < 5 else '护石',
            'name': p['name'],
            'slots': list(p.get('slots', [])),
            'skills': {k: v for k, v in p.get('skills', {}).items() if v > 0}
        })
    charm_info = None
    weapon_info = None
    for c in charms:
        if c.get('part_idx', 5) == 5:
            charm_info = {
                'name': c['name'],
                'skills': {k: v for k, v in c.get('skills', {}).items() if v > 0},
                'armor_slots': list(c.get('slots', [])),
                'weapon_slots': list(c.get('weapon_slots', []))
            }
        elif c.get('part_idx', 5) == 6:
            weapon_info = {
                'name': c['name'],
                'skills': {k: v for k, v in c.get('skills', {}).items() if v > 0},
                'weapon_slots': list(c.get('weapon_slots', []))
            }

    # 系列技能验证（统计防具+护石+武器件数）
    series_actual = {}
    for p in armors:
        for sk_name in p.get('skills', {}):
            if sk_name in fs.NO_DECO_SK:
                series_actual[sk_name] = series_actual.get(sk_name, 0) + 1
    if charm_info and charm_info.get('skills'):
        for sk_name in charm_info['skills']:
            if sk_name in fs.NO_DECO_SK:
                series_actual[sk_name] = series_actual.get(sk_name, 0) + 1
    # 武器部位的系列技能也计入
    for p in best['pieces']:
        if p.get('part_idx', 5) == 6:
            for sk_name in p.get('skills', {}):
                if sk_name in fs.NO_DECO_SK:
                    series_actual[sk_name] = series_actual.get(sk_name, 0) + 1
    series_check = []
    sk = {k: v for k, v in best['skills'].items() if v > 0}
    # 从实际pieces中提取武器提供的系列/组合技能（不依赖plan_cfg）
    actual_weapon_skills = {}
    for p in best.get('pieces', []):
        if p.get('part_idx', 5) == 6:
            for sk_name, lv in p.get('skills', {}).items():
                if sk_name in fs.NO_DECO_SK and lv > 0:
                    actual_weapon_skills[sk_name] = lv
    for k, v in sk.items():
        if k in fs.NO_DECO_SK:
            cap = fs.SKILL_CAPS.get(k, 1)
            # 需求等级：优先使用用户选择的需求等级，否则使用上限（最大等级）
            if requirement_skills and k in requirement_skills:
                user_lv = requirement_skills[k]
            else:
                user_lv = cap
            need_p = max(1, user_lv)
            wprov = actual_weapon_skills.get(k, 0)
            actual_p = series_actual.get(k, 0) + wprov
            display_level = min(actual_p, cap)
            series_check.append({
                'skill': k, 'level': display_level, 'need_pieces': need_p,
                'weapon_provided': wprov, 'armor_pieces': series_actual.get(k, 0),
                'actual_pieces': actual_p, 'ok': actual_p >= need_p
            })

    # 装饰品统计
    decos_used = best.get('deco_used', [])
    from collections import Counter
    if decos_used and isinstance(decos_used[0], dict):
        dnames = [d.get('name', '?') for d in decos_used]
    else:
        dnames = list(decos_used)
    deco_counts = dict(Counter(dnames))

    # 核心技能展示
    core_skills = {}
    for k in DAMAGE_SKILLS:
        if k in sk:
            cap = fs.SKILL_CAPS.get(k, 0)
            lv = min(sk[k], cap) if cap > 0 else sk[k]
            if cap > 0 and lv > 0:
                core_skills[k] = {'level': lv, 'cap': cap}

    # 其他技能
    other_skills = {}
    for k, v in sk.items():
        if k not in DAMAGE_SKILLS and v > 0:
            cap = fs.SKILL_CAPS.get(k, 0)
            lv = min(v, cap) if cap > 0 else v
            if cap > 0 and lv > 0:
                other_skills[k] = {'level': lv, 'cap': cap}

    cfg_d = best.get('_cfg', {})
    weapon_skill_info = {}
    if weapon_sk:
        for s, lv in weapon_sk.items():
            if lv > 0:
                weapon_skill_info[s] = lv
    return {
        'damage': round(best.get('pract', 0), 2),
        'cfg': {
            'guard_lv': cfg_d.get('guard_lv'),
            'spirit_lv': cfg_d.get('spirit_lv'),
            'core_src': cfg_d.get('core_src'),
            'core_skills': cfg_d.get('core_skills', {})
        },
        'timing': {
            'search_time': round(sch_t, 3),
            'total_time': round(t_used, 3),
            'attempts': attempts,
            'verified': verified
        },
        'armors': armor_list,
        'charm': charm_info,
        'weapon': weapon_info,
        'weapon_skill': weapon_skill_info,
        'series_check': series_check,
        'decorations': deco_counts,
        'core_skills': core_skills,
        'other_skills': other_skills,
        'remaining_slots': {
            'armor': list(best.get('rem_a', [])),
            'weapon': list(best.get('rem_w', []))
        }
    }


class SearchHandler(BaseHTTPRequestHandler):
    """HTTP请求处理器"""

    def log_message(self, format, *args):
        """抑制默认日志输出"""
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        # 禁用浏览器缓存，确保用户始终看到最新代码
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ('/', '/index.html'):
            if os.path.exists(INDEX_PATH):
                with open(INDEX_PATH, 'r', encoding='utf-8') as f:
                    html = f.read()
                self._send_html(html)
            else:
                self._send_html('<h1>index.html 未找到，请检查文件</h1>')
            return

        if path == '/api/info':
            skills_data = _load_skills_data()
            series_list = sorted([k for k in skills_data.get('系列技能', {}) if k != '说明'])
            combo_list = sorted([k for k in skills_data.get('组合技能', {}) if k != '说明'])
            info = {
                'skill_caps': fs.SKILL_CAPS,
                'plan_cfgs': [{'label': c[0], 'weapon_sk': c[1], 'extra_fixed': c[2]} for c in PLAN_CFGS],
                'base_fixed_min': BASE_FIXED_MIN,
                'series_names': series_list,
                'combo_skill_list': combo_list,
                'group_skills': list(fs.GROUP_SK) if fs.GROUP_SK else [],
                'weapon_skill_list': sorted(list(fs.WEAPON_SK)),
                'damage_skills': DAMAGE_SKILLS,
                'skills_data': skills_data,
                'skill_categories': SKILL_CATEGORIES,
                'deco_skill_map': {k: v for k, v in fs.deco_skill_map.items()},
            }
            self._send_json(info)
            return

        if path == '/api/skills':
            all_skills = {}
            for p in ['head', 'body', 'arms', 'waist', 'legs']:
                for a in fs.parts[p]:
                    for s, lv in a.get('skills', {}).items():
                        if s not in all_skills:
                            all_skills[s] = {'max_in_armor': 0, 'has_deco': False, 'cap': fs.SKILL_CAPS.get(s, 0)}
                        all_skills[s]['max_in_armor'] = max(all_skills[s]['max_in_armor'], lv)
            for c in fs.charm_pool:
                for s, lv in c.get('skills', {}).items():
                    if s not in all_skills:
                        all_skills[s] = {'max_in_armor': 0, 'has_deco': False, 'cap': fs.SKILL_CAPS.get(s, 0)}
            for dtype in ('weapon', 'armor'):
                for (sk, dt), _ in fs.deco_idx.items():
                    if dt == dtype and sk in all_skills:
                        all_skills[sk]['has_deco'] = True
            self._send_json({'skills': all_skills})
            return

        self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get('Content-Length', 0))
        req_body = self.rfile.read(content_len).decode('utf-8') if content_len > 0 else '{}'
        try:
            params = json.loads(req_body) if req_body else {}
        except json.JSONDecodeError:
            params = {}

        if path == '/api/search_plan':
            return self._handle_search_plan(params)
        if path == '/api/search_all':
            return self._handle_search_all(params)
        if path == '/api/query_extra':
            return self._handle_query_extra(params)
        if path == '/api/custom_search':
            return self._handle_custom_search(params)
        if path == '/api/detail_calc':
            return self._handle_detail_calc(params)
        if path == '/api/weapon_diy':
            return self._handle_weapon_diy(params)

        self._send_json({'error': 'Not found'}, 404)

    def _apply_weapon_slots(self, params):
        """Apply custom weapon slots from params, returns original WSLOTS for restoration"""
        orig_wslots = list(fs.WSLOTS)
        ws = params.get('weapon_slots')
        if ws and isinstance(ws, list) and len(ws) == 3:
            fs.WSLOTS = [max(1, min(3, int(v))) for v in ws]
        return orig_wslots

    def _handle_search_plan(self, params):
        plan_idx = params.get('plan_idx', 0)
        extra_fixed = params.get('extra_fixed', {})
        if plan_idx < 0 or plan_idx >= len(PLAN_CFGS):
            return self._send_json({'error': '无效的方案索引'}, 400)
        cfg = PLAN_CFGS[plan_idx]
        label, weapon_sk, base_extra = cfg
        merged_extra = dict(base_extra)
        merged_extra.update({k: int(v) for k, v in extra_fixed.items() if int(v) > 0})
        new_cfg = (label, weapon_sk, merged_extra)
        t0 = time.time()
        with _lock:
            best, sch_t, att, verc = search_for_plan(new_cfg)
        t_used = time.time() - t0
        result = _plan_result_to_dict(best, new_cfg, t_used, att, verc, sch_t)
        self._send_json({
            'label': label,
            'weapon_sk': weapon_sk,
            'extra_fixed': merged_extra,
            'result': result,
            'ok': result is not None
        })

    def _handle_search_all(self, params):
        extra_fixed_skill = params.get('extra_fixed_skill')
        extra_name = params.get('extra_name', '默认')
        t0 = time.time()
        with _lock:
            results = run_all_plans(extra_fixed_skill)
        t_used = time.time() - t0

        summary_rows = []
        total_dmg = 0.0
        for cfg in PLAN_CFGS:
            label = cfg[0]
            if label in results:
                best, t_plan, att, verc, sch_t = results[label]
                if best:
                    total_dmg += best.get('pract', 0)
                    sk = best['skills']
                    cfg_d = best.get('_cfg', {})
                    flex_skills = []
                    for fsk in ['超会心', '攻击守势', '攻击', '弱点特效', '看破', '逆袭', '无伤', '无我之境']:
                        lv = sk.get(fsk, 0)
                        if lv > 0:
                            base = 0
                            for x in [BASE_FIXED_MIN, cfg[1], cfg[2]]:
                                base = max(base, x.get(fsk, 0))
                            if lv > base:
                                cap = fs.SKILL_CAPS.get(fsk, 0)
                                flex_skills.append(f'{fsk}{lv}/{cap}')
                    flex_skills.append(f"精神{sk.get('精神抖擞', 0)}")
                    summary_rows.append({
                        'label': label,
                        'guard_lv': cfg_d.get('guard_lv'),
                        'damage': round(best.get('pract', 0), 1),
                        'wide': sk.get('广域化', 0) >= 5,
                        'heishi': sk.get('黑蚀龙之力', 0),
                        'bahar': sk.get('霸主之魂', 0),
                        'huolong': sk.get('火龙之力', 0),
                        'juji': sk.get('巨戟龙的默示录', 0),
                        'wuwo': sk.get('无我之境', 0),
                        'flex_skills': flex_skills,
                        'total_time': round(t_plan, 2)
                    })
                else:
                    summary_rows.append({
                        'label': label, 'damage': None, 'ok': False
                    })

        # 最佳方案详情
        best_label = max(results.keys(),
                         key=lambda k: results[k][0]['pract'] if results[k][0] else 0)
        best, best_t, best_att, best_verc, best_scht = results[best_label]
        best_cfg = next(c for c in PLAN_CFGS if c[0] == best_label)
        merged_extra_best = dict(best_cfg[2])
        merged_extra_best.update(extra_fixed_skill or {})
        merged_cfg_best = (best_cfg[0], best_cfg[1], merged_extra_best)
        best_detail = _plan_result_to_dict(best, merged_cfg_best, best_t, best_att, best_verc, best_scht) if best else None

        self._send_json({
            'extra_name': extra_name,
            'total_time': round(t_used, 2),
            'total_damage': round(total_dmg, 1),
            'summary': summary_rows,
            'best_label': best_label,
            'best_detail': best_detail
        })

    def _handle_query_extra(self, params):
        fixed_skills = params.get('fixed_skills', {})
        combo_skills = params.get('combo_skills', {})
        min_rem_armor = int(params.get('min_rem_armor', 0))
        min_rem_weapon = int(params.get('min_rem_weapon', 0))
        mode = params.get('mode', 'normal')
        fav_skills = set(params.get('favorite_skills', []))
        dis_skills = set(params.get('disabled_skills', []))
        fixed_skills = {k: int(v) for k, v in fixed_skills.items() if int(v) > 0}
        combo_skills = {k: int(v) for k, v in combo_skills.items() if int(v) > 0}
        # 武器技能状态（与查询方案一致），用于构建追加查询的武器实际技能：
        # 若用户未在武器配置区选择系列/组合技能（或已禁用为 __disabled__），
        # 前端会把它们置空传入，此处 weapon_series/weapon_combo 为空 => 武器不含技能 =>
        # 追加查询与查询方案保持同一假设，避免"查询无解但追加误报有空间"的矛盾。
        weapon_series = params.get('weapon_series_skill', '')
        weapon_combo = params.get('weapon_combo_skill', '')
        user_weapon_skills = {}
        if weapon_series and weapon_series in fs.NO_DECO_SK:
            user_weapon_skills[weapon_series] = 1
        if weapon_combo and weapon_combo in fs.NO_DECO_SK:
            user_weapon_skills[weapon_combo] = 1

        orig_wslots = self._apply_weapon_slots(params)
        t0 = time.time()

        # 流式 NDJSON 响应：逐条推送进度，避免反向代理超时
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Transfer-Encoding', 'chunked')  # 关键：启用分块传输，实现真正的流式响应
        self.send_header('X-Accel-Buffering', 'no')  # nginx 不缓冲
        self.end_headers()

        def _write_chunk(obj):
            chunk = (json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8')
            # HTTP chunked transfer encoding: size\r\n data \r\n
            self.wfile.write(f'{len(chunk):x}\r\n'.encode('ascii'))
            self.wfile.write(chunk)
            self.wfile.write(b'\r\n')
            self.wfile.flush()

        with _lock:
            try:
                for chunk in fs.query_extra_stream(
                    fixed_skills, combo_skills, min_rem_armor, fs.charm_pool,
                    mode=mode, fav_skills=fav_skills, dis_skills=dis_skills,
                    min_rem_weapon=min_rem_weapon, user_weapon_skills=user_weapon_skills
                ):
                    _write_chunk(chunk)
            except Exception as e:
                fs.WSLOTS = orig_wslots
                fs._FEASIBILITY_ONLY = False
                try:
                    _write_chunk({'type': 'error', 'error': f'查询出错: {e}'})
                except Exception:
                    pass
            finally:
                # 发送结束块（size 0）
                try:
                    self.wfile.write(b'0\r\n\r\n')
                    self.wfile.flush()
                except Exception:
                    pass
                fs.WSLOTS = orig_wslots
                fs._FEASIBILITY_ONLY = False

    def _handle_custom_search(self, params):
        fixed_skills = params.get('fixed_skills', {})
        combo_skills = params.get('combo_skills', {})
        min_rem_armor = int(params.get('min_rem_armor', 0))
        min_rem_weapon = int(params.get('min_rem_weapon', 0))
        max_results = int(params.get('max_results', 5))
        timeout_s = float(params.get('timeout_s', 15.0))
        auto_weapon = params.get('auto_weapon_skill', True)
        disabled_ws = set(params.get('disabled_weapon_skills', []))

        fixed_skills = {k: int(v) for k, v in fixed_skills.items() if int(v) > 0}
        combo_skills = {k: int(v) for k, v in combo_skills.items() if int(v) > 0}

        weapon_series = params.get('weapon_series_skill', '')
        weapon_combo = params.get('weapon_combo_skill', '')
        weapon_series_level = int(params.get('weapon_series_level', 0))
        weapon_combo_level = int(params.get('weapon_combo_level', 0))

        # 武器实际带的技能（用于显示和搜索）
        # 注意：武器洗练技能只提供1级
        weapon_actual_skills = {}
        if weapon_series and weapon_series in fs.NO_DECO_SK:
            weapon_actual_skills[weapon_series] = 1
        if weapon_combo and weapon_combo in fs.NO_DECO_SK:
            weapon_actual_skills[weapon_combo] = 1

        # 需求技能（用户在技能选择区选择的）
        # 固定武器技能≠技能需求：武器只是该技能的来源之一（提供1件），
        # 固定=预筛选武器（只带该技能的武器进入匹配池），不作为配装硬性需求，
        # 因此武器系列/组合技能【不】加入 requirement_skills 与 search_combo。
        requirement_skills = dict(combo_skills)
        search_combo = dict(combo_skills)

        orig_wslots = self._apply_weapon_slots(params)
        t0 = time.time()
        with _lock:
            try:
                # 统一搜索：武器作为第7个部位平权参与，不再有独立搜索路径
                raw_results = fs.dfs_search(
                    fs.charm_pool, fixed_skills, search_combo, min_rem_armor,
                    max_results=max_results, timeout_s=timeout_s, quiet=False,
                    min_rem_weapon=min_rem_weapon, user_weapon_skills=weapon_actual_skills
                )
            except Exception as e:
                fs.WSLOTS = orig_wslots
                self._send_json({'error': f'搜索出错: {e}'}, 500)
                return
        fs.WSLOTS = orig_wslots
        t_used = time.time() - t0
        results = []
        for r in raw_results[:max_results]:
            # 从方案 pieces 中提取实际使用的武器技能（part_idx=6）
            r_weapon_skills = dict(weapon_actual_skills)
            for p in r.get('pieces', []):
                if p.get('part_idx') == 6:
                    for sk, lv in p.get('skills', {}).items():
                        r_weapon_skills[sk] = lv
            fake_cfg = ('自定义搜索', r_weapon_skills, {})
            plan_result = _plan_result_to_dict(r, fake_cfg, t_used, 1, len(raw_results), t_used, requirement_skills=requirement_skills)
            results.append(plan_result)

        # 构造返回的武器技能信息（从结果中提取）
        auto_weapon_info = {}
        if results:
            top_weapon_skills = {k: v for k, v in results[0].get('weapon_skills', {}).items() 
                                 if k in fs.NO_DECO_SK}
            for sk, lv in top_weapon_skills.items():
                if sk in fs.SERIES_SK:
                    auto_weapon_info['series_skill'] = sk
                    auto_weapon_info['series_level'] = fs.SKILL_CAPS.get(sk, 2)
                elif sk in fs.GROUP_SK:
                    auto_weapon_info['group_skill'] = sk
                    auto_weapon_info['group_level'] = fs.SKILL_CAPS.get(sk, 3)

        self._send_json({
            'count': len(results),
            'total_time': round(t_used, 2),
            'fixed_skills': fixed_skills,
            'combo_skills': search_combo,
            'auto_weapon': auto_weapon_info if auto_weapon_info else None,
            'results': results
        })

    def _handle_detail_calc(self, params):
        """Calculate detailed damage breakdown for given skills without re-searching"""
        fixed_skills = params.get('fixed_skills', {})
        combo_skills = params.get('combo_skills', {})
        fixed_skills = {k: int(v) for k, v in fixed_skills.items() if int(v) > 0}
        combo_skills = {k: int(v) for k, v in combo_skills.items() if int(v) > 0}

        weapon_params = params.get('weapon', {})
        w_atk = int(weapon_params.get('base_attack', fs.W_ATK))
        w_crt = int(weapon_params.get('crit', fs.W_CRT))
        w_ele = int(weapon_params.get('element', fs.W_ELE))
        perm_atk = int(weapon_params.get('perm_atk', fs.PERM_ATK))
        weapon_type = weapon_params.get('weapon_type', '大剑')
        augmentation = weapon_params.get('augmentation', 'none')
        affixes = weapon_params.get('affixes', [])

        base_atk = w_atk
        base_crt = w_crt
        base_ele = w_ele
        base_sharp = 100

        if augmentation != 'none':
            if weapon_type in ('笛子', '铳枪'):
                if augmentation == 'attack':
                    base_atk += 3
                elif augmentation == 'critical':
                    base_crt += 2
                elif augmentation == 'element':
                    base_ele += 8
            else:
                if augmentation == 'attack':
                    base_atk += 10
                    base_crt -= 15
                elif augmentation == 'critical':
                    base_crt += 10
                    base_atk -= 10
                    base_sharp -= 10
                    base_ele = 0
                elif augmentation == 'element':
                    base_crt -= 5
                    ele_map = {'大剑':5,'太刀':5,'片手':4,'双刀':3,'大锤':4,'长枪':5,'斩斧':4,'盾斧':5,'虫棍':4,'弓':3}
                    base_ele += ele_map.get(weapon_type, 0)

        affix_type_map = {'attack': '攻击', 'critical': '会心', 'sharpness': '锋利度', 'element': '属性'}
        affix_skill_map = {'攻击': {}, '会心': {}, '锋利度': {}, '属性': {}}
        affix_counts = {}
        for af in affixes:
            af_type = affix_type_map.get(af.get('type', ''), '')
            af_level = int(af.get('level', 1))
            if not af_type:
                continue
            key = af_type + '_' + str(af_level)
            if affix_counts.get(key, 0) >= 2:
                continue
            affix_counts[key] = affix_counts.get(key, 0) + 1
            if af_type == '攻击':
                vals = {1: 5, 2: 6, 3: 9, 4: 12}
                base_atk += vals.get(af_level, 0)
            elif af_type == '会心':
                vals = {1: 5, 2: 6, 3: 8, 4: 10}
                base_crt += vals.get(af_level, 0)
            elif af_type == '锋利度':
                vals = {1: 30, 2: 50}
                base_sharp += vals.get(af_level, 0)
            elif af_type == '属性':
                vals = {1: 30, 2: 50, 3: 80}
                base_ele += vals.get(af_level, 0)

        # 合并所有技能
        all_skills = dict(fixed_skills)
        all_skills.update(combo_skills)

        orig = (fs.W_ATK, fs.W_CRT, fs.W_ELE, fs.PERM_ATK)
        try:
            with _lock:
                fs.W_ATK = max(base_atk, 1)
                fs.W_CRT = max(base_crt, -100)
                fs.W_ELE = max(base_ele, 0)
                fs.PERM_ATK = perm_atk
                fs._calc_damage_cached.cache_clear()
                final_dmg, final_detail = fs.calc_damage_detail(all_skills)
                final_wcr = fs.calc_weighted_crit(all_skills)
                baseline_dmg, baseline_detail = fs.calc_damage_detail(fixed_skills)
                baseline_wcr = fs.calc_weighted_crit(fixed_skills)
            dmg_increase = final_dmg - baseline_dmg
            pct = (final_dmg / max(baseline_dmg, 1) - 1) * 100
            skill_damages = {}
            for sk, lv in all_skills.items():
                if lv <= 0:
                    continue
                reduced = dict(all_skills)
                del reduced[sk]
                reduced_dmg, _ = fs.calc_damage_detail(reduced)
                delta = final_dmg - reduced_dmg
                skill_damages[sk] = {
                    'level': lv,
                    'cap': fs.SKILL_CAPS.get(sk, lv),
                    'standalone_dmg': round(delta, 1),
                }
            aug_label = {'attack': '攻击激化', 'critical': '会心激化', 'element': '属性激化', 'none': '无'}.get(augmentation, '无')
            detail_text = (
                f'{weapon_type} | 激化: {aug_label} | '
                f'攻击:{base_atk} 会心:{base_crt}% 属性:{base_ele} 锋利度:{base_sharp}'
            )
            self._send_json({
                'baseline_dmg': round(baseline_dmg, 1),
                'baseline_wcr': round(baseline_wcr, 1),
                'final_dmg': round(final_dmg, 1),
                'final_wcr': round(final_wcr, 1),
                'dmg_increase': round(dmg_increase, 1),
                'dmg_increase_pct': round(pct, 1),
                'skill_damages': skill_damages,
                'detail_text': detail_text,
                'weapon_stats': {'atk': base_atk, 'crt': base_crt, 'ele': base_ele, 'sharp': base_sharp},
                'baseline_detail': baseline_detail,
                'final_detail': final_detail,
                'time': 0.1,
            })
        except Exception as e:
            self._send_json({'error': f'自定义武器计算出错: {e}'}, 500)
        finally:
            with _lock:
                fs.W_ATK, fs.W_CRT, fs.W_ELE, fs.PERM_ATK = orig
                try:
                    fs._calc_damage_cached.cache_clear()
                except Exception:
                    pass


def main():
    port = 8766
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    server = ThreadingHTTPServer(('127.0.0.1', port), SearchHandler)
    print(f'MHWilds 配装搜索 GUI 已启动: http://localhost:{port}')
    print(f'按 Ctrl+C 停止服务器')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务器已停止')
        server.server_close()


if __name__ == '__main__':
    main()