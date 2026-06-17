"""Vercel serverless function: Dashboard QR BBVA en tiempo real."""
import os, json, pymysql
from http.server import BaseHTTPRequestHandler
from datetime import datetime, date

# --- Config ---
DB = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
    'connect_timeout': 15,
    'read_timeout': 30,
}
START = '2026-06-10 14:10:00'
W_START = '2026-06-10'
W_END = '2026-06-19'

# --- Load cohort IDs ---
_dir = os.path.dirname(__file__)
with open(os.path.join(_dir, 'cohort_ids.json')) as f:
    GROUPS = json.load(f)
ALL_IDS = list(set(i for v in GROUPS.values() for i in v))

# --- DB queries ---
def get_data():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    ph = ','.join(['%s'] * len(ALL_IDS))
    d = {}

    # Monto total cuotas
    cur.execute(f"SELECT ROUND(SUM(c.monto),2) FROM cronograma c WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo'", ALL_IDS)
    d['monto_total'] = float(cur.fetchone()[0] or 0)

    # Cuota montos por solicitud
    cur.execute(f"SELECT c.solicitud_id, ROUND(SUM(c.monto),2) FROM cronograma c WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo' GROUP BY c.solicitud_id", ALL_IDS)
    cuota_montos = {int(r[0]): float(r[1]) for r in cur.fetchall()}

    # Pre-QR payers
    cur.execute(f"SELECT DISTINCT c.solicitud_id FROM pago p JOIN cronograma c ON c.id=p.cronograma_id WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo' AND p.deleted_at IS NULL AND p.fecha_pago < %s", ALL_IDS + [START])
    pre_qr = set(int(r[0]) for r in cur.fetchall())

    # Post-QR payers + pasarela
    cur.execute(f"SELECT c.solicitud_id, p.pasarela_pago FROM pago p JOIN cronograma c ON c.id=p.cronograma_id WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo' AND p.deleted_at IS NULL AND p.fecha_pago >= %s", ALL_IDS + [START])
    sol_pasarela = {}
    paid_set = set()
    for r in cur.fetchall():
        sid = int(r[0])
        paid_set.add(sid)
        sol_pasarela[sid] = r[1]

    # QR paid
    cur.execute(f"SELECT DISTINCT q.solicitud_id FROM bbva_qr_payments q WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s AND q.status='paid'", ALL_IDS + [START])
    qr_paid = set(int(r[0]) for r in cur.fetchall())

    # QR generated
    cur.execute(f"SELECT DISTINCT q.solicitud_id FROM bbva_qr_payments q WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s", ALL_IDS + [START])
    qr_gen = set(int(r[0]) for r in cur.fetchall())

    # Monto cobrado
    cur.execute(f"SELECT ROUND(SUM(p.monto_pago_total),2) FROM pago p JOIN cronograma c ON c.id=p.cronograma_id WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo' AND p.deleted_at IS NULL AND p.fecha_pago >= %s", ALL_IDS + [START])
    d['monto_cobrado'] = float(cur.fetchone()[0] or 0)

    # --- Segmentacion por monto ---
    def rango(m):
        if m <= 50: return '0-50'
        elif m <= 150: return '51-150'
        elif m <= 250: return '151-250'
        return '251+'

    ranges_order = ['0-50', '51-150', '151-250', '251+']
    R = {r: {'base': 0, 'pagaron': 0, 'qr_paid': 0, 'qr_gen': 0, 'monnet': 0, 'bbva_qr': 0, 'mp': 0, 'recaudo': 0} for r in ranges_order}
    for sid, m in cuota_montos.items():
        if sid in pre_qr: continue
        r = rango(m)
        R[r]['base'] += 1
        if sid in paid_set:
            R[r]['pagaron'] += 1
            pas = sol_pasarela.get(sid, '')
            if 'MONNET' in pas: R[r]['monnet'] += 1
            elif 'QR' in pas: R[r]['bbva_qr'] += 1
            elif 'MERCADO' in pas: R[r]['mp'] += 1
            elif 'RECAUDO' in pas or 'BK' in pas: R[r]['recaudo'] += 1
        if sid in qr_paid: R[r]['qr_paid'] += 1
        if sid in qr_gen: R[r]['qr_gen'] += 1
    d['por_monto'] = R
    d['ranges_order'] = ranges_order

    base_total = sum(v['base'] for v in R.values())
    pagos_total = sum(v['pagaron'] for v in R.values())
    d['base_efectiva'] = base_total
    d['total_cuotas'] = len(cuota_montos)
    d['pre_qr'] = len(pre_qr)
    d['total_pagos'] = pagos_total
    d['cuota_promedio'] = round(d['monto_total'] / len(cuota_montos), 2) if cuota_montos else 0

    # --- Evolucion diaria ---
    cur.execute(f"""SELECT DATE(p.fecha_pago) AS dia, p.pasarela_pago, COUNT(DISTINCT c.solicitud_id)
        FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}'
        AND c.estado='activo' AND p.deleted_at IS NULL AND p.fecha_pago >= %s
        GROUP BY dia, p.pasarela_pago ORDER BY dia""", ALL_IDS + [START])
    daily_raw = {}
    for r in cur.fetchall():
        dia = str(r[0])
        if dia not in daily_raw: daily_raw[dia] = {'monnet': 0, 'bbva_qr': 0, 'mp': 0, 'otros': 0}
        pas = r[1]
        if 'MONNET' in pas: daily_raw[dia]['monnet'] += r[2]
        elif 'QR' in pas: daily_raw[dia]['bbva_qr'] += r[2]
        elif 'MERCADO' in pas: daily_raw[dia]['mp'] += r[2]
        else: daily_raw[dia]['otros'] += r[2]
    d['diario'] = daily_raw

    # --- Por grupo ---
    d['grupos'] = {}
    for gname, gids in GROUPS.items():
        gph = ','.join(['%s'] * len(gids))
        cur.execute(f"SELECT COUNT(DISTINCT c.solicitud_id) FROM cronograma c WHERE c.solicitud_id IN ({gph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo'", gids)
        total = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(DISTINCT c.solicitud_id) FROM pago p JOIN cronograma c ON c.id=p.cronograma_id WHERE c.solicitud_id IN ({gph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo' AND p.deleted_at IS NULL AND p.fecha_pago < %s", gids + [START])
        gpre = cur.fetchone()[0]
        cur.execute(f"SELECT p.pasarela_pago, COUNT(DISTINCT c.solicitud_id) FROM pago p JOIN cronograma c ON c.id=p.cronograma_id WHERE c.solicitud_id IN ({gph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}' AND c.estado='activo' AND p.deleted_at IS NULL AND p.fecha_pago >= %s GROUP BY p.pasarela_pago", gids + [START])
        gpas = {}
        for r in cur.fetchall():
            p = r[1]
            if 'MONNET' in r[0]: gpas['monnet'] = gpas.get('monnet', 0) + p
            elif 'QR' in r[0]: gpas['bbva_qr'] = gpas.get('bbva_qr', 0) + p
            elif 'MERCADO' in r[0]: gpas['mp'] = gpas.get('mp', 0) + p
            else: gpas['otros'] = gpas.get('otros', 0) + p
        cur.execute(f"SELECT COUNT(DISTINCT q.solicitud_id) FROM bbva_qr_payments q WHERE q.solicitud_id IN ({gph}) AND q.created_at >= %s AND q.status='paid'", gids + [START])
        gqr_paid = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(DISTINCT q.solicitud_id) FROM bbva_qr_payments q WHERE q.solicitud_id IN ({gph}) AND q.created_at >= %s", gids + [START])
        gqr_gen = cur.fetchone()[0]
        gpaid = sum(gpas.values())
        d['grupos'][gname] = {'total': total, 'pre_qr': gpre, 'base': total - gpre, 'pagaron': gpaid, 'qr_paid': gqr_paid, 'qr_gen': gqr_gen, 'pasarelas': gpas}

    # --- QR status ---
    cur.execute(f"SELECT q.status, COUNT(*) FROM bbva_qr_payments q WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s GROUP BY q.status", ALL_IDS + [START])
    d['qr_status'] = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute(f"SELECT COUNT(*) FROM bbva_qr_payments q WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s", ALL_IDS + [START])
    d['qr_total'] = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(DISTINCT q.solicitud_id) FROM bbva_qr_payments q WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s", ALL_IDS + [START])
    d['qr_clientes'] = cur.fetchone()[0]

    # QR por cliente
    cur.execute(f"SELECT cnt, COUNT(*) FROM (SELECT q.solicitud_id, COUNT(*) AS cnt FROM bbva_qr_payments q WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s GROUP BY q.solicitud_id) t GROUP BY cnt ORDER BY cnt", ALL_IDS + [START])
    d['qr_por_cliente'] = [(r[0], r[1]) for r in cur.fetchall()]

    # --- Banco Monnet ---
    cur.execute(f"""SELECT b.nombre, COUNT(*) FROM monnet_pago_request m
        JOIN evento_zonaclientes e ON e.id=m.evento_id JOIN banco b ON b.id=e.banco_id
        WHERE m.solicitud_id IN ({ph}) AND m.status IN ('5','9') AND m.created_at >= %s
        GROUP BY b.nombre ORDER BY 2 DESC""", ALL_IDS + [START])
    d['bancos_monnet'] = [(r[0], r[1]) for r in cur.fetchall()]

    cur.execute(f"""SELECT CASE WHEN cr.monto<=50 THEN '0-50' WHEN cr.monto<=150 THEN '51-150' WHEN cr.monto<=250 THEN '151-250' ELSE '251+' END AS rango,
        b.nombre, COUNT(*) FROM monnet_pago_request m
        JOIN evento_zonaclientes e ON e.id=m.evento_id JOIN banco b ON b.id=e.banco_id
        JOIN cronograma cr ON cr.solicitud_id=m.solicitud_id AND cr.estado='activo' AND cr.fecha_vencimiento >= '{W_START}' AND cr.fecha_vencimiento < '{W_END}'
        WHERE m.solicitud_id IN ({ph}) AND m.status IN ('5','9') AND m.created_at >= %s
        GROUP BY rango, b.nombre ORDER BY rango, 3 DESC""", ALL_IDS + [START])
    d['bancos_monnet_rango'] = [(r[0], r[1], r[2]) for r in cur.fetchall()]

    # MP metodo
    cur.execute(f"""SELECT CASE JSON_UNQUOTE(JSON_EXTRACT(p.mercado_pago_payment_json, '$.issuer_id'))
        WHEN '12512' THEN 'BCP' WHEN '12446' THEN 'Interbank' WHEN '12354' THEN 'BBVA' WHEN '12551' THEN 'Scotiabank'
        ELSE CONCAT('Otro (', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.mercado_pago_payment_json, '$.payment_method_id')),'?'), ')')
        END AS banco, COUNT(*) FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.solicitud_id IN ({ph}) AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}'
        AND c.estado='activo' AND p.deleted_at IS NULL AND p.mercado_pago_payment_json IS NOT NULL AND p.fecha_pago >= %s
        GROUP BY banco ORDER BY 2 DESC""", ALL_IDS + [START])
    d['mp_metodo'] = [(r[0], r[1]) for r in cur.fetchall()]

    # --- Trazabilidad ---
    cur.execute(f"""SELECT q.solicitud_id, q.created_at, q.status, q.amount,
        p.pasarela_pago, p.fecha_pago, p.monto_pago_total, TIMESTAMPDIFF(SECOND, q.created_at, p.fecha_pago) AS seg
        FROM bbva_qr_payments q LEFT JOIN cronograma c ON c.solicitud_id=q.solicitud_id AND c.estado='activo'
        AND c.fecha_vencimiento >= '{W_START}' AND c.fecha_vencimiento < '{W_END}'
        LEFT JOIN pago p ON p.cronograma_id=c.id AND p.deleted_at IS NULL AND p.fecha_pago >= %s
        WHERE q.solicitud_id IN ({ph}) AND q.created_at >= %s AND q.status IN ('pending','cancelled')
        ORDER BY seg ASC""", [START] + ALL_IDS + [START])
    seen = set()
    migrated = []
    no_pago = 0
    for r in cur.fetchall():
        sid = int(r[0])
        if sid in seen: continue
        seen.add(sid)
        if r[4]:
            migrated.append({'sol': sid, 'seg': r[7], 'pas': r[4], 'qr_at': str(r[1]), 'p_at': str(r[5]), 'qm': float(r[3]), 'pm': float(r[6])})
        else:
            no_pago += 1

    bk = {'<1min': 0, '1-10min': 0, '10min-1h': 0, '>1h': 0}
    for m in migrated:
        s = m['seg']
        if s is None: continue
        if s < 60: bk['<1min'] += 1
        elif s < 600: bk['1-10min'] += 1
        elif s < 3600: bk['10min-1h'] += 1
        else: bk['>1h'] += 1
    d['migraron'] = len(migrated)
    d['sin_pago'] = no_pago
    d['tiempos_migracion'] = bk
    d['top_rapidas'] = sorted([m for m in migrated if m['seg'] and m['seg'] > 0], key=lambda x: x['seg'])[:10]

    # Migraciones por pasarela
    migr_pas = {}
    for m in migrated:
        p = m['pas']
        migr_pas[p] = migr_pas.get(p, 0) + 1
    d['migr_pasarela'] = migr_pas

    conn.close()
    return d

# --- HTML helpers ---
def pct(n, total, decimals=1):
    if not total: return '-'
    return f"{n/total*100:.{decimals}f}%"

def fmt(n):
    if isinstance(n, float): return f"{n:,.2f}"
    return f"{n:,}"

def td(v, cls='', style=''):
    s = f' class="{cls}"' if cls else ''
    st = f' style="{style}"' if style else ''
    return f'<td{s}{st}>{v}</td>'

def tdc(v, bold=False, color=''):
    cls = 'text-center'
    if bold: cls += ' bold'
    style = f'color:{color};' if color else ''
    return td(v, cls, style)

# --- HTML generation ---
def render(d):
    today = datetime.now().strftime('%d/%m/%Y')
    today_short = datetime.now().strftime('%d/%m/%Y')

    # Compute aggregates
    total_qr_paid = sum(v['qr_paid'] for v in d['por_monto'].values())
    total_qr_gen = sum(v['qr_gen'] for v in d['por_monto'].values())
    total_pagos = d['total_pagos']
    base = d['base_efectiva']

    # Pasarela totals
    pas_monnet = sum(v['monnet'] for v in d['por_monto'].values())
    pas_qr = sum(v['bbva_qr'] for v in d['por_monto'].values())
    pas_mp = sum(v['mp'] for v in d['por_monto'].values())
    pas_rec = sum(v['recaudo'] for v in d['por_monto'].values())

    # QR interaction stats
    total_qr_interacted = total_qr_paid + d['migraron']
    migr_total = d['migraron']

    # Direct (no QR)
    direct_monnet = pas_monnet - d['migr_pasarela'].get('MONNET', 0)
    direct_mp = pas_mp - d['migr_pasarela'].get('MERCADO PAGO', 0)
    direct_rec = pas_rec - d['migr_pasarela'].get('BBVA RECAUDO', 0) - d['migr_pasarela'].get('BBVA BK', 0)

    # Bar widths
    pct_qr = round(total_qr_paid / total_pagos * 100, 1) if total_pagos else 0
    pct_migr = round(migr_total / total_pagos * 100, 1) if total_pagos else 0
    pct_monnet = round(direct_monnet / total_pagos * 100, 1) if total_pagos else 0
    pct_otros = round(100 - pct_qr - pct_migr - pct_monnet, 1)

    # Daily table
    daily_html = ''
    acum = 0
    for dia in sorted(d['diario'].keys()):
        dd = d['diario'][dia]
        total_dia = dd['monnet'] + dd['bbva_qr'] + dd['mp'] + dd['otros']
        acum += total_dia
        pct_qr_dia = pct(dd['bbva_qr'], total_dia)
        dia_fmt = dia[5:]  # MM-DD
        nota = '*' if dia == '2026-06-10' else ''
        bg = ' style="background:#fff8e1;"' if dia == datetime.now().strftime('%Y-%m-%d') else ''
        daily_html += f'''<tr{bg}>
            <td class="bold">{dia_fmt.replace('-','/')}{nota}</td>
            <td class="text-center">{dd['monnet']}</td>
            <td class="text-center bold" style="color:#28a745;">{dd['bbva_qr']}</td>
            <td class="text-center">{dd['mp']}</td>
            <td class="text-center">{dd['otros']}</td>
            <td class="text-center bold">{total_dia}</td>
            <td class="text-center bold" style="color:#28a745;">{pct_qr_dia}</td>
            <td class="text-center">{acum}</td>
        </tr>'''

    # Group table
    grp_order = ['Control', 'A', 'B', 'C']
    grp_colors = {'Control': '#495057', 'A': '#1565c0', 'B': '#c62828', 'C': '#7b1fa2'}
    grp_names = {'Control': 'Control', 'A': 'Grupo A', 'B': 'Grupo B', 'C': 'Grupo C'}
    grp_html = ''
    grp_pas_html = ''
    tot_base = tot_pag = tot_qrp = tot_qrg = 0
    for g in grp_order:
        gd = d['grupos'][g]
        color = grp_colors[g]
        pag = gd['pagaron']
        b = gd['base']
        qrp = gd['qr_paid']
        qrg = gd['qr_gen']
        tot_base += b; tot_pag += pag; tot_qrp += qrp; tot_qrg += qrg
        grp_html += f'''<tr>
            <td class="bold" style="color:{color};">{grp_names[g]}</td>
            {tdc(b)}{tdc(pag, True)}{tdc(pct(pag, b))}
            {tdc(qrp, True, '#28a745')}{tdc(pct(qrp, pag), True, '#28a745')}
            {tdc(qrg)}{tdc(pct(qrp, qrg), True)}
        </tr>'''
        # Pasarela row
        mn = gd['pasarelas'].get('monnet', 0)
        qr = gd['pasarelas'].get('bbva_qr', 0)
        mp = gd['pasarelas'].get('mp', 0)
        ot = gd['pasarelas'].get('otros', 0)
        grp_pas_html += f'''<tr>
            <td class="bold" style="color:{color};">{grp_names[g]}</td>
            <td class="text-center">{mn} ({pct(mn, pag)})</td>
            <td class="text-center bold" style="color:#28a745;">{qr} ({pct(qr, pag)})</td>
            <td class="text-center">{mp} ({pct(mp, pag)})</td>
            <td class="text-center">{ot} ({pct(ot, pag)})</td>
            <td class="text-center bold">{pag}</td>
        </tr>'''

    # Monto range table
    rng_html = ''
    rng_pas_html = ''
    for r in d['ranges_order']:
        rd = d['por_monto'][r]
        rng_html += f'''<tr>
            <td class="bold">S/ {r}</td>
            {tdc(rd['base'])}{tdc(rd['pagaron'])}{tdc(pct(rd['pagaron'], rd['base']))}
            {tdc(rd['qr_paid'], True, '#28a745')}{tdc(pct(rd['qr_paid'], rd['pagaron']), True, '#28a745')}
            {tdc(rd['qr_gen'])}{tdc(pct(rd['qr_paid'], rd['qr_gen']), True, '#28a745')}
        </tr>'''
        rt = rd['pagaron']
        rng_pas_html += f'''<tr>
            <td class="bold">S/ {r}</td>
            <td class="text-center">{rd['monnet']} ({pct(rd['monnet'], rt)})</td>
            <td class="text-center bold" style="color:#28a745;">{rd['bbva_qr']} ({pct(rd['bbva_qr'], rt)})</td>
            <td class="text-center">{rd['mp']} ({pct(rd['mp'], rt)})</td>
            <td class="text-center">{rd['recaudo']} ({pct(rd['recaudo'], rt)})</td>
            <td class="text-center bold">{rt}</td>
        </tr>'''

    # Banco Monnet table
    monnet_total = sum(b[1] for b in d['bancos_monnet'])
    banco_html = ''
    banco_colors = {'Yape': '#6f21a8', 'BCP': '#004481', 'Interbank': '#28a745', 'BBVA': '#0066b3'}
    for b_name, b_cnt in d['bancos_monnet']:
        p = round(b_cnt / monnet_total * 100, 1) if monnet_total else 0
        clr = banco_colors.get(b_name, '#666')
        banco_html += f'''<tr>
            <td class="bold" style="color:{clr};">{b_name}</td>
            <td class="text-center bold">{b_cnt}</td>
            <td class="text-center">{p}%</td>
            <td><div style="width:{p}%; height:18px; background:{clr}; border-radius:4px; min-width:8px;"></div></td>
        </tr>'''

    # Banco Monnet por rango
    banco_rng = {}
    for rng, bname, cnt in d['bancos_monnet_rango']:
        if rng not in banco_rng: banco_rng[rng] = {}
        banco_rng[rng][bname] = cnt
    banco_rng_html = ''
    banco_names = [b[0] for b in d['bancos_monnet'][:4]] if d['bancos_monnet'] else ['Yape', 'BCP', 'Interbank', 'BBVA']
    for r in d['ranges_order']:
        cells = ''
        for bn in banco_names:
            v = banco_rng.get(r, {}).get(bn, 0)
            bold = ' bold' if v == max(banco_rng.get(r, {}).values(), default=0) and v > 0 else ''
            cells += f'<td class="text-center{bold}">{v if v else "-"}</td>'
        banco_rng_html += f'<tr><td class="bold">S/ {r}</td>{cells}</tr>'

    # MP metodo table
    mp_total = sum(m[1] for m in d['mp_metodo'])
    mp_html = ''
    mp_name_map = {'Otro (pagoefectivo_atm)': 'PagoEfectivo (ATM/agente)', 'Otro (yape)': 'Yape', 'Otro (debvisa)': 'Debito Visa', 'Otro (debmaster)': 'Debito Mastercard'}
    for m_name, m_cnt in d['mp_metodo']:
        display = mp_name_map.get(m_name, m_name)
        p = round(m_cnt / mp_total * 100, 1) if mp_total else 0
        mp_html += f'''<tr>
            <td class="bold">{display}</td>
            <td class="text-center bold">{m_cnt}</td>
            <td class="text-center">{p}%</td>
        </tr>'''

    # QR status table
    qs = d['qr_status']
    qr_t = d['qr_total']
    qr_status_html = ''
    for s, badge in [('paid', 'status-paid'), ('pending', 'status-pending'), ('cancelled', 'status-cancelled'), ('expired', 'status-expired')]:
        v = qs.get(s, 0)
        if v == 0 and s == 'expired': continue
        qr_status_html += f'''<tr>
            <td><span class="status-badge {badge}">{s.capitalize()}</span></td>
            <td class="text-center bold">{v}</td>
            <td class="text-center">{pct(v, qr_t)}</td>
        </tr>'''

    # QR por cliente
    qr_cli_html = ''
    for cnt, clients in d['qr_por_cliente']:
        qr_cli_html += f'''<tr>
            <td class="bold">{cnt} QR{"s" if cnt > 1 else ""}</td>
            <td class="text-center bold">{clients}</td>
            <td class="text-center">{pct(clients, d['qr_clientes'])}</td>
        </tr>'''

    # Trazabilidad tiempos
    traz_html = ''
    tbk = d['tiempos_migracion']
    for label, key in [('Menos de 1 minuto', '<1min'), ('1 a 10 minutos', '1-10min'), ('10 min a 1 hora', '10min-1h'), ('Mas de 1 hora', '>1h')]:
        v = tbk[key]
        traz_html += f'''<tr>
            <td>{label}</td>
            <td class="text-center bold">{v}</td>
            <td class="text-center">{pct(v, d['migraron'])}</td>
        </tr>'''

    # Top rapidas
    top_html = ''
    for m in d['top_rapidas']:
        seg = m['seg']
        if seg < 60: t_fmt = f"{seg} seg"
        else: t_fmt = f"{seg/60:.1f} min"
        qr_dt = m['qr_at'][5:16].replace('-', '/')
        p_dt = m['p_at'][5:16].replace('-', '/')
        top_html += f'''<tr>
            <td class="bold">{m['sol']}</td>
            <td>{qr_dt}</td><td>{p_dt}</td>
            <td class="text-center bold" style="color:#e8590c;">{t_fmt}</td>
            <td class="text-right">S/ {int(m['qm'])}</td>
            <td class="text-right">S/ {int(m['pm'])}</td>
            <td>{m['pas']}</td>
        </tr>'''

    # Timeline status
    def ts(fecha_str):
        """Return status badge based on today vs date."""
        hoy = date.today()
        if isinstance(fecha_str, str):
            parts = fecha_str.split('/')
            if len(parts) == 2:
                d_m, d_d = int(parts[0]), 6  # hack for month
        return ''

    hoy = date.today()
    def badge_for(month, day):
        d_date = date(2026, month, day)
        if d_date < hoy: return 'status-expired', 'Vencido', ''
        elif d_date == hoy: return 'status-pending', 'Hoy', ' style="background:#fff8e1;"'
        return 'status-active', 'Pendiente', ''

    b10 = badge_for(6, 10)
    b11 = badge_for(6, 11)
    b14 = badge_for(6, 14)
    b15 = badge_for(6, 15)
    b17 = badge_for(6, 17)
    b18 = badge_for(6, 18)

    # Migr pasarela description
    migr_desc_parts = []
    for p, c in sorted(d['migr_pasarela'].items(), key=lambda x: -x[1]):
        migr_desc_parts.append(f"{p} ({c})")
    migr_desc = ', '.join(migr_desc_parts)

    # --- FULL HTML ---
    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reporte QR BBVA - Live</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #f0f2f5; color: #1a1a2e; padding: 40px 20px; }}
        .container {{ max-width: 1100px; margin: 0 auto; }}
        .header {{ background: linear-gradient(135deg, #004481, #0066b3); color: white; padding: 40px; border-radius: 16px; margin-bottom: 30px; box-shadow: 0 4px 20px rgba(0,68,129,0.3); }}
        .header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
        .header p {{ font-size: 15px; opacity: 0.85; }}
        .header .meta {{ margin-top: 16px; display: flex; gap: 24px; font-size: 13px; opacity: 0.75; flex-wrap: wrap; }}
        .live-badge {{ background: #e8590c; color: white; font-size: 11px; padding: 3px 10px; border-radius: 10px; font-weight: 700; letter-spacing: 0.5px; display: inline-flex; align-items: center; gap: 6px; }}
        .live-dot {{ width: 8px; height: 8px; background: white; border-radius: 50%; animation: pulse 1.5s infinite; }}
        @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}
        .phase-banner {{ background: linear-gradient(135deg, #1b5e20, #2e7d32); color: white; padding: 20px 32px; border-radius: 12px; margin-bottom: 30px; display: flex; align-items: center; gap: 20px; box-shadow: 0 2px 12px rgba(27,94,32,0.2); }}
        .phase-banner .phase-icon {{ width: 56px; height: 56px; background: rgba(255,255,255,0.15); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: 700; flex-shrink: 0; }}
        .phase-banner .phase-text h3 {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; }}
        .phase-banner .phase-text p {{ font-size: 13px; opacity: 0.85; }}
        .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .kpi-card {{ background: white; border-radius: 12px; padding: 28px 24px; text-align: center; box-shadow: 0 2px 12px rgba(0,0,0,0.06); position: relative; overflow: hidden; }}
        .kpi-card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; }}
        .kpi-card:nth-child(1)::before {{ background: #004481; }}
        .kpi-card:nth-child(2)::before {{ background: #0066b3; }}
        .kpi-card:nth-child(3)::before {{ background: #28a745; }}
        .kpi-card:nth-child(4)::before {{ background: #e8590c; }}
        .kpi-label {{ font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }}
        .kpi-value {{ font-size: 36px; font-weight: 700; }}
        .kpi-card:nth-child(1) .kpi-value {{ color: #004481; }}
        .kpi-card:nth-child(2) .kpi-value {{ color: #0066b3; }}
        .kpi-card:nth-child(3) .kpi-value {{ color: #28a745; }}
        .kpi-card:nth-child(4) .kpi-value {{ color: #e8590c; }}
        .kpi-sub {{ font-size: 13px; color: #888; margin-top: 6px; }}
        .section {{ background: white; border-radius: 12px; padding: 32px; margin-bottom: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
        .section h2 {{ font-size: 18px; font-weight: 600; margin-bottom: 20px; color: #004481; display: flex; align-items: center; gap: 10px; }}
        .section h2 .num {{ background: #004481; color: white; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        th {{ text-align: left; padding: 12px 16px; background: #f8f9fa; color: #555; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e9ecef; }}
        td {{ padding: 12px 16px; border-bottom: 1px solid #f0f0f0; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background: #f8f9fb; }}
        .text-right {{ text-align: right; }}
        .text-center {{ text-align: center; }}
        .bold {{ font-weight: 600; }}
        .bar-container {{ display: flex; align-items: center; gap: 12px; }}
        .bar-bg {{ flex: 1; height: 28px; background: #f0f2f5; border-radius: 6px; overflow: hidden; position: relative; }}
        .detail-table {{ margin-top: 16px; }}
        .detail-table th {{ font-size: 11px; }}
        .detail-table td {{ font-size: 13px; padding: 10px 16px; }}
        .status-badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
        .status-paid {{ background: #d4edda; color: #155724; }}
        .status-expired {{ background: #fde2e2; color: #9b1c1c; }}
        .status-pending {{ background: #fff3cd; color: #856404; }}
        .status-cancelled {{ background: #f8d7da; color: #721c24; }}
        .status-active {{ background: #cce5ff; color: #004085; }}
        .note {{ background: #fff8e1; border-left: 4px solid #ffc107; padding: 16px 20px; border-radius: 0 8px 8px 0; font-size: 13px; color: #665200; margin-top: 20px; line-height: 1.6; }}
        .group-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
        .group-card {{ border-radius: 10px; padding: 20px; text-align: center; }}
        .group-card.control {{ background: #f8f9fa; border: 2px solid #dee2e6; }}
        .group-card.group-a {{ background: #e3f2fd; border: 2px solid #90caf9; }}
        .group-card.group-b {{ background: #fce4ec; border: 2px solid #f48fb1; }}
        .group-card.group-c {{ background: #f3e5f5; border: 2px solid #ce93d8; }}
        .group-card .group-name {{ font-size: 14px; font-weight: 700; margin-bottom: 4px; }}
        .group-card .group-n {{ font-size: 32px; font-weight: 700; margin-bottom: 4px; }}
        .group-card .group-detail {{ font-size: 12px; color: #666; }}
        .group-card.control .group-name,.group-card.control .group-n {{ color: #495057; }}
        .group-card.group-a .group-name,.group-card.group-a .group-n {{ color: #1565c0; }}
        .group-card.group-b .group-name,.group-card.group-b .group-n {{ color: #c62828; }}
        .group-card.group-c .group-name,.group-card.group-c .group-n {{ color: #7b1fa2; }}
        .footer {{ text-align: center; padding: 24px; font-size: 12px; color: #aaa; }}
        .divider {{ border: none; border-top: 3px solid #004481; margin: 40px 0 30px 0; opacity: 0.15; }}
        .section-title {{ text-align: center; font-size: 22px; font-weight: 700; color: #004481; margin-bottom: 8px; }}
        .section-subtitle {{ text-align: center; font-size: 14px; color: #888; margin-bottom: 30px; }}
        @media (max-width: 768px) {{ .kpi-grid, .group-grid {{ grid-template-columns: repeat(2, 1fr); }} .header .meta {{ flex-direction: column; gap: 4px; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Reporte QR BBVA <span class="live-badge"><span class="live-dot"></span> LIVE</span></h1>
            <p>Piloto de pagos con QR BBVA: resultados en tiempo real del experimento A/B/C</p>
            <div class="meta">
                <span>Piloto mayo: 232 clientes | Cohorte junio: 2,550 clientes</span>
                <span>Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}</span>
            </div>
        </div>

        <div class="phase-banner">
            <div class="phase-icon">2</div>
            <div class="phase-text">
                <h3>Fase 2: Experimento A/B/C en curso</h3>
                <p>Lanzado el 10/06/2026 14:10 &mdash; 2,550 clientes con cuotas venciendo entre el 10 y 18 de junio</p>
            </div>
        </div>

        <!-- KPIs -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Pagos post-QR</div>
                <div class="kpi-value">{fmt(total_pagos)}</div>
                <div class="kpi-sub">de {fmt(base)} base ({pct(total_pagos, base)} cobranza)</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Pagaron con QR</div>
                <div class="kpi-value">{pct(total_qr_paid, total_pagos)}</div>
                <div class="kpi-sub">{fmt(total_qr_paid)} de {fmt(total_pagos)} pagos</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Conversion QR</div>
                <div class="kpi-value">{pct(total_qr_paid, total_qr_gen)}</div>
                <div class="kpi-sub">{fmt(total_qr_paid)} pagaron de {fmt(total_qr_gen)} que generaron</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Monto cobrado</div>
                <div class="kpi-value">S/ {d['monto_cobrado']/1000:.0f}K</div>
                <div class="kpi-sub">de S/ {d['monto_total']/1000:.0f}K total ({pct(d['monto_cobrado'], d['monto_total'])})</div>
            </div>
        </div>

        <!-- Section 1: Distribucion de pagos -->
        <div class="section">
            <h2><span class="num">1</span> Distribucion de pagos (Fase 2)</h2>
            <p style="color:#666; font-size:14px; margin-bottom:8px;">De las {fmt(d['total_cuotas'])} cuotas en ventana, <strong>{d['pre_qr']} pagaron antes</strong> del QR. Base efectiva: <strong>{fmt(base)}</strong>.</p>
            <p style="color:#666; font-size:14px; margin-bottom:24px;"><strong>{fmt(total_pagos)} han pagado</strong> post-QR. S/ {fmt(d['monto_cobrado'])} cobrados de S/ {fmt(d['monto_total'])}.</p>

            <table style="margin-bottom:20px;">
                <thead><tr><th>Categoria</th><th class="text-center">Solicitudes</th><th class="text-center">% de {fmt(total_pagos)} pagos</th><th>Detalle</th></tr></thead>
                <tbody>
                    <tr><td class="bold" style="color:#28a745;">Pagaron con QR</td>{tdc(total_qr_paid, True, '#28a745')}{tdc(pct(total_qr_paid, total_pagos), True, '#28a745')}<td style="color:#666;">Generaron QR y completaron pago</td></tr>
                    <tr><td class="bold" style="color:#e8590c;">QR &rarr; migraron a otro</td>{tdc(migr_total, True, '#e8590c')}{tdc(pct(migr_total, total_pagos), True, '#e8590c')}<td style="color:#666;">{migr_desc}</td></tr>
                    <tr><td class="bold">Monnet directo</td>{tdc(direct_monnet, True)}{tdc(pct(direct_monnet, total_pagos))}<td style="color:#666;">Sin interaccion con QR</td></tr>
                    <tr><td class="bold">MP + Recaudo directo</td>{tdc(direct_mp + direct_rec, True)}{tdc(pct(direct_mp + direct_rec, total_pagos))}<td style="color:#666;">Sin interaccion con QR</td></tr>
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td>{tdc(total_pagos, True)}{tdc('100%', True)}<td></td></tr>
                </tbody>
            </table>

            <div style="margin-bottom:12px;"><div class="bar-container"><div class="bar-bg" style="height:36px;"><div style="display:flex; height:100%;">
                <div style="width:{pct_qr}%; background:#28a745; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600; border-radius:6px 0 0 6px;">QR {pct_qr}%</div>
                <div style="width:{pct_migr}%; background:#ffc107; display:flex; align-items:center; justify-content:center; color:#665200; font-size:11px; font-weight:600;">Migr {pct_migr}%</div>
                <div style="width:{pct_monnet}%; background:#004481; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600;">Monnet {pct_monnet}%</div>
                <div style="width:{max(pct_otros, 1)}%; background:#6f42c1; display:flex; align-items:center; justify-content:center; color:white; font-size:10px; font-weight:600; border-radius:0 6px 6px 0;">Otros {pct_otros}%</div>
            </div></div></div></div>

            <div class="note" style="background:#e8f5e9; border-left-color:#28a745; color:#1b5e20;">
                <strong>Lectura clave:</strong> QR capturo el <strong>{pct(total_qr_paid, total_pagos)}</strong> de pagos. Sumando migraciones, el <strong>{pct(total_qr_interacted, total_pagos)}</strong> ({total_qr_interacted} de {total_pagos}) provinieron de clientes que interactuaron con el QR.
            </div>
        </div>

        <!-- Section 2: Resultados por grupo -->
        <div class="section">
            <h2><span class="num">2</span> Resultados por grupo (post-QR)</h2>
            <table style="margin-bottom:20px;">
                <thead><tr><th>Grupo</th><th class="text-center">Base</th><th class="text-center">Pagaron</th><th class="text-center">% Cobranza</th><th class="text-center">Via QR</th><th class="text-center">% QR</th><th class="text-center">Generaron QR</th><th class="text-center">Conv. QR</th></tr></thead>
                <tbody>
                    {grp_html}
                    <tr style="background:#f8f9fa;">
                        <td class="bold">Total</td>{tdc(fmt(tot_base), True)}{tdc(fmt(tot_pag), True)}{tdc(pct(tot_pag, tot_base), True)}
                        {tdc(fmt(tot_qrp), True, '#28a745')}{tdc(pct(tot_qrp, tot_pag), True, '#28a745')}
                        {tdc(fmt(tot_qrg), True)}{tdc(pct(tot_qrp, tot_qrg), True)}
                    </tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Desglose por pasarela</h3>
            <table class="detail-table" style="margin-bottom:20px;">
                <thead><tr><th>Grupo</th><th class="text-center">Monnet</th><th class="text-center">BBVA QR</th><th class="text-center">MP</th><th class="text-center">Rec/Otros</th><th class="text-center">Total</th></tr></thead>
                <tbody>{grp_pas_html}</tbody>
            </table>
        </div>

        <!-- Section 3: Evolucion diaria -->
        <div class="section">
            <h2><span class="num">3</span> Evolucion diaria de pagos</h2>
            <table class="detail-table">
                <thead><tr><th>Dia</th><th class="text-center">Monnet</th><th class="text-center">BBVA QR</th><th class="text-center">MP</th><th class="text-center">Otros</th><th class="text-center">Total</th><th class="text-center">% QR</th><th class="text-center">Acum.</th></tr></thead>
                <tbody>
                    {daily_html}
                    <tr style="background:#f8f9fa;">
                        <td class="bold">Total</td>
                        <td class="text-center bold">{pas_monnet}</td>
                        <td class="text-center bold" style="color:#28a745;">{pas_qr}</td>
                        <td class="text-center bold">{pas_mp}</td>
                        <td class="text-center bold">{pas_rec}</td>
                        <td class="text-center bold">{total_pagos}</td>
                        <td class="text-center bold" style="color:#28a745;">{pct(pas_qr, total_pagos)}</td>
                        <td class="text-center bold">{total_pagos}</td>
                    </tr>
                </tbody>
            </table>
            <p style="color:#666; font-size:12px; margin-top:4px;">* 10/06: solo desde las 14:10. {d['pre_qr']} pagos pre-QR excluidos.</p>
        </div>

        <!-- Section 4: Segmentacion por monto -->
        <div class="section">
            <h2><span class="num">4</span> Segmentacion por monto de cuota</h2>
            <table style="margin-bottom:24px;">
                <thead><tr><th>Rango</th><th class="text-center">Base</th><th class="text-center">Pagaron</th><th class="text-center">% Cobranza</th><th class="text-center">Via QR</th><th class="text-center">% QR</th><th class="text-center">Gen QR</th><th class="text-center">Conv. QR</th></tr></thead>
                <tbody>
                    {rng_html}
                    <tr style="background:#f8f9fa;">
                        <td class="bold">Total</td>{tdc(fmt(base), True)}{tdc(fmt(total_pagos), True)}{tdc(pct(total_pagos, base), True)}
                        {tdc(fmt(total_qr_paid), True, '#28a745')}{tdc(pct(total_qr_paid, total_pagos), True, '#28a745')}
                        {tdc(fmt(total_qr_gen), True)}{tdc(pct(total_qr_paid, total_qr_gen), True)}
                    </tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Pasarela por rango</h3>
            <table class="detail-table" style="margin-bottom:20px;">
                <thead><tr><th>Rango</th><th class="text-center">Monnet</th><th class="text-center">BBVA QR</th><th class="text-center">MP</th><th class="text-center">Recaudo</th><th class="text-center">Total</th></tr></thead>
                <tbody>{rng_pas_html}</tbody>
            </table>
        </div>

        <!-- Section 5: Banco por pasarela -->
        <div class="section">
            <h2><span class="num">5</span> Banco seleccionado por pasarela</h2>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Monnet ({monnet_total} registros)</h3>
            <table style="margin-bottom:8px;">
                <thead><tr><th>Banco/Metodo</th><th class="text-center">Clientes</th><th class="text-center">%</th><th></th></tr></thead>
                <tbody>
                    {banco_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{monnet_total}</td><td class="text-center bold">100%</td><td></td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px; margin-top:24px;">Monnet por rango</h3>
            <table class="detail-table" style="margin-bottom:24px;">
                <thead><tr><th>Rango</th>{''.join(f'<th class="text-center">{bn}</th>' for bn in banco_names)}</tr></thead>
                <tbody>{banco_rng_html}</tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Mercado Pago ({mp_total} registros)</h3>
            <table style="margin-bottom:8px;">
                <thead><tr><th>Metodo</th><th class="text-center">Clientes</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    {mp_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{mp_total}</td><td class="text-center bold">100%</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Section 6: Trazabilidad QR -->
        <div class="section">
            <h2><span class="num">6</span> Trazabilidad QR</h2>
            <p style="color:#666; font-size:14px; margin-bottom:12px;">{d['qr_total']} QRs generados por {d['qr_clientes']} clientes. Status:</p>
            <table style="margin-bottom:20px;">
                <thead><tr><th>Status QR</th><th class="text-center">Cantidad</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    {qr_status_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{d['qr_total']}</td><td class="text-center bold">100%</td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">QRs por cliente</h3>
            <table class="detail-table" style="margin-bottom:20px;">
                <thead><tr><th>QRs generados</th><th class="text-center">Clientes</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    {qr_cli_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{d['qr_clientes']}</td><td class="text-center bold">100%</td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Migraciones: tiempo QR &rarr; otro medio</h3>
            <p style="color:#666; font-size:14px; margin-bottom:16px;"><strong>{d['migraron']} migraron</strong> a otro medio, <strong>{d['sin_pago']} sin pago</strong>.</p>
            <table class="detail-table" style="margin-bottom:20px;">
                <thead><tr><th>Tiempo</th><th class="text-center">Clientes</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    {traz_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{d['migraron']}</td><td class="text-center bold">100%</td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Top 10 migraciones mas rapidas</h3>
            <table class="detail-table" style="margin-bottom:20px;">
                <thead><tr><th>Solicitud</th><th>QR generado</th><th>Pago alterno</th><th class="text-center">Diferencia</th><th class="text-right">Monto QR</th><th class="text-right">Monto pagado</th><th>Pasarela</th></tr></thead>
                <tbody>{top_html}</tbody>
            </table>
        </div>

        <div class="footer">
            BaldeCash &mdash; Dashboard live &mdash; Ultima actualizacion: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
        </div>
    </div>
</body>
</html>'''
    return html


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            data = get_data()
            html = render(data)
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 's-maxage=300, stale-while-revalidate=60')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(f'''<!DOCTYPE html><html><body style="font-family:sans-serif;padding:40px;">
                <h1>Error generando dashboard</h1><pre>{str(e)}</pre>
                <p>Verifica las variables de entorno DB_HOST, DB_USER, DB_PASSWORD, DB_NAME.</p>
            </body></html>'''.encode('utf-8'))
