"""
La Salle GVDasa - Extrator de Calendário Semanal
Faz login via Playwright (OAuth), extrai token e consome a API REST.
"""

import asyncio
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

# ── Configurações ──────────────────────────────────────────────────────────────
LOGIN_URL    = "https://lasalle.aluno.gvdasa.com.br"
API_BASE     = "https://api.gvdasa.com.br/portal/api/v1"
OUTPUT_FILE  = Path(__file__).parent / "index.html"

# Lidos de variáveis de ambiente (nunca hardcode em código!)
MATRICULA = os.environ["GVDASA_MATRICULA"]   # ex: 01311114009
SENHA     = os.environ["GVDASA_SENHA"]


# ── Login e captura do token ───────────────────────────────────────────────────
async def get_auth_token() -> str:
    """Faz login via Playwright e retorna o bearer token."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page    = await context.new_page()

        captured_token: str | None = None

        # Intercepta qualquer requisição à API GVDasa para pegar o Bearer token
        async def intercept(request):
            nonlocal captured_token
            if captured_token:
                return
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer ") and "api.gvdasa.com.br" in request.url:
                captured_token = auth.split(" ", 1)[1]
                print(f"✅ Token capturado via request intercept!")

        page.on("request", intercept)

        print("🔐 Abrindo portal La Salle...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(3)

        print(f"   URL atual: {page.url}")

        # O portal redireciona para login.lasalle.edu.br via OAuth
        # Aguarda a página de login OAuth carregar (pode já ter redirecionado)
        print("📝 Aguardando página de login OAuth...")
        try:
            # Aguarda aparecer um input visível de texto (matrícula)
            await page.wait_for_selector(
                'input:not([type="hidden"]):not([type="password"])',
                timeout=20_000,
                state="visible"
            )
        except Exception:
            print(f"   Ainda em: {page.url}")

        print(f"   URL do login: {page.url}")

        # Preenche matrícula e senha
        print("📝 Preenchendo credenciais...")
        try:
            # Preenche o primeiro input visível (matrícula/usuário)
            await page.fill('input:not([type="hidden"]):not([type="password"])', MATRICULA)
            await asyncio.sleep(0.5)
            await page.fill('input[type="password"]', SENHA)
            await asyncio.sleep(0.5)
            print("   Credenciais preenchidas!")
        except Exception as e:
            print(f"⚠️ Erro ao preencher: {e}")
            # Tenta por índice
            try:
                inputs = await page.query_selector_all('input:not([type="hidden"])')
                print(f"   Inputs visíveis: {len(inputs)}")
                if len(inputs) >= 2:
                    await inputs[0].fill(MATRICULA)
                    await inputs[1].fill(SENHA)
                    print("   Preenchido por índice!")
            except Exception as e2:
                print(f"⚠️ Também falhou por índice: {e2}")

        # Clica no botão de login
        print("🖱️ Clicando em Entrar...")
        try:
            # Tenta várias estratégias para achar o botão
            for selector in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Entrar")', 'button:has-text("ENTRAR")', 'button']:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    print(f"   Clicou em: {selector}")
                    break
        except Exception as e:
            print(f"⚠️ Erro ao clicar: {e}")

        # Aguarda redirecionamento de volta para o portal após login OAuth
        print("⏳ Aguardando redirecionamento pós-login...")
        try:
            await page.wait_for_url("**/pagina-inicial**", timeout=45_000)
            print(f"   Chegou em: {page.url}")
        except Exception:
            print(f"   Timeout. URL atual: {page.url}")

        await page.wait_for_load_state("networkidle", timeout=30_000)
        await asyncio.sleep(3)

        # Se ainda não capturou, navega para o cronograma para forçar chamadas à API
        if not captured_token:
            print("🔄 Navegando para cronograma para forçar requisições à API...")
            await page.goto(f"{LOGIN_URL}/cronograma", wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(4)

        # Se ainda não capturou, vasculha o localStorage em todos os domínios
        if not captured_token:
            print("🔍 Buscando token no localStorage...")
            all_storage = await page.evaluate("""() => {
                const result = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    result[key] = localStorage.getItem(key);
                }
                return result;
            }""")
            print(f"   localStorage keys: {list(all_storage.keys())}")
            for key, val in all_storage.items():
                if not val:
                    continue
                # Tenta direto como JWT
                if val.startswith("eyJ"):
                    captured_token = val
                    print(f"   Token direto na chave: {key}")
                    break
                # Tenta parsear como JSON e buscar access_token
                try:
                    obj = json.loads(val)
                    if isinstance(obj, dict):
                        for field in ("access_token", "token", "accessToken", "id_token"):
                            if obj.get(field, "").startswith("eyJ"):
                                captured_token = obj[field]
                                print(f"   Token em {key}.{field}")
                                break
                        # Busca recursiva em objetos aninhados
                        if not captured_token:
                            val_str = json.dumps(obj)
                            import re
                            matches = re.findall(r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}', val_str)
                            if matches:
                                captured_token = matches[0]
                                print(f"   Token encontrado via regex em {key}")
                                break
                except Exception:
                    pass

        # Última tentativa: sessionStorage
        if not captured_token:
            print("🔍 Buscando token no sessionStorage...")
            session_storage = await page.evaluate("""() => {
                const result = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    result[key] = sessionStorage.getItem(key);
                }
                return result;
            }""")
            for key, val in session_storage.items():
                if not val:
                    continue
                if val.startswith("eyJ"):
                    captured_token = val
                    break
                try:
                    import re
                    matches = re.findall(r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}', val)
                    if matches:
                        captured_token = matches[0]
                        break
                except Exception:
                    pass

        await browser.close()

        if not captured_token:
            raise RuntimeError(
                "Não foi possível capturar o token de autenticação. "
                "Verifique se GVDASA_MATRICULA e GVDASA_SENHA estão corretos."
            )

        print("✅ Token capturado com sucesso!")
        return captured_token


# ── Chamadas à API ─────────────────────────────────────────────────────────────
def make_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Origin": "https://lasalle.aluno.gvdasa.com.br",
    }


async def fetch_context(token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{API_BASE}/ContextoUsuario", headers=make_headers(token))
        r.raise_for_status()
        data = r.json()
        usuario = data["usuario"]
        # Pega o primeiro estudante vinculado
        aluno = next(
            (p for p in usuario.get("papeis", []) if "Aluno" in p.get("descricaoPapel", "") or p.get("idPapel") in [20, 21]),
            None,
        )
        return {
            "idResponsavel": usuario["id"],
            "nomeResponsavel": usuario["nome"],
        }


async def fetch_enturmacoes(token: str, id_pessoa_aluno: int) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{API_BASE}/Alunos/Enturmacoes",
            params={"idPessoa": id_pessoa_aluno},
            headers=make_headers(token),
        )
        r.raise_for_status()
        data = r.json()
        enturmacao = data[0]  # pega a enturmação ativa
        return {
            "idEnturmacao": enturmacao["idEnturmacao"],
            "idTurma": enturmacao["idTurma"],
            "turma": enturmacao.get("descricaoTurma", ""),
            "curso": enturmacao.get("descricaoCurso", ""),
            "nomeAluno": enturmacao.get("nomeAluno", ""),
        }


async def fetch_cronograma_dia(token: str, id_enturmacao: int, id_turma: int, data_aula: date) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{API_BASE}/Enturmacoes/Cronograma",
            params={
                "idEnturmacao": id_enturmacao,
                "dataAula": data_aula.isoformat(),
                "idTurma": id_turma,
            },
            headers=make_headers(token),
        )
        r.raise_for_status()
        return r.json()


async def fetch_semana(token: str, id_enturmacao: int, id_turma: int) -> list[dict]:
    """Busca os 5 dias úteis da semana atual."""
    hoje = date.today()
    # Início da semana (segunda-feira)
    inicio = hoje - timedelta(days=hoje.weekday())
    dias = [inicio + timedelta(days=i) for i in range(5)]  # seg a sex

    tasks = [fetch_cronograma_dia(token, id_enturmacao, id_turma, d) for d in dias]
    resultados = await asyncio.gather(*tasks, return_exceptions=True)

    semana = []
    for d, r in zip(dias, resultados):
        if isinstance(r, Exception):
            semana.append({"data": d.isoformat(), "erro": str(r), "aulas": []})
        else:
            semana.append(r)
    return semana


async def fetch_avaliacoes(token: str, id_enturmacao: int) -> list[dict]:
    """Busca avaliações próximas."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{API_BASE}/Enturmacoes/Avaliacoes",
                params={"idEnturmacao": id_enturmacao},
                headers=make_headers(token),
                timeout=10,
            )
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
        except Exception:
            pass
    return []


# ── Gerador de HTML ────────────────────────────────────────────────────────────
DIAS_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta"]

CORES_DISCIPLINA = [
    "#4f46e5", "#0891b2", "#059669", "#d97706", "#dc2626",
    "#7c3aed", "#be185d", "#0369a1", "#15803d", "#b45309",
]

def get_cor(disciplina: str, cache: dict) -> str:
    if disciplina not in cache:
        cache[disciplina] = CORES_DISCIPLINA[len(cache) % len(CORES_DISCIPLINA)]
    return cache[disciplina]


def render_html(semana: list[dict], info: dict) -> str:
    hoje = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    cor_cache: dict[str, str] = {}

    # Coleta todas as disciplinas para legenda
    todas_disciplinas = set()
    for dia in semana:
        for aula in dia.get("aulas", []):
            if not aula.get("aulaCancelada"):
                todas_disciplinas.add(aula["disciplina"])

    # Pré-gera cores
    for d in sorted(todas_disciplinas):
        get_cor(d, cor_cache)

    legenda_html = "".join(
        f'<span class="leg-item"><span class="leg-dot" style="background:{cor}"></span>{disc}</span>'
        for disc, cor in cor_cache.items()
    )

    colunas_html = ""
    for i, dia_data in enumerate(semana):
        data_obj = date.fromisoformat(dia_data["dataAula"]) if "dataAula" in dia_data else (inicio_semana + timedelta(days=i))
        is_hoje = data_obj == hoje
        feriado = dia_data.get("feriado", False)
        desc_feriado = dia_data.get("descricaoFeriado", "")
        aulas = dia_data.get("aulas", [])

        header_class = "col-header hoje" if is_hoje else "col-header"
        data_fmt = data_obj.strftime("%d/%m")

        aulas_html = ""
        if feriado:
            aulas_html = f'<div class="feriado">🎉 {desc_feriado or "Feriado"}</div>'
        elif not dia_data.get("diaLetivo", True):
            aulas_html = '<div class="feriado">📅 Sem aula</div>'
        elif not aulas:
            aulas_html = '<div class="sem-aula">Sem aulas cadastradas</div>'
        else:
            for aula in aulas:
                cancelada = aula.get("aulaCancelada", False)
                disciplina = aula["disciplina"]
                professor = aula.get("professor", "")
                horario = aula.get("horario", "")
                tem_avaliacao = aula.get("avaliacao", False)
                desc_avaliacao = aula.get("descricaoAvaliacao", "")

                conteudos = aula.get("conteudoAula", [])
                conteudo_previsto = next((c["conteudo"] for c in conteudos if "previsto" in c["titulo"].lower()), "")
                conteudo_realizado = next((c["conteudo"] for c in conteudos if "realizado" in c["titulo"].lower()), "")

                cor = get_cor(disciplina, cor_cache)
                card_class = "aula-card cancelada" if cancelada else "aula-card"

                avaliacao_badge = ""
                if tem_avaliacao:
                    avaliacao_badge = f'<span class="badge avaliacao">📝 {desc_avaliacao or "Avaliação"}</span>'

                conteudo_section = ""
                if conteudo_previsto:
                    conteudo_section += f'<div class="conteudo"><strong>Previsto:</strong> {conteudo_previsto}</div>'
                if conteudo_realizado:
                    conteudo_section += f'<div class="conteudo realizado"><strong>Realizado:</strong> {conteudo_realizado}</div>'

                aulas_html += f"""
                <div class="{card_class}" style="border-left: 4px solid {cor}">
                    <div class="aula-top">
                        <span class="disciplina" style="color:{cor}">{disciplina}</span>
                        <span class="horario">⏰ {horario}</span>
                    </div>
                    <div class="professor">👤 {professor}</div>
                    {avaliacao_badge}
                    {conteudo_section}
                    {'<div class="cancelada-label">⚠️ Aula cancelada</div>' if cancelada else ''}
                </div>"""

        colunas_html += f"""
        <div class="col-dia">
            <div class="{header_class}">
                <div class="dia-nome">{DIAS_PT[i]}</div>
                <div class="dia-data">{data_fmt}</div>
                {'<div class="hoje-badge">Hoje</div>' if is_hoje else ''}
            </div>
            <div class="col-body">{aulas_html}</div>
        </div>"""

    nome_aluno = info.get("nomeAluno", "")
    turma = info.get("turma", "")
    semana_fmt = f"{inicio_semana.strftime('%d/%m')} – {(inicio_semana + timedelta(days=4)).strftime('%d/%m/%Y')}"
    gerado_em = hoje.strftime("%d/%m/%Y às ") + __import__("datetime").datetime.now().strftime("%H:%M")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calendário La Salle – {semana_fmt}</title>
<style>
  :root {{
    --azul: #0f2e99;
    --azul-claro: #2440d6;
    --vermelho: #ff2342;
    --bg: #f0f4ff;
    --card-bg: #ffffff;
    --text: #1e293b;
    --sub: #64748b;
    --border: #e2e8f0;
    --hoje-bg: #eff6ff;
    --hoje-border: #2440d6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); }}

  /* Header */
  .topbar {{ background: var(--azul); color: white; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }}
  .topbar-star {{ font-size: 28px; }}
  .topbar-title {{ font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }}
  .topbar-sub {{ font-size: 13px; opacity: 0.75; margin-top: 2px; }}
  .topbar-info {{ margin-left: auto; text-align: right; font-size: 13px; opacity: 0.85; }}

  /* Legenda */
  .legenda {{ background: white; border-bottom: 1px solid var(--border); padding: 10px 24px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 12px; }}
  .legenda-label {{ font-weight: 600; color: var(--sub); margin-right: 4px; }}
  .leg-item {{ display: flex; align-items: center; gap: 5px; color: var(--text); }}
  .leg-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}

  /* Grid */
  .grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; padding: 20px 24px; min-height: calc(100vh - 140px); }}

  .col-dia {{ display: flex; flex-direction: column; background: var(--card-bg); border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border: 1px solid var(--border); }}

  .col-header {{ background: var(--azul); color: white; padding: 12px 14px; text-align: center; position: relative; }}
  .col-header.hoje {{ background: var(--azul-claro); box-shadow: 0 0 0 2px var(--azul-claro); }}
  .dia-nome {{ font-size: 13px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; opacity: 0.9; }}
  .dia-data {{ font-size: 22px; font-weight: 800; line-height: 1.2; }}
  .hoje-badge {{ display: inline-block; background: var(--vermelho); color: white; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 99px; margin-top: 4px; letter-spacing: 0.5px; }}

  .col-body {{ padding: 10px; display: flex; flex-direction: column; gap: 8px; flex: 1; }}

  /* Cards de aula */
  .aula-card {{ background: #f8faff; border-radius: 8px; padding: 10px 12px; border-left: 4px solid #ccc; transition: box-shadow 0.15s; }}
  .aula-card:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .aula-card.cancelada {{ opacity: 0.5; }}
  .aula-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 6px; margin-bottom: 4px; }}
  .disciplina {{ font-size: 13px; font-weight: 700; line-height: 1.3; }}
  .horario {{ font-size: 11px; color: var(--sub); white-space: nowrap; }}
  .professor {{ font-size: 11px; color: var(--sub); margin-bottom: 4px; }}
  .conteudo {{ font-size: 11px; color: #475569; margin-top: 4px; line-height: 1.4; }}
  .conteudo.realizado {{ color: #059669; }}
  .badge {{ display: inline-block; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 99px; margin-top: 4px; }}
  .badge.avaliacao {{ background: #fef3c7; color: #92400e; }}
  .cancelada-label {{ font-size: 11px; color: var(--vermelho); font-weight: 600; margin-top: 4px; }}

  .feriado {{ text-align: center; padding: 20px 10px; font-size: 13px; color: var(--sub); font-weight: 600; }}
  .sem-aula {{ text-align: center; padding: 20px 10px; font-size: 12px; color: #94a3b8; }}

  /* Footer */
  footer {{ text-align: center; padding: 16px; font-size: 11px; color: var(--sub); border-top: 1px solid var(--border); background: white; }}

  @media (max-width: 900px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .topbar {{ flex-wrap: wrap; }}
    .topbar-info {{ margin-left: 0; text-align: left; }}
  }}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-star">⭐</div>
  <div>
    <div class="topbar-title">La Salle — Calendário Semanal</div>
    <div class="topbar-sub">Semana de {semana_fmt}</div>
  </div>
  <div class="topbar-info">
    <div><strong>{nome_aluno}</strong></div>
    <div>Turma {turma}</div>
  </div>
</div>

<div class="legenda">
  <span class="legenda-label">Disciplinas:</span>
  {legenda_html}
</div>

<div class="grid">
  {colunas_html}
</div>

<footer>Gerado em {gerado_em} · Portal GVDasa La Salle</footer>

</body>
</html>"""


# ── Orquestração principal ─────────────────────────────────────────────────────
async def main():
    print("🚀 Iniciando extração do calendário La Salle...")

    token = await get_auth_token()

    print("📋 Buscando contexto do usuário...")
    ctx = await fetch_context(token)
    id_responsavel = ctx["idResponsavel"]

    # idPessoa do aluno = id_responsavel - 1 (padrão GVDasa observado no HAR)
    id_aluno = id_responsavel - 1

    print(f"🎒 Buscando enturmação do aluno (id={id_aluno})...")
    enturmacao = await fetch_enturmacoes(token, id_aluno)
    print(f"   Turma: {enturmacao['turma']} | Aluno: {enturmacao['nomeAluno']}")

    print("📅 Buscando cronograma da semana...")
    semana = await fetch_semana(token, enturmacao["idEnturmacao"], enturmacao["idTurma"])

    print("🎨 Gerando HTML...")
    html = render_html(semana, enturmacao)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"✅ Calendário gerado: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
