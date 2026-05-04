# 📅 Calendário Semanal — La Salle GVDasa

Extrai automaticamente o cronograma semanal do portal La Salle e publica como página HTML gratuita via GitHub Pages.

---

## 🛠️ Como configurar (passo a passo)

### 1. Criar o repositório no GitHub

1. Acesse [github.com](https://github.com) e faça login (crie uma conta se não tiver — é grátis)
2. Clique em **New repository**
3. Nome sugerido: `lasalle-calendario`
4. Marque **Private** (recomendado, pois contém suas credenciais como secrets)
5. Clique em **Create repository**

---

### 2. Subir os arquivos

Faça upload dos arquivos deste projeto para o repositório:
- `fetch_calendar.py`
- `.github/workflows/calendar.yml`

Você pode usar a interface web do GitHub (arrastar e soltar os arquivos).

---

### 3. Configurar as credenciais (Secrets)

> ⚠️ Nunca coloque sua matrícula e senha diretamente no código!

No seu repositório, vá em:
**Settings → Secrets and variables → Actions → New repository secret**

Crie dois secrets:

| Nome | Valor |
|------|-------|
| `GVDASA_MATRICULA` | Sua matrícula (ex: `01311114009`) |
| `GVDASA_SENHA` | Sua senha do portal |

---

### 4. Ativar o GitHub Pages

1. Vá em **Settings → Pages**
2. Em **Source**, selecione **Deploy from a branch**
3. Em **Branch**, selecione `gh-pages` e pasta `/ (root)`
4. Clique em **Save**

---

### 5. Rodar pela primeira vez

1. Vá na aba **Actions** do seu repositório
2. Clique no workflow **📅 Atualizar Calendário La Salle**
3. Clique em **Run workflow → Run workflow**
4. Aguarde ~2 minutos

---

### 6. Acessar o calendário

Após o primeiro run, seu calendário estará disponível em:

```
https://SEU_USUARIO.github.io/lasalle-calendario/
```

---

## ⏰ Atualização automática

O workflow roda automaticamente **todo dia de semana às 6h da manhã** (horário de Brasília).

Você também pode disparar manualmente clicando em **Run workflow** na aba Actions.

---

## 📁 Estrutura do projeto

```
lasalle-calendario/
├── fetch_calendar.py          # Script principal
├── index.html                 # Calendário gerado (criado automaticamente)
└── .github/
    └── workflows/
        └── calendar.yml       # Automação GitHub Actions
```

---

## ❓ Problemas comuns

**Login não funciona:**
- Verifique se os secrets `GVDASA_MATRICULA` e `GVDASA_SENHA` estão corretos
- O portal pode ter mudado o layout — abra uma issue

**Calendário sem aulas:**
- Normal para feriados e finais de semana
- Verifique se o portal está com o cronograma preenchido para a semana

**Erro no GitHub Actions:**
- Clique no run com ❌ e expanda os logs para ver o erro
