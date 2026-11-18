# Primecare — Discharge Packet

Ferramenta da Primecare Chiropractic para montar o pacote de alta de pacientes MVA.

| Parte | Onde roda | O que faz |
|---|---|---|
| **Página** (`docs/`, GitHub Pages) | qualquer Chrome | Você envia o Statement e o Intake, confere os dados e ela gera **Discharge Packet, Lien Notice (With/Without LOR), Lien Notice Final, Carta de Agradecimento e Letter 112** em PDF. Pode salvar direto nas pastas do Synology. |
| **Script local** (`local/`) | Mac da clínica | Entra no ChiroTouch com a sua sessão, busca o paciente, baixa SOAP notes / Statement / Intake e gera as mesmas cartas com o mesmo motor da página, salvando no Synology. |

## Privacidade (HIPAA)
- A página **não envia nada** para servidor nenhum: os PDFs são lidos e gerados dentro do navegador.
- O repositório só tem código, o papel timbrado e a lista de escritórios. **Nenhum dado de paciente** e **nenhuma senha**.
- O script local usa um perfil próprio do Chrome: você faz login no ChiroTouch uma vez e a sessão fica só neste Mac.
- Observação: em contas GitHub Pro/Team, o *site* do GitHub Pages fica acessível por quem tiver o link, mesmo com repositório privado (só o código fica privado). Como o site não contém dados de pacientes, isso não expõe PHI.

## Usar a página
1. Abra o site do GitHub Pages (ou `docs/index.html` localmente).
2. Arraste o(s) Statement(s) — preenche MRN, nome, initial exam, último atendimento, charges, pagos e advogado.
3. Preencha/confira DOB, DOL, endereço (2 linhas), sexo, LOR e se teve Final Exam.
4. Arraste o Intake e marque as páginas do *Irrevocable Assignment* (geralmente 9 e 10; duplo clique abre a página).
5. **Gerar cartas** → abrir/baixar cada PDF, **Baixar tudo (.zip)**, ou **Salvar nas pastas do Synology** (escolha as pastas "Discharge Packet" e "Letters" uma vez).

## Usar o script local (Mac)
1. Baixe este repositório no Mac.
2. Duplo clique em `local/Instalar.command` (uma vez).
3. Duplo clique em `local/Rodar paciente.command` e digite o MRN — ou no Terminal:
   ```
   python3 local/primecare_run.py patient 2830
   python3 local/primecare_run.py patient 2830 --lor n --final-exam s --assign-pages 9,10
   python3 local/primecare_run.py finals            # quem teve Final Exam ontem
   python3 local/primecare_run.py letters 2830      # só refaz as cartas (usa patient.json)
   ```
4. Pastas: copie `local/config.example.json` para `local/config.json` se precisar mudar os caminhos.

O script tenta fazer os cliques do ChiroTouch sozinho. Se a tela do ChiroTouch mudar, ele avisa no Terminal, você faz o clique no Chrome e ele captura o PDF automaticamente.

## Regras que o motor segue
- Data de hoje no topo das cartas; "Sent via regular and certified mail:" com linha em branco (ou o tracking informado).
- Nome na ordem Primeiro, Meio, Último. Mr./his ou Ms./her conforme o sexo.
- Datas de tratamento: primeira consulta (99203) até o último atendimento do Statement; valores somando todos os Statements.
- Lien com assinatura "PC"; Lien Notice Final = Lien + Assignment (+ Statement se With LOR).
- Sem Final Exam → Letter 112. Carta de Agradecimento sempre, também copiada em `Letters/MM-DD-AAAA`.
- Sem advogado → Discharge Packet com "No Attorney" e sem Lien.

## Manutenção
- `docs/engine.js` — layout de cada carta (posições medidas dos modelos aprovados).
- `docs/statement.js` — leitura do Statement do ChiroTouch.
- `docs/assets/attorneys.json` — escritórios e endereços padrão (edite para adicionar novos).
- `tools/build_assets.py` — recria o papel timbrado e as fontes a partir dos modelos.
