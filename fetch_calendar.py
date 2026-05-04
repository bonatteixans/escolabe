"""
La Salle GVDasa - Extrator de Calendário Semanal
Faz login via Playwright e usa a sessão autenticada para chamar a API REST.
"""

import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext

# ── Configurações ──────────────────────────────────────────────────────────────
LOGIN_URL   = "https://lasalle.aluno.gvdasa.com.br"
API_BASE    = "https://api.gvdasa.com.br/portal/api/v1"
OUTPUT_FILE = Path(__file__).parent / "index.html"

MATRICULA = os.environ["GVDASA_MATRICULA"]
SENHA     = os.environ["GVDASA_SENHA"]


# ── Login ──────────────────────────────────────────────────────────────────────
async def fazer_login(context: BrowserContext) -> None:
    page = await context.new_page()

    print("🔐 Abrindo portal La Salle...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(2)
    print(f"   Redirecionado para: {page.url}")

    await page.wait_for_selector(
        'input:not([type="hidden"]):not([type="password"])',
        timeout=20_000, state="visible"
    )

    print("📝 Preenchendo credenciais...")
    await page.fill('input:not([type="hidden"]):not([type="password"])', MATRICULA)
    await asyncio.sleep(0.3)
    await page.fill('input[type="password"]', SENHA)
    await asyncio.sleep(0.3)

    print("🖱️ Clicando em Entrar...")
    await page.click('button[type="submit"]')

    print("⏳ Aguardando redirecionamento pós-login...")
    await page.wait_for_url("**/pagina-inicial**", timeout=45_000)
    await page.wait_for_load_state("networkidle", timeout=20_000)
    print(f"✅ Login OK! URL: {page.url}")
    await page.close()


# ── Chamadas à API via sessão autenticada ──────────────────────────────────────
async def api_get(context: BrowserContext, url: str, params: dict = None) -> dict | list:
    page = await context.new_page()
    try:
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            full_url = f"{url}?{qs}"
        else:
            full_url = url

        response_body = None

        async def handle_response(response):
            nonlocal response_body
            if full_url in response.url and response.status == 200:
                try:
                    response_body = await response.json()
                except Exception:
                    pass

        page.on("response", handle_response)
        resp = await page.goto(full_url, wait_until="domcontentloaded", timeout=30_000)

        if response_body is None and resp:
            try:
                text = await resp.text()
                response_body = json.loads(text)
            except Exception:
                pass

        return response_body or {}
    finally:
        await page.close()


async def fetch_contexto(context: BrowserContext) -> dict:
    data = await api_get(context, f"{API_BASE}/ContextoUsuario")
    usuario = data.get("usuario", {})
    print(f"   Usuário: {usuario.get('nome')} (id={usuario.get('id')})")
    return {"idResponsavel": usuario["id"], "nome": usuario["nome"]}


async def fetch_enturmacoes(context: BrowserContext, id_aluno: int) -> dict:
    data = await api_get(context, f"{API_BASE}/Alunos/Enturmacoes", {"idPessoa": id_aluno})
    if not data or not isinstance(data, list):
        raise RuntimeError(f"Enturmações vazias para idPessoa={id_aluno}: {data}")
    e = data[0]
    print(f"   Turma: {e.get('descricaoTurma')} | Aluno: {e.get('nomeAluno')}")
    return {
        "idEnturmacao": e["idEnturmacao"],
        "idTurma":      e["idTurma"],
        "turma":        e.get("descricaoTurma", ""),
        "nomeAluno":    e.get("nomeAluno", ""),
    }


async def fetch_cronograma_dia(context: BrowserContext, id_enturmacao: int, id_turma: int, data_aula: date) -> dict:
    return await api_get(context, f"{API_BASE}/Enturmacoes/Cronograma", {
        "idEnturmacao": id_enturmacao,
        "dataAula":     data_aula.isoformat(),
        "idTurma":      id_turma,
    })


async def fetch_semana(context: BrowserContext, id_enturmacao: int, id_turma: int) -> list[dict]:
    hoje   = date.today()
    inicio = hoje - timedelta(days=hoje.weekday())
    dias   = [inicio + timedelta(days=i) for i in range(5)]
    semana = []
    for d in dias:
        try:
            r = await fetch_cronograma_dia(context, id_enturmacao, id_turma, d)
            semana.append(r if r else {"dataAula": d.isoformat(), "aulas": []})
        except Exception as ex:
            print(f"   ⚠️ Erro em {d}: {ex}")
            semana.append({"dataAula": d.isoformat(), "aulas": []})
    return semana


# ── Gerador de HTML ────────────────────────────────────────────────────────────
DIAS_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta"]
CORES   = ["#4f46e5","#0891b2","#059669","#d97706","#dc2626","#7c3aed","#be185d","#0369a1","#15803d","#b45309"]

def get_cor(disciplina: str, cache: dict) -> str:
    if disciplina not in cache:
        cache[disciplina] = CORES[len(cache) % len(CORES)]
    return cache[disciplina]


def render_html(semana: list[dict], info: dict) -> str:
    hoje          = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    cor_cache: dict = {}

    for dia in semana:
        for aula in dia.get("aulas", []):
            if not aula.get("aulaCancelada"):
                get_cor(aula["disciplina"], cor_cache)

    legenda_html = "".join(
        f'<span class="leg-item"><span class="leg-dot" style="background:{cor}"></span>{disc}</span>'
        for disc, cor in cor_cache.items()
    )

    colunas_html = ""
    for i, dia_data in enumerate(semana):
        data_obj = date.fromisoformat(dia_data.get("dataAula", (inicio_semana + timedelta(days=i)).isoformat()))
        is_hoje  = data_obj == hoje
        feriado  = dia_data.get("feriado", False)
        aulas    = dia_data.get("aulas", [])
        data_fmt = data_obj.strftime("%d/%m")

        aulas_html = ""
        if feriado:
            aulas_html = f'<div class="feriado">🎉 {dia_data.get("descricaoFeriado") or "Feriado"}</div>'
        elif not dia_data.get("diaLetivo", True) and not aulas:
            aulas_html = '<div class="feriado">📅 Sem aula</div>'
        elif not aulas:
            aulas_html = '<div class="sem-aula">Sem aulas cadastradas</div>'
        else:
            for aula in aulas:
                cancelada  = aula.get("aulaCancelada", False)
                disciplina = aula.get("disciplina", "")
                professor  = aula.get("professor", "")
                horario    = aula.get("horario", "")
                tem_aval   = aula.get("avaliacao", False)
                desc_aval  = aula.get("descricaoAvaliacao", "")
                conteudos  = aula.get("conteudoAula", [])
                c_prev     = next((c["conteudo"] for c in conteudos if "previsto"  in c["titulo"].lower()), "")
                c_real     = next((c["conteudo"] for c in conteudos if "realizado" in c["titulo"].lower()), "")
                cor        = get_cor(disciplina, cor_cache)
                badge      = f'<span class="badge avaliacao">📝 {desc_aval or "Avaliação"}</span>' if tem_aval else ""
                cont       = ""
                if c_prev: cont += f'<div class="conteudo"><strong>Previsto:</strong> {c_prev}</div>'
                if c_real: cont += f'<div class="conteudo realizado"><strong>Realizado:</strong> {c_real}</div>'
                cancel     = '<div class="cancelada-label">⚠️ Aula cancelada</div>' if cancelada else ""
                aulas_html += f"""
                <div class="aula-card{'  cancelada' if cancelada else ''}" style="border-left:4px solid {cor}">
                  <div class="aula-top">
                    <span class="disciplina" style="color:{cor}">{disciplina}</span>
                    <span class="horario">⏰ {horario}</span>
                  </div>
                  <div class="professor">👤 {professor}</div>
                  {badge}{cont}{cancel}
                </div>"""

        hoje_badge   = '<div class="hoje-badge">Hoje</div>' if is_hoje else ""
        col_class    = "col-header hoje" if is_hoje else "col-header"
        colunas_html += f"""
        <div class="col-dia">
          <div class="{col_class}">
            <div class="dia-nome">{DIAS_PT[i]}</div>
            <div class="dia-data">{data_fmt}</div>
            {hoje_badge}
          </div>
          <div class="col-body">{aulas_html}</div>
        </div>"""

    nome_aluno = info.get("nomeAluno", "")
    turma      = info.get("turma", "")
    semana_fmt = f"{inicio_semana.strftime('%d/%m')} – {(inicio_semana + timedelta(days=4)).strftime('%d/%m/%Y')}"
    import datetime
    gerado_em  = datetime.datetime.now().strftime("%d/%m/%Y às %H:%M")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calendário La Salle – {semana_fmt}</title>
<style>
:root{{--azul:#0f2e99;--azul-c:#2440d6;--verm:#ff2342;--bg:#f0f4ff;--border:#e2e8f0;--sub:#64748b}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:#1e293b}}
.topbar{{background:var(--azul);color:white;padding:16px 24px;display:flex;align-items:center;gap:16px}}
.topbar-title{{font-size:20px;font-weight:700}}.topbar-sub{{font-size:13px;opacity:.75;margin-top:2px}}
.topbar-info{{margin-left:auto;text-align:right;font-size:13px;opacity:.85}}
.legenda{{background:white;border-bottom:1px solid var(--border);padding:10px 24px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;font-size:12px}}
.legenda-label{{font-weight:600;color:var(--sub)}}
.leg-item{{display:flex;align-items:center;gap:5px}}
.leg-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;padding:20px 24px}}
.col-dia{{display:flex;flex-direction:column;background:white;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);border:1px solid var(--border)}}
.col-header{{background:var(--azul);color:white;padding:12px 14px;text-align:center}}
.col-header.hoje{{background:var(--azul-c)}}
.dia-nome{{font-size:13px;font-weight:700;text-transform:uppercase;opacity:.9}}
.dia-data{{font-size:22px;font-weight:800;line-height:1.2}}
.hoje-badge{{display:inline-block;background:var(--verm);color:white;font-size:10px;font-weight:700;padding:2px 7px;border-radius:99px;margin-top:4px}}
.col-body{{padding:10px;display:flex;flex-direction:column;gap:8px;flex:1}}
.aula-card{{background:#f8faff;border-radius:8px;padding:10px 12px}}
.aula-card.cancelada{{opacity:.5}}
.aula-top{{display:flex;justify-content:space-between;align-items:flex-start;gap:6px;margin-bottom:4px}}
.disciplina{{font-size:13px;font-weight:700;line-height:1.3}}
.horario{{font-size:11px;color:var(--sub);white-space:nowrap}}
.professor{{font-size:11px;color:var(--sub);margin-bottom:4px}}
.conteudo{{font-size:11px;color:#475569;margin-top:4px;line-height:1.4}}
.conteudo.realizado{{color:#059669}}
.badge{{display:inline-block;font-size:10px;font-weight:600;padding:2px 8px;border-radius:99px;margin-top:4px}}
.badge.avaliacao{{background:#fef3c7;color:#92400e}}
.cancelada-label{{font-size:11px;color:var(--verm);font-weight:600;margin-top:4px}}
.feriado{{text-align:center;padding:20px 10px;font-size:13px;color:var(--sub);font-weight:600}}
.sem-aula{{text-align:center;padding:20px 10px;font-size:12px;color:#94a3b8}}
footer{{text-align:center;padding:16px;font-size:11px;color:var(--sub);border-top:1px solid var(--border);background:white}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="topbar-title">⭐ La Salle — Calendário Semanal</div>
    <div class="topbar-sub">Semana de {semana_fmt}</div>
  </div>
  <div class="topbar-info"><div><strong>{nome_aluno}</strong></div><div>Turma {turma}</div></div>
</div>
<div class="legenda"><span class="legenda-label">Disciplinas:</span>{legenda_html}</div>
<div class="grid">{colunas_html}</div>
<footer>Gerado em {gerado_em} · Portal GVDasa La Salle</footer>
</body>
</html>"""


# ── Orquestração principal ─────────────────────────────────────────────────────
async def main():
    print("🚀 Iniciando extração do calendário La Salle...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )

        await fazer_login(context)

        print("📋 Buscando contexto do usuário...")
        ctx      = await fetch_contexto(context)
        id_aluno = ctx["idResponsavel"] - 1

        print(f"🎒 Buscando enturmação (idAluno={id_aluno})...")
        enturmacao = await fetch_enturmacoes(context, id_aluno)

        print("📅 Buscando cronograma da semana...")
        semana = await fetch_semana(context, enturmacao["idEnturmacao"], enturmacao["idTurma"])

        await browser.close()

    print("🎨 Gerando HTML...")
    html = render_html(semana, enturmacao)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"✅ Calendário gerado: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
