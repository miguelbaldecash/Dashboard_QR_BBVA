"""Vercel serverless function: Dashboard QR BBVA en tiempo real.
Muestra metricas del mes actual para TODAS las solicitudes con cuotas activas.
Sin separacion por grupos (desde julio 2026).
"""
import os, pymysql
from http.server import BaseHTTPRequestHandler
from datetime import datetime, date
from calendar import monthrange

# --- Config ---
DB = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASSWORD'),
    'database': os.environ.get('DB_NAME'),
    'connect_timeout': 15,
    'read_timeout': 30,
}

MESES_ES = {1:'Enero',2:'Febrero',3:'Marzo',4:'Abril',5:'Mayo',6:'Junio',
             7:'Julio',8:'Agosto',9:'Septiembre',10:'Octubre',11:'Noviembre',12:'Diciembre'}


def get_month_window():
    """Return (start, end) for the current month."""
    hoy = date.today()
    w_start = f"{hoy.year}-{hoy.month:02d}-01"
    if hoy.month == 12:
        w_end = f"{hoy.year + 1}-01-01"
    else:
        w_end = f"{hoy.year}-{hoy.month + 1:02d}-01"
    return w_start, w_end, hoy


def get_data():
    w_start, w_end, hoy = get_month_window()
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    d = {'w_start': w_start, 'w_end': w_end, 'mes': MESES_ES[hoy.month], 'anio': hoy.year}

    # --- Base: todas las cuotas activas en el mes ---
    cur.execute("""SELECT COUNT(DISTINCT c.solicitud_id), COUNT(*), ROUND(SUM(c.monto),2)
        FROM cronograma c WHERE c.estado='activo'
        AND c.fecha_vencimiento >= %s AND c.fecha_vencimiento < %s""", (w_start, w_end))
    r = cur.fetchone()
    d['total_solicitudes'] = r[0]
    d['total_cuotas'] = r[1]
    d['monto_total'] = float(r[2] or 0)

    # Cuota monto por solicitud
    cur.execute("""SELECT c.solicitud_id, ROUND(SUM(c.monto),2)
        FROM cronograma c WHERE c.estado='activo'
        AND c.fecha_vencimiento >= %s AND c.fecha_vencimiento < %s
        GROUP BY c.solicitud_id""", (w_start, w_end))
    cuota_montos = {int(r[0]): float(r[1]) for r in cur.fetchall()}

    # Pagos del mes por pasarela
    cur.execute("""SELECT c.solicitud_id, p.pasarela_pago
        FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.estado='activo' AND c.fecha_vencimiento >= %s AND c.fecha_vencimiento < %s
        AND p.deleted_at IS NULL""", (w_start, w_end))
    sol_pasarela = {}
    paid_set = set()
    for r in cur.fetchall():
        sid = int(r[0])
        paid_set.add(sid)
        sol_pasarela[sid] = r[1]

    d['total_pagos'] = len(paid_set)

    # Monto cobrado
    cur.execute("""SELECT ROUND(SUM(p.monto_pago_total),2)
        FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.estado='activo' AND c.fecha_vencimiento >= %s AND c.fecha_vencimiento < %s
        AND p.deleted_at IS NULL""", (w_start, w_end))
    d['monto_cobrado'] = float(cur.fetchone()[0] or 0)

    # Pasarela counts
    pas_count = {}
    for sid, pas in sol_pasarela.items():
        key = _classify_pasarela(pas)
        pas_count[key] = pas_count.get(key, 0) + 1
    d['pas_count'] = pas_count

    # QR metrics del mes
    cur.execute("""SELECT COUNT(DISTINCT q.solicitud_id)
        FROM bbva_qr_payments q WHERE q.status='paid'
        AND q.created_at >= %s AND q.created_at < %s""", (w_start, w_end))
    d['qr_paid'] = cur.fetchone()[0]

    cur.execute("""SELECT COUNT(DISTINCT q.solicitud_id)
        FROM bbva_qr_payments q
        WHERE q.created_at >= %s AND q.created_at < %s""", (w_start, w_end))
    d['qr_gen'] = cur.fetchone()[0]

    cur.execute("""SELECT q.status, COUNT(*)
        FROM bbva_qr_payments q
        WHERE q.created_at >= %s AND q.created_at < %s
        GROUP BY q.status""", (w_start, w_end))
    d['qr_status'] = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("""SELECT COUNT(*)
        FROM bbva_qr_payments q
        WHERE q.created_at >= %s AND q.created_at < %s""", (w_start, w_end))
    d['qr_total'] = cur.fetchone()[0]

    cur.execute("""SELECT COUNT(DISTINCT q.solicitud_id)
        FROM bbva_qr_payments q
        WHERE q.created_at >= %s AND q.created_at < %s""", (w_start, w_end))
    d['qr_clientes'] = cur.fetchone()[0]

    # QR por cliente
    cur.execute("""SELECT cnt, COUNT(*) FROM (
        SELECT q.solicitud_id, COUNT(*) AS cnt FROM bbva_qr_payments q
        WHERE q.created_at >= %s AND q.created_at < %s
        GROUP BY q.solicitud_id) t GROUP BY cnt ORDER BY cnt""", (w_start, w_end))
    d['qr_por_cliente'] = [(r[0], r[1]) for r in cur.fetchall()]

    # --- Segmentacion por monto ---
    ranges_order = ['0-50', '51-150', '151-250', '251+']
    R = {r: {'base': 0, 'pagaron': 0, 'qr_paid': 0, 'qr_gen': 0,
             'monnet': 0, 'bbva_qr': 0, 'mp': 0, 'recaudo': 0} for r in ranges_order}

    qr_paid_set = set()
    cur.execute("""SELECT DISTINCT q.solicitud_id FROM bbva_qr_payments q
        WHERE q.status='paid' AND q.created_at >= %s AND q.created_at < %s""", (w_start, w_end))
    for r in cur.fetchall():
        qr_paid_set.add(int(r[0]))

    qr_gen_set = set()
    cur.execute("""SELECT DISTINCT q.solicitud_id FROM bbva_qr_payments q
        WHERE q.created_at >= %s AND q.created_at < %s""", (w_start, w_end))
    for r in cur.fetchall():
        qr_gen_set.add(int(r[0]))

    for sid, m in cuota_montos.items():
        rng = _rango(m)
        R[rng]['base'] += 1
        if sid in paid_set:
            R[rng]['pagaron'] += 1
            pas = sol_pasarela.get(sid, '')
            if 'MONNET' in pas: R[rng]['monnet'] += 1
            elif 'QR' in pas: R[rng]['bbva_qr'] += 1
            elif 'MERCADO' in pas: R[rng]['mp'] += 1
            elif 'RECAUDO' in pas or 'BK' in pas: R[rng]['recaudo'] += 1
        if sid in qr_paid_set: R[rng]['qr_paid'] += 1
        if sid in qr_gen_set: R[rng]['qr_gen'] += 1

    d['por_monto'] = R
    d['ranges_order'] = ranges_order
    d['cuota_promedio'] = round(d['monto_total'] / d['total_solicitudes'], 2) if d['total_solicitudes'] else 0

    # --- Evolucion diaria (todos los meses desde mayo 2026) ---
    cur.execute("""SELECT DATE(p.fecha_pago) AS dia, p.pasarela_pago, COUNT(DISTINCT c.solicitud_id)
        FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.estado='activo'
        AND c.fecha_vencimiento >= '2026-05-01' AND c.fecha_vencimiento < %s
        AND p.deleted_at IS NULL
        GROUP BY dia, p.pasarela_pago ORDER BY dia""", (w_end,))
    daily_raw = {}
    for r in cur.fetchall():
        dia = str(r[0])
        if dia not in daily_raw:
            daily_raw[dia] = {'monnet': 0, 'bbva_qr': 0, 'mp': 0, 'otros': 0}
        pas = r[1]
        if 'MONNET' in pas: daily_raw[dia]['monnet'] += r[2]
        elif 'QR' in pas: daily_raw[dia]['bbva_qr'] += r[2]
        elif 'MERCADO' in pas: daily_raw[dia]['mp'] += r[2]
        else: daily_raw[dia]['otros'] += r[2]
    d['diario'] = daily_raw

    # --- Banco Monnet ---
    cur.execute("""SELECT b.nombre, COUNT(*) FROM monnet_pago_request m
        JOIN evento_zonaclientes e ON e.id=m.evento_id JOIN banco b ON b.id=e.banco_id
        WHERE m.status IN ('5','9') AND m.created_at >= %s AND m.created_at < %s
        GROUP BY b.nombre ORDER BY 2 DESC""", (w_start, w_end))
    d['bancos_monnet'] = [(r[0], r[1]) for r in cur.fetchall()]

    cur.execute("""SELECT CASE WHEN cr.monto<=50 THEN '0-50' WHEN cr.monto<=150 THEN '51-150'
        WHEN cr.monto<=250 THEN '151-250' ELSE '251+' END AS rango,
        b.nombre, COUNT(*) FROM monnet_pago_request m
        JOIN evento_zonaclientes e ON e.id=m.evento_id JOIN banco b ON b.id=e.banco_id
        JOIN cronograma cr ON cr.solicitud_id=m.solicitud_id AND cr.estado='activo'
        AND cr.fecha_vencimiento >= %s AND cr.fecha_vencimiento < %s
        WHERE m.status IN ('5','9') AND m.created_at >= %s AND m.created_at < %s
        GROUP BY rango, b.nombre ORDER BY rango, 3 DESC""",
        (w_start, w_end, w_start, w_end))
    d['bancos_monnet_rango'] = [(r[0], r[1], r[2]) for r in cur.fetchall()]

    # MP metodo
    cur.execute("""SELECT CASE JSON_UNQUOTE(JSON_EXTRACT(p.mercado_pago_payment_json, '$.issuer_id'))
        WHEN '12512' THEN 'BCP' WHEN '12446' THEN 'Interbank' WHEN '12354' THEN 'BBVA' WHEN '12551' THEN 'Scotiabank'
        ELSE CONCAT('Otro (', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(p.mercado_pago_payment_json, '$.payment_method_id')),'?'), ')')
        END AS banco, COUNT(*) FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.estado='activo' AND c.fecha_vencimiento >= %s AND c.fecha_vencimiento < %s
        AND p.deleted_at IS NULL AND p.mercado_pago_payment_json IS NOT NULL
        GROUP BY banco ORDER BY 2 DESC""", (w_start, w_end))
    d['mp_metodo'] = [(r[0], r[1]) for r in cur.fetchall()]

    # --- Trazabilidad: QR generados que no fueron pagados con QR ---
    cur.execute("""SELECT q.solicitud_id, q.created_at, q.status, q.amount,
        p.pasarela_pago, p.fecha_pago, p.monto_pago_total,
        TIMESTAMPDIFF(SECOND, q.created_at, p.fecha_pago) AS seg
        FROM bbva_qr_payments q
        LEFT JOIN cronograma c ON c.solicitud_id=q.solicitud_id AND c.estado='activo'
            AND c.fecha_vencimiento >= %s AND c.fecha_vencimiento < %s
        LEFT JOIN pago p ON p.cronograma_id=c.id AND p.deleted_at IS NULL
        WHERE q.created_at >= %s AND q.created_at < %s
        AND q.status IN ('pending','cancelled')
        ORDER BY seg ASC""", (w_start, w_end, w_start, w_end))
    seen = set()
    migrated = []
    no_pago = 0
    for r in cur.fetchall():
        sid = int(r[0])
        if sid in seen:
            continue
        seen.add(sid)
        if r[4]:
            migrated.append({'sol': sid, 'seg': r[7], 'pas': r[4],
                             'qr_at': str(r[1]), 'p_at': str(r[5]),
                             'qm': float(r[3]), 'pm': float(r[6])})
        else:
            no_pago += 1

    bk = {'<1min': 0, '1-10min': 0, '10min-1h': 0, '>1h': 0}
    for m in migrated:
        s = m['seg']
        if s is None:
            continue
        if s < 60: bk['<1min'] += 1
        elif s < 600: bk['1-10min'] += 1
        elif s < 3600: bk['10min-1h'] += 1
        else: bk['>1h'] += 1

    d['migraron'] = len(migrated)
    d['sin_pago'] = no_pago
    d['tiempos_migracion'] = bk
    d['top_rapidas'] = sorted([m for m in migrated if m['seg'] and m['seg'] > 0],
                               key=lambda x: x['seg'])[:10]

    migr_pas = {}
    for m in migrated:
        p = m['pas']
        migr_pas[p] = migr_pas.get(p, 0) + 1
    d['migr_pasarela'] = migr_pas

    # --- Evolucion mensual (ultimos 3 meses con datos QR) ---
    cur.execute("""SELECT DATE_FORMAT(q.created_at, '%%Y-%%m') AS mes,
        COUNT(DISTINCT CASE WHEN q.status='paid' THEN q.solicitud_id END),
        COUNT(DISTINCT q.solicitud_id), COUNT(*)
        FROM bbva_qr_payments q WHERE q.created_at >= '2026-05-01'
        GROUP BY mes ORDER BY mes""")
    qr_hist = {r[0]: {'paid': r[1], 'gen': r[2], 'qrs': r[3]} for r in cur.fetchall()}

    cur.execute("""SELECT DATE_FORMAT(c.fecha_vencimiento, '%%Y-%%m') AS mes,
        COUNT(DISTINCT c.solicitud_id), ROUND(SUM(c.monto),2)
        FROM cronograma c WHERE c.estado='activo' AND c.fecha_vencimiento >= '2026-05-01'
        AND c.fecha_vencimiento < %s
        GROUP BY mes ORDER BY mes""", (w_end,))
    cuota_hist = {r[0]: {'solic': r[1], 'monto': float(r[2])} for r in cur.fetchall()}

    cur.execute("""SELECT DATE_FORMAT(c.fecha_vencimiento, '%%Y-%%m') AS mes,
        p.pasarela_pago, COUNT(DISTINCT c.solicitud_id)
        FROM pago p JOIN cronograma c ON c.id=p.cronograma_id
        WHERE c.estado='activo' AND c.fecha_vencimiento >= '2026-05-01'
        AND c.fecha_vencimiento < %s AND p.deleted_at IS NULL
        GROUP BY mes, p.pasarela_pago ORDER BY mes""", (w_end,))
    pago_hist = {}
    for r in cur.fetchall():
        mes = r[0]
        if mes not in pago_hist:
            pago_hist[mes] = {'total': 0, 'bbva_qr': 0, 'monnet': 0, 'mp': 0, 'otros': 0}
        cnt = r[2]
        pago_hist[mes]['total'] += cnt
        pas = r[1]
        if 'QR' in pas: pago_hist[mes]['bbva_qr'] += cnt
        elif 'MONNET' in pas: pago_hist[mes]['monnet'] += cnt
        elif 'MERCADO' in pas: pago_hist[mes]['mp'] += cnt
        else: pago_hist[mes]['otros'] += cnt

    d['hist'] = {'qr': qr_hist, 'cuotas': cuota_hist, 'pagos': pago_hist}

    conn.close()
    return d


def _classify_pasarela(pas):
    if 'QR' in pas: return 'bbva_qr'
    if 'MONNET' in pas: return 'monnet'
    if 'MERCADO' in pas: return 'mp'
    if 'RECAUDO' in pas or 'BK' in pas: return 'recaudo'
    return 'otros'


def _rango(m):
    if m <= 50: return '0-50'
    elif m <= 150: return '51-150'
    elif m <= 250: return '151-250'
    return '251+'


# --- HTML helpers ---
def pct(n, total, decimals=1):
    if not total: return '-'
    return f"{n/total*100:.{decimals}f}%"

def fmt(n):
    if isinstance(n, float): return f"{n:,.2f}"
    return f"{n:,}"

def tdc(v, bold=False, color=''):
    cls = 'text-center'
    if bold: cls += ' bold'
    style = f' style="color:{color};"' if color else ''
    return f'<td class="{cls}"{style}>{v}</td>'


# --- HTML generation ---
def render(d):
    now = datetime.now()
    hoy_str = now.strftime('%Y-%m-%d')
    total_pagos = d['total_pagos']
    base = d['total_solicitudes']
    qr_paid = d['qr_paid']
    qr_gen = d['qr_gen']
    pc = d['pas_count']
    pas_qr = pc.get('bbva_qr', 0)
    pas_monnet = pc.get('monnet', 0)
    pas_mp = pc.get('mp', 0)
    pas_rec = pc.get('recaudo', 0)
    pas_otros = pc.get('otros', 0)

    # Bar widths
    pct_qr = round(pas_qr / total_pagos * 100, 1) if total_pagos else 0
    pct_monnet = round(pas_monnet / total_pagos * 100, 1) if total_pagos else 0
    pct_mp = round(pas_mp / total_pagos * 100, 1) if total_pagos else 0
    pct_otros_bar = round(100 - pct_qr - pct_monnet - pct_mp, 1)

    # --- Daily table grouped by month ---
    daily_html = ''
    # Group days by month, compute per-month totals
    hoy = date.today()
    current_mes = f"{hoy.year}-{hoy.month:02d}"
    daily_by_month = {}
    for dia in sorted(d['diario'].keys()):
        mes_key = dia[:7]  # '2026-07'
        if mes_key not in daily_by_month:
            daily_by_month[mes_key] = []
        daily_by_month[mes_key].append(dia)

    # Build month filter options (enero a diciembre 2026)
    month_filter_options = ''
    for m_ in range(1, 13):
        mk = f"2026-{m_:02d}"
        label = MESES_ES[m_]
        sel = ' selected' if mk == current_mes else ''
        month_filter_options += f'<option value="{mk}"{sel}>{label}</option>'

    # Build all daily rows with data-mes attribute
    for dia in sorted(d['diario'].keys()):
        dd = d['diario'][dia]
        mes_key = dia[:7]
        total_dia = dd['monnet'] + dd['bbva_qr'] + dd['mp'] + dd['otros']
        pct_qr_dia = pct(dd['bbva_qr'], total_dia)
        dia_fmt = dia[8:10] + '/' + dia[5:7]  # DD/MM
        bg_style = 'background:#fff8e1;' if dia == hoy_str else ''
        display = 'none' if mes_key != current_mes else ''
        style = f' style="{bg_style}display:{display};"'.replace('display:;', '').replace('style=" "', '').strip()
        if not bg_style and display:
            style = f' style="display:none;"'
        elif bg_style and display:
            style = f' style="{bg_style}display:{display};"'
        elif bg_style:
            style = f' style="{bg_style}"'
        else:
            style = ''
        daily_html += f'''<tr class="daily-row" data-mes="{mes_key}"{style}>
            <td class="bold">{dia_fmt}</td>
            {tdc(dd['bbva_qr'], True, '#28a745')}{tdc(dd['monnet'])}{tdc(dd['mp'])}{tdc(dd['otros'])}
            <td class="text-center bold">{total_dia}</td>
            <td class="text-center bold" style="color:#28a745;">{pct_qr_dia}</td>
        </tr>'''

    # Per-month summary rows (hidden, shown by JS)
    monthly_summary = {}
    for mk, dias in daily_by_month.items():
        s = {'bbva_qr': 0, 'monnet': 0, 'mp': 0, 'otros': 0, 'total': 0}
        for dia in dias:
            dd = d['diario'][dia]
            s['bbva_qr'] += dd['bbva_qr']
            s['monnet'] += dd['monnet']
            s['mp'] += dd['mp']
            s['otros'] += dd['otros']
            s['total'] += dd['monnet'] + dd['bbva_qr'] + dd['mp'] + dd['otros']
        monthly_summary[mk] = s

    daily_summary_rows = ''
    for mk, s in sorted(monthly_summary.items()):
        display = 'none' if mk != current_mes else ''
        style = f' style="background:#f8f9fa;display:{display};"' if display else ' style="background:#f8f9fa;"'
        pct_qr_m = pct(s['bbva_qr'], s['total'])
        daily_summary_rows += f'''<tr class="daily-summary" data-mes="{mk}"{style}>
            <td class="bold">Total</td>
            <td class="text-center bold" style="color:#28a745;">{s['bbva_qr']}</td>
            <td class="text-center bold">{s['monnet']}</td>
            <td class="text-center bold">{s['mp']}</td>
            <td class="text-center bold">{s['otros']}</td>
            <td class="text-center bold">{s['total']}</td>
            <td class="text-center bold" style="color:#28a745;">{pct_qr_m}</td>
        </tr>'''

    # --- Monto range tables ---
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
            {tdc(f"{rd['bbva_qr']} ({pct(rd['bbva_qr'], rt)})", True, '#28a745')}
            {tdc(f"{rd['monnet']} ({pct(rd['monnet'], rt)})")}
            {tdc(f"{rd['mp']} ({pct(rd['mp'], rt)})")}
            {tdc(f"{rd['recaudo']} ({pct(rd['recaudo'], rt)})")}
            {tdc(rt, True)}
        </tr>'''

    # --- Banco Monnet table ---
    monnet_total = sum(b[1] for b in d['bancos_monnet']) if d['bancos_monnet'] else 0
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

    banco_rng = {}
    for rng, bname, cnt in d['bancos_monnet_rango']:
        if rng not in banco_rng: banco_rng[rng] = {}
        banco_rng[rng][bname] = cnt
    banco_rng_html = ''
    banco_names = [b[0] for b in d['bancos_monnet'][:4]] if d['bancos_monnet'] else ['Yape', 'BCP', 'Interbank', 'BBVA']
    for r in d['ranges_order']:
        cells = ''
        vals = banco_rng.get(r, {})
        max_v = max(vals.values(), default=0)
        for bn in banco_names:
            v = vals.get(bn, 0)
            bold = ' bold' if v == max_v and v > 0 else ''
            cells += f'<td class="text-center{bold}">{v if v else "-"}</td>'
        banco_rng_html += f'<tr><td class="bold">S/ {r}</td>{cells}</tr>'

    # --- MP metodo ---
    mp_total = sum(m[1] for m in d['mp_metodo']) if d['mp_metodo'] else 0
    mp_html = ''
    mp_name_map = {'Otro (pagoefectivo_atm)': 'PagoEfectivo', 'Otro (yape)': 'Yape',
                   'Otro (debvisa)': 'Debito Visa', 'Otro (debmaster)': 'Debito Mastercard'}
    for m_name, m_cnt in d['mp_metodo']:
        display = mp_name_map.get(m_name, m_name)
        p = round(m_cnt / mp_total * 100, 1) if mp_total else 0
        mp_html += f'''<tr>
            <td class="bold">{display}</td>
            <td class="text-center bold">{m_cnt}</td>
            <td class="text-center">{p}%</td>
        </tr>'''

    # --- QR status ---
    qs = d['qr_status']
    qr_t = d['qr_total']
    qr_status_html = ''
    for s, badge in [('paid', 'status-paid'), ('pending', 'status-pending'),
                     ('cancelled', 'status-cancelled'), ('expired', 'status-expired')]:
        v = qs.get(s, 0)
        if v == 0 and s == 'expired': continue
        qr_status_html += f'''<tr>
            <td><span class="status-badge {badge}">{s.capitalize()}</span></td>
            <td class="text-center bold">{v}</td>
            <td class="text-center">{pct(v, qr_t)}</td>
        </tr>'''

    qr_cli_html = ''
    for cnt, clients in d['qr_por_cliente']:
        qr_cli_html += f'''<tr>
            <td class="bold">{cnt} QR{"s" if cnt > 1 else ""}</td>
            <td class="text-center bold">{clients}</td>
            <td class="text-center">{pct(clients, d['qr_clientes'])}</td>
        </tr>'''

    # --- Trazabilidad ---
    traz_html = ''
    tbk = d['tiempos_migracion']
    for label, key in [('Menos de 1 minuto', '<1min'), ('1 a 10 minutos', '1-10min'),
                       ('10 min a 1 hora', '10min-1h'), ('Mas de 1 hora', '>1h')]:
        v = tbk[key]
        traz_html += f'''<tr>
            <td>{label}</td>
            <td class="text-center bold">{v}</td>
            <td class="text-center">{pct(v, d['migraron'])}</td>
        </tr>'''

    top_html = ''
    for m in d['top_rapidas']:
        seg = m['seg']
        t_fmt = f"{seg} seg" if seg < 60 else f"{seg/60:.1f} min"
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

    migr_desc = ', '.join(f"{p} ({c})" for p, c in
                          sorted(d['migr_pasarela'].items(), key=lambda x: -x[1]))

    # --- Evolucion mensual ---
    hist_html = ''
    hist = d['hist']
    hist_meses = sorted(set(list(hist['cuotas'].keys()) + list(hist['pagos'].keys())))
    for mes in hist_meses:
        y, m = mes.split('-')
        mes_label = f"{MESES_ES.get(int(m), m)} {y}"
        ch = hist['cuotas'].get(mes, {})
        ph_ = hist['pagos'].get(mes, {})
        qh = hist['qr'].get(mes, {})
        solic = ch.get('solic', 0)
        total_pag = ph_.get('total', 0)
        qr_p = ph_.get('bbva_qr', 0)
        qr_gen_h = qh.get('gen', 0)
        qr_paid_h = qh.get('paid', 0)
        conv = pct(qr_paid_h, qr_gen_h) if qr_gen_h else '-'
        pct_qr_h = pct(qr_p, total_pag) if total_pag else '-'
        pct_cob = pct(total_pag, solic) if solic else '-'
        is_current = mes == f"{now.year}-{now.month:02d}"
        bg = ' style="background:#fff8e1;"' if is_current else ''
        hist_html += f'''<tr{bg}>
            <td class="bold">{mes_label}{'*' if is_current else ''}</td>
            {tdc(fmt(solic))}{tdc(fmt(total_pag))}{tdc(pct_cob, True)}
            {tdc(fmt(qr_p), True, '#28a745')}{tdc(pct_qr_h, True, '#28a745')}
            {tdc(fmt(qr_gen_h))}{tdc(conv, True)}
        </tr>'''

    # --- FULL HTML ---
    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard QR BBVA - {d['mes']} {d['anio']}</title>
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
        .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .kpi-card {{ background: white; border-radius: 12px; padding: 28px 24px; text-align: center; box-shadow: 0 2px 12px rgba(0,0,0,0.06); position: relative; overflow: hidden; }}
        .kpi-card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px; }}
        .kpi-card:nth-child(1)::before {{ background: #004481; }}
        .kpi-card:nth-child(2)::before {{ background: #28a745; }}
        .kpi-card:nth-child(3)::before {{ background: #0066b3; }}
        .kpi-card:nth-child(4)::before {{ background: #e8590c; }}
        .kpi-label {{ font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }}
        .kpi-value {{ font-size: 36px; font-weight: 700; }}
        .kpi-card:nth-child(1) .kpi-value {{ color: #004481; }}
        .kpi-card:nth-child(2) .kpi-value {{ color: #28a745; }}
        .kpi-card:nth-child(3) .kpi-value {{ color: #0066b3; }}
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
        .detail-table {{ margin-top: 16px; }}
        .detail-table th {{ font-size: 11px; }}
        .detail-table td {{ font-size: 13px; padding: 10px 16px; }}
        .status-badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
        .status-paid {{ background: #d4edda; color: #155724; }}
        .status-expired {{ background: #fde2e2; color: #9b1c1c; }}
        .status-pending {{ background: #fff3cd; color: #856404; }}
        .status-cancelled {{ background: #f8d7da; color: #721c24; }}
        .note {{ background: #fff8e1; border-left: 4px solid #ffc107; padding: 16px 20px; border-radius: 0 8px 8px 0; font-size: 13px; color: #665200; margin-top: 20px; line-height: 1.6; }}
        .footer {{ text-align: center; padding: 24px; font-size: 12px; color: #aaa; }}
        .divider {{ border: none; border-top: 3px solid #004481; margin: 40px 0 30px 0; opacity: 0.15; }}
        .section-title {{ text-align: center; font-size: 22px; font-weight: 700; color: #004481; margin-bottom: 8px; }}
        .section-subtitle {{ text-align: center; font-size: 14px; color: #888; margin-bottom: 30px; }}
        @media (max-width: 768px) {{ .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }} .header .meta {{ flex-direction: column; gap: 4px; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Dashboard QR BBVA <span class="live-badge"><span class="live-dot"></span> LIVE</span></h1>
            <p>Metricas de cobranza y adopcion de QR BBVA &mdash; {d['mes']} {d['anio']}</p>
            <div class="meta">
                <span>{fmt(base)} solicitudes con cuotas en {d['mes'].lower()}</span>
                <span>Cuota promedio: S/ {fmt(d['cuota_promedio'])}</span>
                <span>Actualizado: {now.strftime('%d/%m/%Y %H:%M')}</span>
            </div>
        </div>

        <!-- KPIs -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Cobranza</div>
                <div class="kpi-value">{pct(total_pagos, base)}</div>
                <div class="kpi-sub">{fmt(total_pagos)} de {fmt(base)} solicitudes</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Pagaron con QR</div>
                <div class="kpi-value">{pct(pas_qr, total_pagos)}</div>
                <div class="kpi-sub">{fmt(pas_qr)} de {fmt(total_pagos)} pagos</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Conversion QR</div>
                <div class="kpi-value">{pct(qr_paid, qr_gen)}</div>
                <div class="kpi-sub">{fmt(qr_paid)} pagaron de {fmt(qr_gen)} que generaron</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Monto cobrado</div>
                <div class="kpi-value">S/ {d['monto_cobrado']/1000:.0f}K</div>
                <div class="kpi-sub">de S/ {d['monto_total']/1000:.0f}K ({pct(d['monto_cobrado'], d['monto_total'])})</div>
            </div>
        </div>

        <!-- 1: Distribucion de pagos -->
        <div class="section">
            <h2><span class="num">1</span> Distribucion de pagos por pasarela</h2>
            <table style="margin-bottom:20px;">
                <thead><tr><th>Pasarela</th><th class="text-center">Solicitudes</th><th class="text-center">% de pagos</th></tr></thead>
                <tbody>
                    <tr><td class="bold" style="color:#28a745;">BBVA QR</td>{tdc(fmt(pas_qr), True, '#28a745')}{tdc(pct(pas_qr, total_pagos), True, '#28a745')}</tr>
                    <tr><td class="bold">Monnet</td>{tdc(fmt(pas_monnet), True)}{tdc(pct(pas_monnet, total_pagos))}</tr>
                    <tr><td class="bold">Mercado Pago</td>{tdc(fmt(pas_mp), True)}{tdc(pct(pas_mp, total_pagos))}</tr>
                    <tr><td class="bold">Recaudo / BK</td>{tdc(fmt(pas_rec + pas_otros), True)}{tdc(pct(pas_rec + pas_otros, total_pagos))}</tr>
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td>{tdc(fmt(total_pagos), True)}{tdc('100%', True)}</tr>
                </tbody>
            </table>

            <div style="margin-bottom:12px;"><div style="height:36px; background:#f0f2f5; border-radius:6px; overflow:hidden; display:flex;">
                <div style="width:{pct_qr}%; background:#28a745; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600; border-radius:6px 0 0 6px;">QR {pct_qr}%</div>
                <div style="width:{pct_monnet}%; background:#004481; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600;">Monnet {pct_monnet}%</div>
                <div style="width:{pct_mp}%; background:#6f42c1; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600;">MP {pct_mp}%</div>
                <div style="width:{max(pct_otros_bar, 1)}%; background:#adb5bd; display:flex; align-items:center; justify-content:center; color:white; font-size:10px; font-weight:600; border-radius:0 6px 6px 0;">Otros</div>
            </div></div>

            <div class="note" style="background:#e8f5e9; border-left-color:#28a745; color:#1b5e20;">
                <strong>QR BBVA es el canal principal</strong> con el <strong>{pct(pas_qr, total_pagos)}</strong> de los pagos.
                Sumando clientes que generaron QR y luego migraron ({d['migraron']}), el QR influyo en <strong>{pct(pas_qr + d['migraron'], total_pagos)}</strong> de las cobranzas.
            </div>
        </div>

        <!-- 2: Evolucion diaria -->
        <div class="section">
            <h2 style="justify-content:space-between;"><span style="display:flex;align-items:center;gap:10px;"><span class="num">2</span> Evolucion diaria de pagos</span>
                <select id="mesFilter" onchange="filterMonth(this.value)" style="padding:6px 32px 6px 12px; border-radius:8px; border:2px solid #004481; color:#004481; font-size:13px; font-weight:600; cursor:pointer; appearance:none; -webkit-appearance:none; background:white url('data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 fill=%22%23004481%22 viewBox=%220 0 16 16%22%3E%3Cpath d=%22M8 11L3 6h10z%22/%3E%3C/svg%3E') no-repeat right 10px center;">
                    {month_filter_options}
                </select>
            </h2>
            <table class="detail-table" id="dailyTable">
                <thead><tr><th>Dia</th><th class="text-center">BBVA QR</th><th class="text-center">Monnet</th><th class="text-center">MP</th><th class="text-center">Otros</th><th class="text-center">Total</th><th class="text-center">% QR</th></tr></thead>
                <tbody>
                    {daily_html}
                    {daily_summary_rows}
                </tbody>
            </table>
        </div>
        <script>
        function filterMonth(mes) {{
            document.querySelectorAll('.daily-row, .daily-summary').forEach(function(tr) {{
                tr.style.display = tr.getAttribute('data-mes') === mes ? '' : 'none';
            }});
        }}
        </script>

        <!-- 3: Segmentacion por monto -->
        <div class="section">
            <h2><span class="num">3</span> Segmentacion por monto de cuota</h2>
            <table style="margin-bottom:24px;">
                <thead><tr><th>Rango</th><th class="text-center">Base</th><th class="text-center">Pagaron</th><th class="text-center">% Cobranza</th><th class="text-center">Via QR</th><th class="text-center">% QR</th><th class="text-center">Gen QR</th><th class="text-center">Conv. QR</th></tr></thead>
                <tbody>
                    {rng_html}
                    <tr style="background:#f8f9fa;">
                        <td class="bold">Total</td>{tdc(fmt(base), True)}{tdc(fmt(total_pagos), True)}{tdc(pct(total_pagos, base), True)}
                        {tdc(fmt(qr_paid), True, '#28a745')}{tdc(pct(qr_paid, total_pagos), True, '#28a745')}
                        {tdc(fmt(qr_gen), True)}{tdc(pct(qr_paid, qr_gen), True)}
                    </tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Pasarela por rango</h3>
            <table class="detail-table">
                <thead><tr><th>Rango</th><th class="text-center">BBVA QR</th><th class="text-center">Monnet</th><th class="text-center">MP</th><th class="text-center">Recaudo</th><th class="text-center">Total</th></tr></thead>
                <tbody>{rng_pas_html}</tbody>
            </table>
        </div>

        <!-- 4: Banco por pasarela -->
        <div class="section">
            <h2><span class="num">4</span> Banco seleccionado por pasarela</h2>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Monnet ({monnet_total} registros)</h3>
            <table style="margin-bottom:8px;">
                <thead><tr><th>Banco/Metodo</th><th class="text-center">Clientes</th><th class="text-center">%</th><th></th></tr></thead>
                <tbody>
                    {banco_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{monnet_total}</td><td class="text-center bold">100%</td><td></td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px; margin-top:24px;">Monnet por rango de cuota</h3>
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

        <!-- 5: Trazabilidad QR -->
        <div class="section">
            <h2><span class="num">5</span> Trazabilidad QR</h2>
            <p style="color:#666; font-size:14px; margin-bottom:12px;">{d['qr_total']} QRs generados por {d['qr_clientes']} clientes en {d['mes'].lower()}.</p>
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
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Migraciones: QR &rarr; otro medio</h3>
            <p style="color:#666; font-size:14px; margin-bottom:16px;"><strong>{d['migraron']} migraron</strong> ({migr_desc}). <strong>{d['sin_pago']} sin pago</strong>.</p>
            <table class="detail-table" style="margin-bottom:20px;">
                <thead><tr><th>Tiempo</th><th class="text-center">Clientes</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    {traz_html}
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">{d['migraron']}</td><td class="text-center bold">100%</td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Top 10 migraciones mas rapidas</h3>
            <table class="detail-table">
                <thead><tr><th>Solicitud</th><th>QR generado</th><th>Pago alterno</th><th class="text-center">Diferencia</th><th class="text-right">Monto QR</th><th class="text-right">Monto pagado</th><th>Pasarela</th></tr></thead>
                <tbody>{top_html}</tbody>
            </table>
        </div>

        <!-- 6: Evolucion mensual -->
        <hr class="divider">
        <p class="section-title">Evolucion Mensual</p>
        <p class="section-subtitle">Comparativa desde el lanzamiento de QR BBVA (mayo 2026)</p>

        <div class="section">
            <h2><span class="num">6</span> Metricas mes a mes</h2>
            <table>
                <thead><tr><th>Mes</th><th class="text-center">Solicitudes</th><th class="text-center">Pagaron</th><th class="text-center">% Cobranza</th><th class="text-center">Via QR</th><th class="text-center">% QR</th><th class="text-center">Gen QR</th><th class="text-center">Conv. QR</th></tr></thead>
                <tbody>
                    {hist_html}
                </tbody>
            </table>
            <p style="font-size:12px; color:#888; margin-top:8px;">* Mes en curso (datos parciales)</p>
        </div>

        <!-- ===== FASE 2: EXPERIMENTO A/B/C JUNIO (historico) ===== -->
        <hr class="divider">
        <p class="section-title">Fase 2: Experimento A/B/C (Junio 2026)</p>
        <p class="section-subtitle">2,550 clientes con cuotas 10-18 junio &mdash; 4 grupos estratificados &mdash; Fase concluida</p>

        <div class="section">
            <h2><span class="num">F2</span> Diseno del experimento</h2>
            <p style="color:#666; font-size:14px; margin-bottom:24px;">2,550 clientes con cuotas venciendo entre el 10 y 18 de junio, asignados aleatoriamente a 4 grupos balanceados por fecha de vencimiento y monto.</p>
            <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:24px;">
                <div style="border-radius:10px; padding:20px; text-align:center; background:#f8f9fa; border:2px solid #dee2e6;"><div style="font-size:14px; font-weight:700; color:#495057;">Control</div><div style="font-size:32px; font-weight:700; color:#495057;">638</div><div style="font-size:12px; color:#666;">S/ 106,885 | Prom. S/ 167.53</div></div>
                <div style="border-radius:10px; padding:20px; text-align:center; background:#e3f2fd; border:2px solid #90caf9;"><div style="font-size:14px; font-weight:700; color:#1565c0;">Grupo A</div><div style="font-size:32px; font-weight:700; color:#1565c0;">638</div><div style="font-size:12px; color:#666;">S/ 100,698 | Prom. S/ 157.83</div></div>
                <div style="border-radius:10px; padding:20px; text-align:center; background:#fce4ec; border:2px solid #f48fb1;"><div style="font-size:14px; font-weight:700; color:#c62828;">Grupo B</div><div style="font-size:32px; font-weight:700; color:#c62828;">637</div><div style="font-size:12px; color:#666;">S/ 111,383 | Prom. S/ 174.86</div></div>
                <div style="border-radius:10px; padding:20px; text-align:center; background:#f3e5f5; border:2px solid #ce93d8;"><div style="font-size:14px; font-weight:700; color:#7b1fa2;">Grupo C</div><div style="font-size:32px; font-weight:700; color:#7b1fa2;">637</div><div style="font-size:12px; color:#666;">S/ 105,198 | Prom. S/ 165.15</div></div>
            </div>
            <div class="note"><strong>Balance:</strong> Diferencia maxima entre promedios: S/ 17.03 (10.8%). Todos dentro de +/- 5.1% de la media global (S/ 166.34).</div>
        </div>

        <div class="section">
            <h2><span class="num">F2</span> Distribucion por fecha de vencimiento</h2>
            <table style="margin-bottom:20px;">
                <thead><tr><th>Fecha venc.</th><th class="text-center">Control</th><th class="text-center">A</th><th class="text-center">B</th><th class="text-center">C</th><th class="text-center">Total</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    <tr><td class="bold">10/06</td><td class="text-center">185</td><td class="text-center">185</td><td class="text-center">184</td><td class="text-center">184</td><td class="text-center bold">738</td><td class="text-center">28.9%</td></tr>
                    <tr><td class="bold">11/06</td><td class="text-center">10</td><td class="text-center">10</td><td class="text-center">11</td><td class="text-center">11</td><td class="text-center bold">42</td><td class="text-center">1.6%</td></tr>
                    <tr><td class="bold">12/06</td><td class="text-center">12</td><td class="text-center">12</td><td class="text-center">11</td><td class="text-center">11</td><td class="text-center bold">46</td><td class="text-center">1.8%</td></tr>
                    <tr><td class="bold">13/06</td><td class="text-center">1</td><td class="text-center">1</td><td class="text-center">2</td><td class="text-center">2</td><td class="text-center bold">6</td><td class="text-center">0.2%</td></tr>
                    <tr><td class="bold">14/06</td><td class="text-center">2</td><td class="text-center">2</td><td class="text-center">2</td><td class="text-center">1</td><td class="text-center bold">7</td><td class="text-center">0.3%</td></tr>
                    <tr><td class="bold">15/06</td><td class="text-center">39</td><td class="text-center">38</td><td class="text-center">38</td><td class="text-center">39</td><td class="text-center bold">154</td><td class="text-center">6.0%</td></tr>
                    <tr><td class="bold">16/06</td><td class="text-center">9</td><td class="text-center">10</td><td class="text-center">10</td><td class="text-center">9</td><td class="text-center bold">38</td><td class="text-center">1.5%</td></tr>
                    <tr><td class="bold">17/06</td><td class="text-center">9</td><td class="text-center">9</td><td class="text-center">9</td><td class="text-center">9</td><td class="text-center bold">36</td><td class="text-center">1.4%</td></tr>
                    <tr><td class="bold">18/06</td><td class="text-center">371</td><td class="text-center">371</td><td class="text-center">370</td><td class="text-center">371</td><td class="text-center bold">1,483</td><td class="text-center">58.2%</td></tr>
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">638</td><td class="text-center bold">638</td><td class="text-center bold">637</td><td class="text-center bold">637</td><td class="text-center bold">2,550</td><td class="text-center bold">100%</td></tr>
                </tbody>
            </table>
            <p style="font-size:12px; color:#888;">El 87.1% de los vencimientos se concentra en 10/06 (29%) y 18/06 (58%).</p>
        </div>

        <div class="section">
            <h2><span class="num">F2</span> Resultado del experimento (conclusiones)</h2>
            <div class="note" style="background:#e8f5e9; border-left-color:#28a745; color:#1b5e20;">
                <strong>Conclusion Fase 2:</strong> No se encontraron diferencias significativas entre los grupos de tratamiento. QR BBVA demostro adopcion consistente en todos los segmentos, lo que sustento la decision de eliminar grupos de control y desplegar QR a toda la base desde julio 2026.
            </div>
        </div>

        <!-- ===== FASE 1: PILOTO MAYO (historico) ===== -->
        <hr class="divider">
        <p class="section-title">Fase 1: Piloto (Mayo 2026)</p>
        <p class="section-subtitle">232 clientes con cuota 25/05 &mdash; Corte 07/06 (13 dias) &mdash; Sin campana de cobranza</p>

        <div class="kpi-grid" style="margin-top:24px;">
            <div class="kpi-card"><div class="kpi-label">Cobranza (de 232)</div><div class="kpi-value">91%</div><div class="kpi-sub">211 pagaron, 21 pendientes</div></div>
            <div class="kpi-card"><div class="kpi-label">Pagaron con QR</div><div class="kpi-value">30.8%</div><div class="kpi-sub">65 de 211 pagos</div></div>
            <div class="kpi-card"><div class="kpi-label">Interactuaron con QR</div><div class="kpi-value">109</div><div class="kpi-sub">65 QR + 38 migraron + 2 sin pago</div></div>
            <div class="kpi-card"><div class="kpi-label">Errores tecnicos</div><div class="kpi-value">0%</div><div class="kpi-sub">116 QRs sin fallas</div></div>
        </div>

        <div class="section">
            <h2><span class="num">P1</span> Distribucion de pagos (piloto 13 dias)</h2>
            <p style="color:#666; font-size:14px; margin-bottom:20px;">De 281 con cuota mayo, 49 pagaron antes del QR. Base efectiva: 232. De esos, 211 pagaron (91%).</p>
            <table style="margin-bottom:20px;">
                <thead><tr><th>Categoria</th><th class="text-center">Clientes</th><th class="text-center">% de 211</th><th>Detalle</th></tr></thead>
                <tbody>
                    <tr><td class="bold" style="color:#28a745;">Pagaron con QR</td><td class="text-center bold" style="color:#28a745;">65</td><td class="text-center bold" style="color:#28a745;">30.8%</td><td style="color:#666;">Generaron QR y completaron pago</td></tr>
                    <tr><td class="bold" style="color:#e8590c;">QR &rarr; migraron a otro</td><td class="text-center bold" style="color:#e8590c;">38</td><td class="text-center bold" style="color:#e8590c;">18.0%</td><td style="color:#666;">Monnet (35), MP (1), Recaudo (2)</td></tr>
                    <tr><td class="bold">Monnet directo</td><td class="text-center bold">90</td><td class="text-center">42.7%</td><td style="color:#666;">Sin interaccion con QR</td></tr>
                    <tr><td class="bold">Mercado Pago directo</td><td class="text-center bold">11</td><td class="text-center">5.2%</td><td style="color:#666;">Sin interaccion con QR</td></tr>
                    <tr><td class="bold">BBVA Recaudo directo</td><td class="text-center bold">7</td><td class="text-center">3.3%</td><td style="color:#666;">Pago via recaudo bancario</td></tr>
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">211</td><td class="text-center bold">100%</td><td></td></tr>
                </tbody>
            </table>
            <div style="margin-bottom:12px;"><div style="height:36px; background:#f0f2f5; border-radius:6px; overflow:hidden; display:flex;">
                <div style="width:30.8%; background:#28a745; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600; border-radius:6px 0 0 6px;">QR 30.8%</div>
                <div style="width:18.0%; background:#ffc107; display:flex; align-items:center; justify-content:center; color:#665200; font-size:11px; font-weight:600;">QR&rarr;otro 18%</div>
                <div style="width:42.7%; background:#004481; display:flex; align-items:center; justify-content:center; color:white; font-size:11px; font-weight:600;">Monnet 42.7%</div>
                <div style="width:8.5%; background:#6f42c1; display:flex; align-items:center; justify-content:center; color:white; font-size:10px; font-weight:600; border-radius:0 6px 6px 0;">MP+Rec 8.5%</div>
            </div></div>
        </div>

        <div class="section">
            <h2><span class="num">P2</span> Segmentacion por monto (piloto)</h2>
            <table style="margin-bottom:24px;">
                <thead><tr><th>Rango</th><th class="text-center">Base</th><th class="text-center">Pagaron</th><th class="text-center">% Cobranza</th><th class="text-center">Via QR</th><th class="text-center">% QR</th><th class="text-center">Conv. QR</th></tr></thead>
                <tbody>
                    <tr><td class="bold">S/ 0-50</td><td class="text-center">8</td><td class="text-center">6</td><td class="text-center">75.0%</td><td class="text-center bold" style="color:#28a745;">2</td><td class="text-center">33.3%</td><td class="text-center bold">100%</td></tr>
                    <tr><td class="bold">S/ 51-150</td><td class="text-center">79</td><td class="text-center">71</td><td class="text-center">89.9%</td><td class="text-center bold" style="color:#28a745;">27</td><td class="text-center bold">38.0%</td><td class="text-center bold">71.1%</td></tr>
                    <tr><td class="bold">S/ 151-250</td><td class="text-center">96</td><td class="text-center">90</td><td class="text-center">93.8%</td><td class="text-center bold" style="color:#28a745;">20</td><td class="text-center">22.2%</td><td class="text-center">50.0%</td></tr>
                    <tr><td class="bold">S/ 251+</td><td class="text-center">49</td><td class="text-center">44</td><td class="text-center">89.8%</td><td class="text-center bold" style="color:#28a745;">16</td><td class="text-center bold">36.4%</td><td class="text-center bold">69.6%</td></tr>
                    <tr style="background:#f8f9fa;"><td class="bold">Total</td><td class="text-center bold">232</td><td class="text-center bold">211</td><td class="text-center bold">90.9%</td><td class="text-center bold" style="color:#28a745;">65</td><td class="text-center bold">30.8%</td><td class="text-center bold">59.6%</td></tr>
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2><span class="num">P3</span> Banco seleccionado (piloto)</h2>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Monnet (125 clientes)</h3>
            <table style="margin-bottom:24px;">
                <thead><tr><th>Banco</th><th class="text-center">Clientes</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    <tr><td class="bold" style="color:#6f21a8;">Yape</td><td class="text-center bold">102</td><td class="text-center bold">81.0%</td></tr>
                    <tr><td class="bold" style="color:#004481;">BCP</td><td class="text-center bold">16</td><td class="text-center">12.7%</td></tr>
                    <tr><td class="bold" style="color:#0066b3;">BBVA</td><td class="text-center bold">4</td><td class="text-center">3.2%</td></tr>
                    <tr><td class="bold" style="color:#28a745;">Interbank</td><td class="text-center bold">4</td><td class="text-center">3.2%</td></tr>
                </tbody>
            </table>
            <h3 style="font-size:14px; color:#004481; margin-bottom:12px;">Mercado Pago (12 clientes)</h3>
            <table>
                <thead><tr><th>Metodo</th><th class="text-center">Clientes</th><th class="text-center">%</th></tr></thead>
                <tbody>
                    <tr><td class="bold">PagoEfectivo</td><td class="text-center bold">6</td><td class="text-center">50.0%</td></tr>
                    <tr><td class="bold" style="color:#6f21a8;">Yape</td><td class="text-center bold">3</td><td class="text-center">25.0%</td></tr>
                    <tr><td class="bold" style="color:#004481;">BCP</td><td class="text-center bold">2</td><td class="text-center">16.7%</td></tr>
                    <tr><td class="bold" style="color:#28a745;">Interbank</td><td class="text-center bold">1</td><td class="text-center">8.3%</td></tr>
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2><span class="num">P4</span> Resumen ejecutivo del piloto</h2>
            <table>
                <thead><tr><th>Metrica</th><th class="text-center">24h</th><th class="text-center">13 dias</th></tr></thead>
                <tbody>
                    <tr><td class="bold">Pagos totales</td><td class="text-center">64</td><td class="text-center bold">211</td></tr>
                    <tr><td class="bold">Pagos via QR</td><td class="text-center">14</td><td class="text-center bold">65</td></tr>
                    <tr><td class="bold">% QR sobre pagos</td><td class="text-center">21.9%</td><td class="text-center bold">30.8%</td></tr>
                    <tr><td class="bold">% cobranza</td><td class="text-center">25.8%</td><td class="text-center bold">90.9%</td></tr>
                    <tr><td class="bold">Errores tecnicos</td><td class="text-center">0</td><td class="text-center bold">0</td></tr>
                </tbody>
            </table>
            <div class="note" style="background:#e8f5e9; border-left-color:#28a745; color:#1b5e20;">
                <strong>Conclusion del piloto:</strong> QR BBVA capturo <strong>30.8%</strong> de los pagos con <strong>cero errores</strong>. El 48.8% de pagadores interactuo con el QR. Estos resultados sustentaron la escalacion a Fase 2 con 2,550 clientes.
            </div>
        </div>

        <div class="section">
            <h2><span class="num">N</span> Notas metodologicas</h2>
            <div class="note"><strong>Piloto (mayo 2026):</strong> 300 solicitudes originales &rarr; 281 con cuota mayo &rarr; 49 pagaron antes del QR &rarr; Base efectiva: 232. Solo clientes sin campana de cobranza.</div>
            <div class="note" style="margin-top:12px;"><strong>Experimento (junio 2026):</strong> 2,550 clientes con cuotas 10-18 jun. 4 grupos (Control, A, B, C) estratificados por fecha de vencimiento. Concluido sin diferencias significativas entre grupos.</div>
            <div class="note" style="margin-top:12px;"><strong>Operacion (julio 2026+):</strong> QR desplegado a toda la base sin grupos de control. Dashboard muestra metricas del mes en curso automaticamente.</div>
        </div>

        <div class="footer">
            BaldeCash &mdash; Dashboard QR BBVA &mdash; {now.strftime('%d/%m/%Y %H:%M:%S')}
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
            import traceback
            self.send_response(500)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(f'''<!DOCTYPE html><html><body style="font-family:sans-serif;padding:40px;">
                <h1>Error generando dashboard</h1><pre>{traceback.format_exc()}</pre>
                <p>Verifica las variables de entorno DB_HOST, DB_USER, DB_PASSWORD, DB_NAME.</p>
            </body></html>'''.encode('utf-8'))
