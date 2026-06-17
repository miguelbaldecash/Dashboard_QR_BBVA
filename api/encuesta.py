"""Encuesta QR BBVA - Serverless function."""
import os, json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode
from datetime import datetime

WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')  # Google Sheets Apps Script URL

FORM_HTML = '''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Encuesta QR BBVA - BaldeCash</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; color: #1a1a2e; min-height: 100vh; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #004481, #0066b3); color: white; padding: 32px 24px; border-radius: 16px; margin-bottom: 24px; text-align: center; }
        .header h1 { font-size: 22px; font-weight: 700; margin-bottom: 8px; }
        .header p { font-size: 14px; opacity: 0.85; line-height: 1.5; }
        .header .time { font-size: 12px; opacity: 0.6; margin-top: 12px; }
        .card { background: white; border-radius: 12px; padding: 24px; margin-bottom: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .card h2 { font-size: 16px; font-weight: 600; color: #004481; margin-bottom: 6px; }
        .card .required { color: #e8590c; font-size: 12px; }
        .card p.desc { font-size: 13px; color: #666; margin-bottom: 16px; line-height: 1.4; }
        .q-number { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; background: #004481; color: white; border-radius: 50%; font-size: 12px; font-weight: 700; margin-right: 8px; flex-shrink: 0; }
        .radio-group { display: flex; gap: 12px; margin-top: 8px; }
        .radio-option { flex: 1; }
        .radio-option input { display: none; }
        .radio-option label { display: block; padding: 14px; text-align: center; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .radio-option input:checked + label { border-color: #004481; background: #e3f0ff; color: #004481; }
        .radio-option label:hover { border-color: #90caf9; }
        textarea { width: 100%; min-height: 80px; padding: 14px; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 14px; font-family: inherit; resize: vertical; transition: border-color 0.2s; }
        textarea:focus { outline: none; border-color: #004481; }
        .likert-container { margin-top: 12px; }
        .likert-labels { display: flex; justify-content: space-between; font-size: 11px; color: #888; margin-bottom: 8px; }
        .likert-options { display: flex; gap: 8px; }
        .likert-options .radio-option { flex: 1; }
        .likert-options .radio-option label { padding: 12px 4px; font-size: 16px; }
        .section-divider { text-align: center; color: #aaa; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin: 8px 0; }
        .submit-btn { width: 100%; padding: 16px; background: linear-gradient(135deg, #004481, #0066b3); color: white; border: none; border-radius: 12px; font-size: 16px; font-weight: 700; cursor: pointer; transition: transform 0.1s, box-shadow 0.2s; box-shadow: 0 4px 12px rgba(0,68,129,0.3); }
        .submit-btn:hover { transform: translateY(-1px); box-shadow: 0 6px 16px rgba(0,68,129,0.4); }
        .submit-btn:active { transform: translateY(0); }
        .progress { height: 4px; background: #e0e0e0; border-radius: 2px; margin-bottom: 24px; overflow: hidden; }
        .progress-bar { height: 100%; background: linear-gradient(90deg, #004481, #28a745); border-radius: 2px; transition: width 0.3s; }
        .footer { text-align: center; padding: 20px; font-size: 11px; color: #aaa; }
        .hidden { display: none; }
        .branch-notice { background: #fff8e1; border-left: 4px solid #ffc107; padding: 12px 16px; border-radius: 0 8px 8px 0; font-size: 13px; color: #665200; margin-bottom: 16px; }
        @media (max-width: 480px) {
            .container { padding: 12px; }
            .header { padding: 24px 16px; }
            .card { padding: 20px 16px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Encuesta sobre pagos con QR</h1>
            <p>Queremos mejorar tu experiencia de pago. Esta encuesta toma menos de 2 minutos.</p>
            <div class="time">BaldeCash &bull; Junio 2026</div>
        </div>

        <div class="progress"><div class="progress-bar" id="progressBar" style="width: 0%"></div></div>

        <form id="surveyForm" method="POST" action="/api/encuesta">

            <!-- P1: Filtro -->
            <div class="card" id="q1-card">
                <h2><span class="q-number">1</span> Viste la opcion de pagar con QR BBVA?<span class="required"> *</span></h2>
                <p class="desc">Cuando entraste a pagar tu cuota, aparece un codigo QR de BBVA como opcion de pago.</p>
                <div class="radio-group">
                    <div class="radio-option">
                        <input type="radio" id="q1_si" name="q1_vio_qr" value="Si" required onchange="handleQ1()">
                        <label for="q1_si">Si, lo vi</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" id="q1_no" name="q1_vio_qr" value="No" required onchange="handleQ1()">
                        <label for="q1_no">No lo vi</label>
                    </div>
                    <div class="radio-option">
                        <input type="radio" id="q1_nosure" name="q1_vio_qr" value="No estoy seguro" required onchange="handleQ1()">
                        <label for="q1_nosure">No recuerdo</label>
                    </div>
                </div>
            </div>

            <!-- Branch: No vio QR -->
            <div class="branch-notice hidden" id="branch-no">
                <strong>Entendido.</strong> Como no viste el QR, saltamos a la ultima pregunta. Gracias por tu tiempo.
            </div>

            <!-- P2: Razon principal (abierta) -->
            <div class="card hidden" id="q2-card">
                <h2><span class="q-number">2</span> Cual fue la razon principal por la que no pagaste con el QR?<span class="required"> *</span></h2>
                <p class="desc">Cuentanos con tus propias palabras, no hay respuestas incorrectas.</p>
                <textarea name="q2_razon" placeholder="Ej: No tengo cuenta BBVA, me parecio dificil, prefiero Yape..." id="q2_input"></textarea>
            </div>

            <!-- Seccion Likert -->
            <div class="hidden" id="likert-section">
                <div class="section-divider" style="margin-top:8px; margin-bottom:16px;">Indica que tan de acuerdo estas</div>

                <!-- P3 -->
                <div class="card">
                    <h2><span class="q-number">3</span> No tengo cuenta en BBVA</h2>
                    <div class="likert-container">
                        <div class="likert-labels"><span>Muy en desacuerdo</span><span>Muy de acuerdo</span></div>
                        <div class="likert-options">
                            <div class="radio-option"><input type="radio" id="q3_1" name="q3_sin_cuenta" value="1"><label for="q3_1">1</label></div>
                            <div class="radio-option"><input type="radio" id="q3_2" name="q3_sin_cuenta" value="2"><label for="q3_2">2</label></div>
                            <div class="radio-option"><input type="radio" id="q3_3" name="q3_sin_cuenta" value="3"><label for="q3_3">3</label></div>
                            <div class="radio-option"><input type="radio" id="q3_4" name="q3_sin_cuenta" value="4"><label for="q3_4">4</label></div>
                            <div class="radio-option"><input type="radio" id="q3_5" name="q3_sin_cuenta" value="5"><label for="q3_5">5</label></div>
                        </div>
                    </div>
                </div>

                <!-- P4 -->
                <div class="card">
                    <h2><span class="q-number">4</span> Me parecio complicado el proceso del QR</h2>
                    <div class="likert-container">
                        <div class="likert-labels"><span>Muy en desacuerdo</span><span>Muy de acuerdo</span></div>
                        <div class="likert-options">
                            <div class="radio-option"><input type="radio" id="q4_1" name="q4_complicado" value="1"><label for="q4_1">1</label></div>
                            <div class="radio-option"><input type="radio" id="q4_2" name="q4_complicado" value="2"><label for="q4_2">2</label></div>
                            <div class="radio-option"><input type="radio" id="q4_3" name="q4_complicado" value="3"><label for="q4_3">3</label></div>
                            <div class="radio-option"><input type="radio" id="q4_4" name="q4_complicado" value="4"><label for="q4_4">4</label></div>
                            <div class="radio-option"><input type="radio" id="q4_5" name="q4_complicado" value="5"><label for="q4_5">5</label></div>
                        </div>
                    </div>
                </div>

                <!-- P5 -->
                <div class="card">
                    <h2><span class="q-number">5</span> No estaba seguro de que el pago se registrara correctamente</h2>
                    <div class="likert-container">
                        <div class="likert-labels"><span>Muy en desacuerdo</span><span>Muy de acuerdo</span></div>
                        <div class="likert-options">
                            <div class="radio-option"><input type="radio" id="q5_1" name="q5_confianza" value="1"><label for="q5_1">1</label></div>
                            <div class="radio-option"><input type="radio" id="q5_2" name="q5_confianza" value="2"><label for="q5_2">2</label></div>
                            <div class="radio-option"><input type="radio" id="q5_3" name="q5_confianza" value="3"><label for="q5_3">3</label></div>
                            <div class="radio-option"><input type="radio" id="q5_4" name="q5_confianza" value="4"><label for="q5_4">4</label></div>
                            <div class="radio-option"><input type="radio" id="q5_5" name="q5_confianza" value="5"><label for="q5_5">5</label></div>
                        </div>
                    </div>
                </div>

                <!-- P6 -->
                <div class="card">
                    <h2><span class="q-number">6</span> Prefiero pagar por Yape porque ya lo conozco</h2>
                    <div class="likert-container">
                        <div class="likert-labels"><span>Muy en desacuerdo</span><span>Muy de acuerdo</span></div>
                        <div class="likert-options">
                            <div class="radio-option"><input type="radio" id="q6_1" name="q6_habito" value="1"><label for="q6_1">1</label></div>
                            <div class="radio-option"><input type="radio" id="q6_2" name="q6_habito" value="2"><label for="q6_2">2</label></div>
                            <div class="radio-option"><input type="radio" id="q6_3" name="q6_habito" value="3"><label for="q6_3">3</label></div>
                            <div class="radio-option"><input type="radio" id="q6_4" name="q6_habito" value="4"><label for="q6_4">4</label></div>
                            <div class="radio-option"><input type="radio" id="q6_5" name="q6_habito" value="5"><label for="q6_5">5</label></div>
                        </div>
                    </div>
                </div>

                <!-- P7 -->
                <div class="card">
                    <h2><span class="q-number">7</span> Me preocupaba que me cobraran algo adicional</h2>
                    <div class="likert-container">
                        <div class="likert-labels"><span>Muy en desacuerdo</span><span>Muy de acuerdo</span></div>
                        <div class="likert-options">
                            <div class="radio-option"><input type="radio" id="q7_1" name="q7_costo" value="1"><label for="q7_1">1</label></div>
                            <div class="radio-option"><input type="radio" id="q7_2" name="q7_costo" value="2"><label for="q7_2">2</label></div>
                            <div class="radio-option"><input type="radio" id="q7_3" name="q7_costo" value="3"><label for="q7_3">3</label></div>
                            <div class="radio-option"><input type="radio" id="q7_4" name="q7_costo" value="4"><label for="q7_4">4</label></div>
                            <div class="radio-option"><input type="radio" id="q7_5" name="q7_costo" value="5"><label for="q7_5">5</label></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- P8: Percepcion -->
            <div class="card hidden" id="q8-card">
                <h2><span class="q-number">8</span> Como percibes el uso de QR para pagar tus cuotas?</h2>
                <p class="desc">Dejanos tu comentario, opinion o sugerencia sobre esta tecnologia.</p>
                <textarea name="q8_percepcion" placeholder="Ej: Me parece buena idea pero..., Creo que seria mejor si..." id="q8_input"></textarea>
            </div>

            <!-- P9: Cierre -->
            <div class="card" id="q9-card">
                <h2><span class="q-number">9</span> Que necesitarias para animarte a pagar con QR la proxima vez?</h2>
                <p class="desc">Tu respuesta nos ayuda a mejorar.</p>
                <textarea name="q9_mejora" placeholder="Ej: Un tutorial, que acepte otros bancos, mas confianza..." id="q9_input"></textarea>
            </div>

            <input type="hidden" name="timestamp" id="timestamp">
            <button type="submit" class="submit-btn" id="submitBtn">Enviar encuesta</button>
        </form>

        <div class="footer">Tus respuestas son anonimas &bull; BaldeCash 2026</div>
    </div>

    <script>
        function handleQ1() {
            const val = document.querySelector('input[name="q1_vio_qr"]:checked').value;
            const showFull = val === 'Si';
            const showBranch = val !== 'Si';

            document.getElementById('branch-no').classList.toggle('hidden', !showBranch);
            document.getElementById('q2-card').classList.toggle('hidden', !showFull);
            document.getElementById('likert-section').classList.toggle('hidden', !showFull);
            document.getElementById('q8-card').classList.toggle('hidden', !showFull);

            // Q9 siempre visible
            updateProgress();
        }

        // Progress bar
        function updateProgress() {
            const total = document.querySelectorAll('.card:not(.hidden)').length;
            const answered = document.querySelectorAll('input[type="radio"]:checked').length +
                [...document.querySelectorAll('textarea')].filter(t => !t.closest('.hidden') && t.value.trim()).length;
            const pct = Math.min(100, Math.round(answered / total * 100));
            document.getElementById('progressBar').style.width = pct + '%';
        }

        document.querySelectorAll('input, textarea').forEach(el => {
            el.addEventListener('change', updateProgress);
            el.addEventListener('input', updateProgress);
        });

        document.getElementById('surveyForm').addEventListener('submit', function(e) {
            document.getElementById('timestamp').value = new Date().toISOString();
        });
    </script>
</body>
</html>'''

THANKS_HTML = '''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gracias - BaldeCash</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
        .card { background: white; border-radius: 16px; padding: 48px 32px; text-align: center; max-width: 500px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }
        .check { width: 80px; height: 80px; background: #d4edda; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 24px; }
        .check svg { width: 40px; height: 40px; color: #28a745; }
        h1 { font-size: 24px; color: #1a1a2e; margin-bottom: 12px; }
        p { font-size: 15px; color: #666; line-height: 1.6; }
        .footer { margin-top: 32px; font-size: 12px; color: #aaa; }
    </style>
</head>
<body>
    <div class="card">
        <div class="check">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
        </div>
        <h1>Gracias por tu respuesta!</h1>
        <p>Tu opinion nos ayuda a mejorar la experiencia de pago. Puedes cerrar esta ventana.</p>
        <div class="footer">BaldeCash 2026</div>
    </div>
</body>
</html>'''


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(FORM_HTML.encode('utf-8'))

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        data = parse_qs(body)
        # Flatten
        flat = {k: v[0] if len(v) == 1 else v for k, v in data.items()}
        flat['submitted_at'] = datetime.now().isoformat()

        # Log to Vercel (visible in Logs tab)
        print(f"SURVEY_RESPONSE: {json.dumps(flat, ensure_ascii=False)}")

        # Forward to webhook if configured
        if WEBHOOK_URL:
            try:
                from urllib.request import Request, urlopen
                req = Request(WEBHOOK_URL, data=json.dumps(flat).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
                urlopen(req, timeout=5)
            except Exception as e:
                print(f"Webhook error: {e}")

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(THANKS_HTML.encode('utf-8'))
