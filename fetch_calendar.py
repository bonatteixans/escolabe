"""
La Salle GVDasa - Extrator de Calendário Semanal v2
Faz login via Playwright, usa fetch() do browser para chamar a API REST.
Busca cronograma por ComponenteCurricular (dados completos do mês).
"""

import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path

from playwright.async_api import async_playwright, Page

LOGIN_URL   = "https://lasalle.aluno.gvdasa.com.br"
API_BASE    = "https://api.gvdasa.com.br/portal/api/v1"
OUTPUT_FILE = Path(__file__).parent / "index.html"

MATRICULA = os.environ["GVDASA_MATRICULA"]
SENHA     = os.environ["GVDASA_SENHA"]


# ── Login ──────────────────────────────────────────────────────────────────────
async def fazer_login(page: Page) -> None:
    print("🔐 Abrindo portal...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(2)
    await page.wait_for_selector('input:not([type="hidden"]):not([type="password"])', timeout=20_000, state="visible")
    await page.fill('input:not([type="hidden"]):not([type="password"])', MATRICULA)
    await asyncio.sleep(0.3)
    await page.fill('input[type="password"]', SENHA)
    await asyncio.sleep(0.3)
    await page.click('button[type="submit"]')
    await page.wait_for_url("**/pagina-inicial**", timeout=45_000)
    await page.wait_for_load_state("networkidle", timeout=20_000)
    print(f"✅ Login OK!")


# ── API via fetch() do browser ─────────────────────────────────────────────────
async def api_get(page: Page, url: str, params: dict = None):
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{url}?{qs}"
    else:
        full_url = url

    result = await page.evaluate(f"""async () => {{
        try {{
            const resp = await fetch("{full_url}", {{
                method: "GET",
                headers: {{ "Accept": "application/json" }},
                credentials: "include"
            }});
            const text = await resp.text();
            return {{ status: resp.status, body: text }};
        }} catch(e) {{
            return {{ status: 0, body: e.toString() }};
        }}
    }}""")

    if result["status"] == 200:
        try:
            return json.loads(result["body"])
        except Exception:
            return None
    print(f"   ⚠️ {result['status']} — {full_url[-70:]}: {result['body'][:100]}")
    return None


# ── Dados do usuário ───────────────────────────────────────────────────────────
async def fetch_contexto(page: Page) -> dict:
    data = await api_get(page, f"{API_BASE}/ContextoUsuario")
    usuario = data.get("usuario", {}) if data else {}
    uid  = usuario.get("id") or usuario.get("idPessoa")
    nome = usuario.get("nome") or ""
    print(f"   Usuário: {nome} (id={uid})")
    if not uid:
        raise RuntimeError(f"Id não encontrado: {data}")
    return {"idResponsavel": int(uid), "nome": nome}


async def fetch_enturmacoes(page: Page, id_aluno: int) -> dict:
    data = await api_get(page, f"{API_BASE}/Alunos/Enturmacoes", {"idPessoa": id_aluno})
    if not data or not isinstance(data, list):
        raise RuntimeError(f"Enturmações vazias: {data}")
    print(f"   Raw enturmacao: {json.dumps(data[0])[:300]}")
    e = data[0]
    # Tenta diferentes nomes de campo usados pelo GVDasa
    id_enturmacao = e.get("idEnturmacao") or e.get("id") or e.get("idMatricula")
    # idTurma pode estar em vários campos
    id_turma = (e.get("idTurma") or 
                e.get("idTurmaAtual") or
                e.get("turmaId") or
                (e.get("turmaObj", {}) or {}).get("id") or
                0)
    # Se não encontrou, imprime todo o objeto para diagnóstico
    if not id_turma:
        print(f"   ⚠️ idTurma não encontrado, campos disponíveis: {list(e.keys())}")
    nome_aluno_raw = e.get("nomeAluno") or e.get("nome") or e.get("nomeEstudante") or "Bernardo"
    turma_raw = e.get("descricaoTurma") or e.get("turma") or e.get("descricao") or ""
    print(f"   Turma: {turma_raw} | Aluno: {nome_aluno_raw}")
    if not id_enturmacao:
        raise RuntimeError(f"idEnturmacao não encontrado: {e}")
    return {
        "idEnturmacao": id_enturmacao,
        "idTurma":      id_turma or 0,
        "turma":        str(turma_raw) if not isinstance(turma_raw, str) else turma_raw,
        "nomeAluno":    nome_aluno_raw,
    }


async def fetch_componentes(page: Page, id_enturmacao: int) -> list:
    data = await api_get(page, f"{API_BASE}/Enturmacoes/ComponentesCurriculares", {"idEnturmacao": id_enturmacao})
    if not data or not isinstance(data, list):
        print(f"   ⚠️ Componentes raw: {data}")
        return []
    print(f"   {len(data)} componentes curriculares encontrados")
    if data:
        print(f"   Exemplo componente: {json.dumps(data[0])[:300]}")
    return data


# ── Cronograma por componente (mês inteiro) ────────────────────────────────────
async def fetch_componente_mes(page: Page, id_enturmacao: int, id_turma: int, id_componente: int, mes: int, ano: int) -> list:
    data = await api_get(page, f"{API_BASE}/Enturmacoes/Cronograma/ComponenteCurricular", {
        "idEnturmacao":          id_enturmacao,
        "idComponenteCurricular": id_componente,
        "mes":                   mes,
        "ano":                   ano,
        "idTurma":               id_turma,
    })
    return data if isinstance(data, list) else []


# ── Coleta dados de múltiplos meses ───────────────────────────────────────────
async def fetch_todos_dados(page: Page, enturmacao: dict, componentes: list) -> dict:
    """
    Retorna dict: { "YYYY-MM-DD": { id_componente: aula_data } }
    Busca 2 meses atrás + mês atual + 2 meses à frente.
    """
    hoje = date.today()
    meses = []
    for delta in range(-2, 3):
        m = hoje.month + delta
        a = hoje.year
        while m < 1: m += 12; a -= 1
        while m > 12: m -= 12; a += 1
        meses.append((m, a))

    por_data: dict = {}

    for comp in componentes:
        id_comp     = comp.get("idComponenteCurricular") or comp.get("id")
        nome_comp   = comp.get("descricaoComponenteCurricular") or comp.get("descricao") or comp.get("nome") or f"Componente {id_comp}"
        prof_comp   = comp.get("nomeProfessor") or ""
        if not id_comp:
            continue
        print(f"   Buscando: {nome_comp}")

        for (mes, ano) in meses:
            aulas = await fetch_componente_mes(page, enturmacao["idEnturmacao"], enturmacao["idTurma"], id_comp, mes, ano)
            if aulas:
                print(f"     ✅ {nome_comp} {mes}/{ano}: {len(aulas)} aulas")
            for aula in aulas:
                data_str = aula.get("dataAula", "")[:10]
                if not data_str:
                    continue
                if data_str not in por_data:
                    por_data[data_str] = []
                por_data[data_str].append({
                    "idComponente":  id_comp,
                    "disciplina":    nome_comp,
                    "professor":     prof_comp,
                    "horario":       aula.get("horario", ""),
                    "avaliacao":     aula.get("avaliacao", False),
                    "descAvaliacao": aula.get("descricaoAvaliacao") or "",
                    "previsto":      next((c["conteudo"] for c in aula.get("conteudoAula", []) if "previsto"  in c["titulo"].lower()), ""),
                    "realizado":     next((c["conteudo"] for c in aula.get("conteudoAula", []) if "realizado" in c["titulo"].lower()), ""),
                    "temas":         [t.get("descricao","") for t in aula.get("bnccTemas", []) if t.get("descricao")],
                    "cancelada":     aula.get("aulaCancelada", False),
                })

    return por_data


# ── Gerador de HTML ────────────────────────────────────────────────────────────
def render_html(por_data: dict, enturmacao: dict) -> str:
    import datetime, json as _json

    hoje          = date.today()
    nome_raw = enturmacao.get("nomeAluno", "Bernardo") or "Bernardo"
    nome_aluno = nome_raw.split()[0] if nome_raw.strip() else "Bernardo"
    gerado_em     = datetime.datetime.now().strftime("%d/%m/%Y às %H:%M")

    # Serializa dados para JS
    dados_js = _json.dumps(por_data, ensure_ascii=False)

    CORES = [
        "#4f46e5","#0891b2","#059669","#d97706","#dc2626",
        "#7c3aed","#be185d","#0369a1","#15803d","#b45309",
        "#0e7490","#65a30d","#9333ea","#c2410c","#0f766e",
    ]

    # Monta mapa de cores por disciplina
    disciplinas = sorted(set(
        aula["disciplina"]
        for aulas in por_data.values()
        for aula in aulas
    ))
    cor_map = {d: CORES[i % len(CORES)] for i, d in enumerate(disciplinas)}
    cor_map_js = _json.dumps(cor_map, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bernardo — Programação Semanal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* ── Reset & Base ── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --azul:      #0f2e99;
  --azul-dark: #0a1f6b;
  --azul-tab:  #0c2580;
  --verm:      #E24B4A;
  --verm-bg:   #FCEBEB;
  --verm-brd:  #F09595;
  --hoje-bg:   #EFF6FF;
  --hoje-num:  #0C447C;
  --hoje-lbl:  #185FA5;
  --bg:        #F4F6FB;
  --surface:   #FFFFFF;
  --border:    rgba(0,0,0,0.08);
  --text:      #1a1f36;
  --text-2:    #64748b;
  --text-3:    #94a3b8;
  --font:      'DM Sans', system-ui, sans-serif;
  --mono:      'DM Mono', monospace;
}}
html {{ font-family: var(--font); background: var(--bg); color: var(--text); font-size: 14px; }}
body {{ min-height: 100vh; display: flex; flex-direction: column; }}

/* ── Topbar ── */
.topbar {{
  background: var(--azul);
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
}}
.topbar-name {{ color: #fff; font-size: 16px; font-weight: 600; letter-spacing: -0.2px; }}
.topbar-date {{ color: rgba(255,255,255,0.65); font-size: 11px; margin-top: 2px; }}
.week-nav {{ display: flex; align-items: center; gap: 8px; flex-shrink: 0; }}
.week-btn {{
  all: unset;
  background: rgba(255,255,255,0.18);
  border: 1.5px solid rgba(255,255,255,0.6);
  color: #fff;
  border-radius: 6px;
  padding: 5px 13px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  font-family: var(--font);
  transition: background 0.15s;
}}
.week-btn:hover {{ background: rgba(255,255,255,0.28); }}
.week-label {{ color: #fff; font-size: 12px; font-weight: 500; min-width: 100px; text-align: center; }}

/* ── Tabs ── */
.tabs {{
  background: var(--azul-tab);
  display: flex;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  flex-shrink: 0;
}}
.tab {{
  padding: 9px 18px;
  font-size: 12px;
  color: rgba(255,255,255,0.55);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  font-weight: 400;
  transition: color 0.15s;
  user-select: none;
}}
.tab:hover {{ color: rgba(255,255,255,0.85); }}
.tab.active {{ color: #fff; border-bottom-color: var(--verm); font-weight: 500; }}

/* ── Views ── */
.view {{ display: none; flex: 1; }}
.view.active {{ display: flex; flex-direction: column; flex: 1; }}

/* ── Grid semanal ── */
.week-grid {{
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 1px;
  background: var(--border);
  flex: 1;
}}
.day-col {{ background: var(--surface); display: flex; flex-direction: column; }}
.day-head {{
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}}
.day-head.today {{ background: var(--hoje-bg); }}
.day-head.today .day-name {{ color: var(--hoje-lbl); }}
.day-head.today .day-num  {{ color: var(--hoje-num); }}
.day-name {{ font-size: 10px; font-weight: 500; color: var(--text-2); text-transform: uppercase; letter-spacing: 0.6px; }}
.day-num  {{ font-size: 22px; font-weight: 600; color: var(--text); line-height: 1.1; font-variant-numeric: tabular-nums; }}
.day-body {{ padding: 8px; display: flex; flex-direction: column; gap: 5px; flex: 1; }}

/* ── Prova banner ── */
.prova-banner {{
  background: var(--verm-bg);
  border: 1.5px solid var(--verm);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}}
.prova-circle {{
  width: 20px; height: 20px;
  background: var(--verm);
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 1px;
  font-size: 12px; font-weight: 700; color: #fff; line-height: 1;
}}
.prova-nome {{ font-size: 12px; font-weight: 600; color: #791F1F; }}
.prova-sub  {{ font-size: 10px; color: #A32D2D; margin-top: 2px; }}

/* ── Matéria (sanfona) ── */
.materia {{
  border-radius: 7px;
  overflow: hidden;
  border: 1px solid var(--border);
  background: var(--surface);
}}
.mat-head {{
  padding: 7px 9px;
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
}}
.mat-head:hover {{ background: rgba(0,0,0,0.02); }}
.mat-dot  {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.mat-nome {{ font-size: 11px; font-weight: 500; color: var(--text); flex: 1; line-height: 1.3; }}
.mat-hora {{ font-size: 10px; color: var(--text-3); font-family: var(--mono); white-space: nowrap; }}
.mat-arr  {{ font-size: 9px; color: var(--text-3); margin-left: 2px; transition: transform 0.15s; }}
.mat-arr.open {{ transform: rotate(90deg); }}

.mat-body {{
  border-top: 1px solid var(--border);
  padding: 8px 10px;
  display: none;
  flex-direction: column;
  gap: 6px;
  background: #fafbfd;
}}
.mat-body.open {{ display: flex; }}

.field-block {{ padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
.field-block:last-child {{ border-bottom: none; padding-bottom: 0; }}
.field-label {{ font-size: 9px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 3px; }}
.field-val   {{ font-size: 11px; color: var(--text); line-height: 1.5; }}
.field-empty {{ font-size: 10px; color: var(--text-3); font-style: italic; }}

.sem-aula {{ text-align: center; padding: 24px 0; font-size: 11px; color: var(--text-3); }}
.feriado  {{ text-align: center; padding: 20px 0; font-size: 12px; color: var(--text-2); font-weight: 500; }}

/* ── Aba atividades futuras ── */
.future-view {{ padding: 20px; display: flex; flex-direction: column; gap: 16px; }}
.future-week {{ background: var(--surface); border-radius: 10px; border: 1px solid var(--border); overflow: hidden; }}
.future-week-head {{
  padding: 10px 16px;
  background: var(--azul);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
}}
.future-week-head.past {{ background: #64748b; }}
.future-items {{ display: flex; flex-direction: column; }}
.future-item {{
  padding: 10px 16px;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  border-bottom: 1px solid var(--border);
}}
.future-item:last-child {{ border-bottom: none; }}
.future-date {{
  font-size: 11px;
  font-family: var(--mono);
  color: var(--text-2);
  min-width: 70px;
  flex-shrink: 0;
  padding-top: 1px;
}}
.future-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 4px; }}
.future-content {{ flex: 1; }}
.future-disc {{ font-size: 12px; font-weight: 500; color: var(--text); }}
.future-desc {{ font-size: 11px; color: var(--text-2); margin-top: 2px; line-height: 1.4; }}
.future-prova {{ background: var(--verm-bg); border: 1px solid var(--verm-brd); border-radius: 99px; font-size: 10px; font-weight: 600; color: #791F1F; padding: 1px 8px; display: inline-block; margin-top: 4px; }}

/* ── Footer ── */
.footer {{
  background: var(--surface);
  border-top: 1px solid var(--border);
  padding: 9px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
}}
.footer-left {{ font-size: 10px; color: var(--text-2); line-height: 1.7; }}
.footer-btn {{
  all: unset;
  border: 1px solid var(--border);
  color: var(--text-2);
  border-radius: 6px;
  padding: 3px 9px;
  font-size: 10px;
  cursor: pointer;
  font-family: var(--font);
  transition: background 0.15s;
  white-space: nowrap;
}}
.footer-btn:hover {{ background: var(--bg); }}

/* ── Responsivo ── */
@media (max-width: 768px) {{
  .week-grid {{ grid-template-columns: 1fr; }}
  .topbar {{ flex-wrap: wrap; gap: 10px; }}
  .week-nav {{ width: 100%; justify-content: space-between; }}
  .week-label {{ flex: 1; }}
  .day-col {{ border-bottom: 2px solid var(--border); }}
  .day-head {{ display: flex; align-items: center; gap: 10px; }}
  .day-num  {{ font-size: 18px; }}
}}

/* ── Impressão ── */
@media print {{
  @page {{ margin: 15mm; }}
  .topbar {{ background: #fff !important; padding: 0 0 10px; border-bottom: 2px solid #000; }}
  .topbar-name {{ color: #000 !important; font-size: 18px; }}
  .topbar-date {{ color: #555 !important; }}
  .week-btn, .tabs, .footer-btn {{ display: none !important; }}
  .tabs {{ display: none !important; }}
  .view {{ display: flex !important; }}
  #view-future {{ display: none !important; }}
  .week-grid {{ grid-template-columns: repeat(5, 1fr) !important; gap: 4px; background: #ddd; }}
  .day-head.today {{ background: #eee !important; }}
  .day-head.today .day-num {{ color: #000 !important; }}
  .day-head.today .day-name {{ color: #555 !important; }}
  .mat-body {{ display: flex !important; background: #f9f9f9 !important; }}
  .prova-banner {{ border: 2px solid #000 !important; background: #f0f0f0 !important; }}
  .prova-nome {{ color: #000 !important; }}
  .prova-sub {{ color: #333 !important; }}
  .prova-circle {{ background: #000 !important; }}
  .footer {{ border-top: 1px solid #ccc; padding: 8px 0 0; }}
  .footer-left {{ color: #777 !important; }}
  body {{ background: #fff; }}
}}
</style>
</head>
<body>

<div class="topbar">
  <div>
    <div class="topbar-name" id="topbar-name">Bernardo — programação semanal</div>
    <div class="topbar-date" id="topbar-date"></div>
  </div>
  <div class="week-nav">
    <button class="week-btn" onclick="mudarSemana(-1)">‹ anterior</button>
    <span class="week-label" id="week-label">semana atual</span>
    <button class="week-btn" onclick="mudarSemana(1)">próxima ›</button>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="mostrarAba('semana', this)">semana</div>
  <div class="tab" onclick="mostrarAba('future', this)">atividades futuras</div>
  <div class="tab" onclick="window.print()">🖨 imprimir</div>
</div>

<div class="view active" id="view-semana">
  <div class="week-grid" id="week-grid"></div>
</div>

<div class="view" id="view-future">
  <div class="future-view" id="future-view"></div>
</div>

<div class="footer">
  <div class="footer-left">
    Visão de aulas v:1.0 · Desenvolvido por Guilherme Bonatto<br>
    Última atualização: {gerado_em}
  </div>
  <button class="footer-btn" onclick="location.reload()">↺ forçar atualização</button>
</div>

<script>
const POR_DATA = {dados_js};
const COR_MAP  = {cor_map_js};
const HOJE_STR = "{hoje.isoformat()}";
const DIAS_PT  = ["domingo","segunda","terça","quarta","quinta","sexta","sábado"];
const DIAS_SHORT = ["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"];

let semanaOffset = 0;

function isoToDate(s) {{
  const [y,m,d] = s.split('-').map(Number);
  return new Date(y, m-1, d);
}}

function dateToIso(d) {{
  return d.getFullYear() + '-' +
    String(d.getMonth()+1).padStart(2,'0') + '-' +
    String(d.getDate()).padStart(2,'0');
}}

function inicioSemana(offset) {{
  const hoje = isoToDate(HOJE_STR);
  const dow   = hoje.getDay();
  const seg   = new Date(hoje);
  seg.setDate(hoje.getDate() - (dow === 0 ? 6 : dow - 1) + offset * 7);
  return seg;
}}

function fmt(d) {{
  return String(d.getDate()).padStart(2,'0') + '/' + String(d.getMonth()+1).padStart(2,'0');
}}

function fmtLongo(d) {{
  return `${{fmt(d)}}/${{d.getFullYear()}}`;
}}

function cor(disciplina) {{
  return COR_MAP[disciplina] || '#888';
}}

function renderSemana() {{
  const seg = inicioSemana(semanaOffset);
  const sex = new Date(seg); sex.setDate(seg.getDate() + 4);

  document.getElementById('topbar-date').textContent =
    `Semana de ${{fmt(seg)}} – ${{fmtLongo(sex)}}`;

  const label = semanaOffset === 0 ? 'semana atual'
    : semanaOffset < 0 ? `${{Math.abs(semanaOffset)}} sem. atrás`
    : `${{semanaOffset}} sem. à frente`;
  document.getElementById('week-label').textContent = label;

  const grid = document.getElementById('week-grid');
  grid.innerHTML = '';

  for (let i = 0; i < 5; i++) {{
    const d = new Date(seg); d.setDate(seg.getDate() + i);
    const iso = dateToIso(d);
    const isHoje = iso === HOJE_STR;
    const aulas  = (POR_DATA[iso] || []).sort((a,b) => a.horario.localeCompare(b.horario));

    const col = document.createElement('div');
    col.className = 'day-col';

    const head = document.createElement('div');
    head.className = 'day-head' + (isHoje ? ' today' : '');
    head.innerHTML = `<div class="day-name">${{DIAS_PT[i+1]}}</div><div class="day-num">${{String(d.getDate()).padStart(2,'0')}}</div>`;
    col.appendChild(head);

    const body = document.createElement('div');
    body.className = 'day-body';

    if (!aulas.length) {{
      body.innerHTML = '<div class="sem-aula">Sem aulas cadastradas</div>';
    }} else {{
      // Provas primeiro
      aulas.filter(a => a.avaliacao).forEach(a => {{
        const b = document.createElement('div');
        b.className = 'prova-banner';
        b.innerHTML = `
          <div class="prova-circle">!</div>
          <div>
            <div class="prova-nome">Prova — ${{a.disciplina}}</div>
            <div class="prova-sub">${{a.descAvaliacao || ''}}${{a.horario ? ' · ' + a.horario : ''}}</div>
          </div>`;
        body.appendChild(b);
      }});

      // Matérias
      aulas.forEach((a, idx) => {{
        const m = document.createElement('div');
        m.className = 'materia';

        const temConteudo = a.previsto || a.realizado || (a.temas && a.temas.length);
        const aberto = idx === 0 && temConteudo;

        let conteudoHTML = '';
        if (a.previsto)
          conteudoHTML += `<div class="field-block"><div class="field-label">Previsto</div><div class="field-val">${{a.previsto}}</div></div>`;
        if (a.realizado)
          conteudoHTML += `<div class="field-block"><div class="field-label">Realizado</div><div class="field-val">${{a.realizado}}</div></div>`;
        if (a.temas && a.temas.length)
          conteudoHTML += `<div class="field-block"><div class="field-label">Temas</div><div class="field-val">${{a.temas.join(', ')}}</div></div>`;
        if (!conteudoHTML)
          conteudoHTML = `<div class="field-empty">Conteúdo não preenchido</div>`;
        conteudoHTML += `<div class="field-block"><div class="field-label">Professor</div><div class="field-val">${{a.professor || '—'}}</div></div>`;

        m.innerHTML = `
          <div class="mat-head" onclick="toggleMateria(this)">
            <span class="mat-dot" style="background:${{cor(a.disciplina)}}"></span>
            <span class="mat-nome">${{a.disciplina}}</span>
            <span class="mat-hora">${{a.horario}}</span>
            <span class="mat-arr${{aberto ? ' open' : ''}}">▸</span>
          </div>
          <div class="mat-body${{aberto ? ' open' : ''}}">${{conteudoHTML}}</div>`;
        body.appendChild(m);
      }});
    }}

    col.appendChild(body);
    grid.appendChild(col);
  }}
}}

function toggleMateria(head) {{
  const arr  = head.querySelector('.mat-arr');
  const body = head.nextElementSibling;
  const aberto = body.classList.contains('open');
  body.classList.toggle('open', !aberto);
  arr.classList.toggle('open', !aberto);
}}

function mudarSemana(delta) {{
  semanaOffset += delta;
  renderSemana();
}}

function mostrarAba(aba, el) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  if (aba === 'semana') {{
    document.getElementById('view-semana').classList.add('active');
  }} else {{
    document.getElementById('view-future').classList.add('active');
    renderFuturo();
  }}
  if (el) el.classList.add('active');
}}

function renderFuturo() {{
  const container = document.getElementById('future-view');
  container.innerHTML = '';

  const hoje = isoToDate(HOJE_STR);
  const inicio = new Date(hoje); inicio.setDate(hoje.getDate() - 14);

  // Agrupa por semana
  const semanas = {{}};
  Object.entries(POR_DATA).forEach(([iso, aulas]) => {{
    const d = isoToDate(iso);
    if (d < inicio) return;
    // Início da semana
    const dow = d.getDay();
    const seg = new Date(d); seg.setDate(d.getDate() - (dow === 0 ? 6 : dow - 1));
    const key = dateToIso(seg);
    if (!semanas[key]) semanas[key] = [];
    aulas.forEach(a => {{
      if (a.previsto || a.realizado || a.avaliacao || (a.temas && a.temas.length)) {{
        semanas[key].push({{ ...a, data: iso, dataObj: d }});
      }}
    }});
  }});

  const keys = Object.keys(semanas).sort();
  if (!keys.length) {{
    container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-3);font-size:13px">Nenhuma atividade cadastrada ainda.</div>';
    return;
  }}

  keys.forEach(key => {{
    const items = semanas[key].sort((a,b) => a.data.localeCompare(b.data));
    const seg   = isoToDate(key);
    const sex   = new Date(seg); sex.setDate(seg.getDate() + 4);
    const isPast = sex < hoje;

    const block = document.createElement('div');
    block.className = 'future-week';

    const head = document.createElement('div');
    head.className = 'future-week-head' + (isPast ? ' past' : '');
    head.textContent = `Semana de ${{fmt(seg)}} – ${{fmt(sex)}}${{isPast ? ' (passada)' : ''}}`;
    block.appendChild(head);

    items.forEach(item => {{
      const row = document.createElement('div');
      row.className = 'future-item';
      const d = item.dataObj;
      const diasSemana = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];
      let desc = '';
      if (item.avaliacao) desc += `<span class="future-prova">Prova${{item.descAvaliacao ? ': ' + item.descAvaliacao : ''}}</span> `;
      if (item.previsto)  desc += `<div class="future-desc">Previsto: ${{item.previsto}}</div>`;
      if (item.temas && item.temas.length) desc += `<div class="future-desc">Temas: ${{item.temas.join(', ')}}</div>`;

      row.innerHTML = `
        <div class="future-date">${{diasSemana[d.getDay()]}} ${{fmt(d)}}</div>
        <div class="future-dot" style="background:${{cor(item.disciplina)}}"></div>
        <div class="future-content">
          <div class="future-disc">${{item.disciplina}}</div>
          ${{desc}}
        </div>`;
      block.appendChild(row);
    }});

    container.appendChild(block);
  }});
}}

renderSemana();
</script>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    print("🚀 Iniciando extração do calendário La Salle v2...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        await fazer_login(page)

        print("📋 Buscando contexto...")
        ctx      = await fetch_contexto(page)
        id_aluno = ctx["idResponsavel"] - 1

        print(f"🎒 Buscando enturmação (idAluno={id_aluno})...")
        enturmacao = await fetch_enturmacoes(page, id_aluno)

        print("📚 Buscando componentes curriculares...")
        componentes = await fetch_componentes(page, enturmacao["idEnturmacao"])

        if not componentes:
            print("⚠️ Nenhum componente encontrado, usando cronograma diário como fallback...")
            # Fallback: busca semana a semana
            hoje   = date.today()
            por_data = {}
            for w in range(-2, 8):
                inicio = hoje - timedelta(days=hoje.weekday()) + timedelta(weeks=w)
                for i in range(5):
                    d = inicio + timedelta(days=i)
                    r = await api_get(page, f"{API_BASE}/Enturmacoes/Cronograma", {{
                        "idEnturmacao": enturmacao["idEnturmacao"],
                        "dataAula":     d.isoformat(),
                        "idTurma":      enturmacao["idTurma"],
                    }})
                    if r and r.get("aulas"):
                        por_data[d.isoformat()] = [{{
                            "disciplina":    a.get("disciplina",""),
                            "professor":     a.get("professor",""),
                            "horario":       a.get("horario",""),
                            "avaliacao":     a.get("avaliacao", False),
                            "descAvaliacao": a.get("descricaoAvaliacao") or "",
                            "previsto":      next((c["conteudo"] for c in a.get("conteudoAula",[]) if "previsto" in c["titulo"].lower()), ""),
                            "realizado":     next((c["conteudo"] for c in a.get("conteudoAula",[]) if "realizado" in c["titulo"].lower()), ""),
                            "temas":         [],
                            "cancelada":     a.get("aulaCancelada", False),
                        }} for a in r["aulas"]]
        else:
            print("📅 Buscando dados por componente curricular...")
            por_data = await fetch_todos_dados(page, enturmacao, componentes)

        await browser.close()

    print(f"   {len(por_data)} dias com dados")
    print("🎨 Gerando HTML...")
    html = render_html(por_data, enturmacao)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"✅ Calendário gerado: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
