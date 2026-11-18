#!/usr/bin/env python3
"""
Primecare — Discharge Packet, automação local (roda no Mac da clínica).

O que faz, para um paciente:
  1. Abre o Google Chrome com um perfil próprio (você faz login no ChiroTouch uma vez; a sessão fica salva
     neste Mac — a senha NUNCA fica gravada neste script nem no GitHub).
  2. Busca o paciente pelo MRN e lê nome, DOB, sexo, endereço, DOL, advogado e notas pela API do ChiroTouch.
  3. Baixa: All Soap Notes, Case Statement e Intake (tenta clicar sozinho; se o ChiroTouch mudar de tela,
     ele pede para você clicar e captura o PDF automaticamente).
  4. Gera as cartas com o mesmo motor da página do GitHub (Discharge Packet, Lien, Lien Final, Carta,
     Letter 112) e salva tudo nas pastas do Synology.

Uso:
  python3 primecare_run.py finals                 # quem teve Final Exam ontem (ou --date MM/DD/YYYY)
  python3 primecare_run.py patient 2830           # pacote completo do paciente 2830
  python3 primecare_run.py patient 2830 --lor no --final-exam yes --assign-pages 9,10
  python3 primecare_run.py letters 2830           # só regenera as cartas a partir do patient.json salvo
"""
import argparse, base64, datetime as dt, functools, http.server, json, os, re, sys, threading, time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("Falta o Playwright. Rode:  python3 -m pip install --user playwright")

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent / "docs"
CONFIG = HERE / "config.json"
DEFAULTS = {
    "practice_url": "https://practice.chirotouch.com/practice",
    "discharge_dir": "~/Library/CloudStorage/SynologyDrive-Claude/Julia/Discharge Packet",
    "letters_dir": "~/Library/CloudStorage/SynologyDrive-Claude/Julia/Letters",
    "downloads_dir": "~/Downloads",
    "chrome_profile": "~/.primecare/chrome-profile",
    "tenant": "98654d7b-6654-414e-a3f2-e550bee7bb6e",
    "statement_from": "07/04/2025",
    "default_assignment_pages": [9, 10],
}

# ----------------------------------------------------------------------------- helpers
def cfg():
    c = dict(DEFAULTS)
    if CONFIG.exists():
        c.update(json.loads(CONFIG.read_text()))
    for k in ("discharge_dir", "letters_dir", "downloads_dir", "chrome_profile"):
        c[k] = Path(os.path.expanduser(c[k]))
    return c

def say(msg): print(f"\n▶ {msg}", flush=True)
def warn(msg): print(f"  ⚠ {msg}", flush=True)
def ok(msg): print(f"  ✓ {msg}", flush=True)

def ask(question, default=None, choices=None):
    sfx = f" [{default}]" if default else ""
    while True:
        a = input(f"  ? {question}{sfx}: ").strip() or (default or "")
        if not choices or a.lower() in choices:
            return a
        print(f"    responda: {', '.join(choices)}")

def yes(v):
    return str(v).strip().lower() in ("y", "yes", "s", "sim", "true", "1")

def mmddyyyy(v):
    """Accepts ISO '1996-01-09T00:00:00' or 'MM/DD/YYYY' -> 'MM/DD/YYYY'."""
    if not v:
        return ""
    v = str(v)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return f"{m[2]}/{m[3]}/{m[1]}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", v)
    if m:
        y = m[3] if len(m[3]) == 4 else "20" + m[3]
        return f"{int(m[1]):02d}/{int(m[2]):02d}/{y}"
    return v

def today(): return dt.date.today().strftime("%m/%d/%Y")

def pick(d, *keys, default=""):
    """Lenient getter: first non-empty value among several possible key names (case-insensitive)."""
    if not isinstance(d, dict):
        return default
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, "", []):
            return v
    return default

def safe(s): return re.sub(r'[\\/:*?"<>|]', "-", str(s)).strip()

def name_from_chirotouch(p):
    first = pick(p, "firstName", "first")
    middle = pick(p, "middleName", "middle")
    last = pick(p, "lastName", "last")
    return re.sub(r"\s+", " ", f"{first} {middle} {last}").strip()

def address_lines(p):
    addrs = pick(p, "addresses", default=[])
    a = addrs[0] if isinstance(addrs, list) and addrs else (pick(p, "address", default={}) or {})
    if isinstance(a, str):
        return a, ""
    l1 = " ".join(x for x in [pick(a, "address1", "line1", "street"), pick(a, "address2", "line2")] if x)
    city, st, z = pick(a, "city"), pick(a, "state"), str(pick(a, "zipcode", "zip", "postalCode"))[:5]
    return l1.strip(), f"{city}, {st} {z}".strip(", ")

# ----------------------------------------------------------------------------- the browser
INIT_JS = r"""
(() => {
  if (window.__pcHooked) return; window.__pcHooked = true;
  window.__pcBlobs = [];
  const orig = URL.createObjectURL.bind(URL);
  URL.createObjectURL = function (b) {
    try { if (b && b.type && /pdf/i.test(b.type)) window.__pcBlobs.push({ t: Date.now(), blob: b }); } catch (e) {}
    return orig(b);
  };
  // ChiroTouch opens prints/statements in a new window and then points it at a blob: URL.
  const fake = () => {
    const w = { closed: false, close() { this.closed = true; }, focus() {}, print() {},
      document: { write() {}, close() {}, open() {}, title: '' }, location: {} };
    return new Proxy(w, { set(t, k, v) { t[k] = v; return true; } });
  };
  try { Object.defineProperty(window, 'open', { value: (u) => { if (u && !/^blob:|^about:blank|^$/.test(String(u))) { const a = document.createElement('a'); a.href = u; a.target = '_blank'; } return fake(); }, writable: false }); } catch (e) {}
})();
"""

class CT:
    def __init__(self, c, page):
        self.c, self.page = c, page
        self.token, self.api_base, self.tenant = None, None, c["tenant"]
        page.on("request", self._sniff)

    def _sniff(self, req):
        h = req.headers
        t = h.get("x-access-token")
        if t:
            self.token = t
            m = re.match(r"(https://[^/]+)/", req.url)
            if m and "/api/" in req.url:
                self.api_base = m[1]
            m = re.search(r"/tenants/([0-9a-f-]{36})/", req.url)
            if m:
                self.tenant = m[1]

    def open(self):
        p = self.page
        p.goto(self.c["practice_url"], wait_until="domcontentloaded")
        t0 = time.time()
        while time.time() - t0 < 600:
            if self.token:
                return
            if re.search(r"login|signin|auth", p.url, re.I):
                say("O ChiroTouch pediu login. Entre na janela do Chrome (só desta vez) — estou esperando…")
                p.wait_for_url(re.compile(r"practice\.chirotouch\.com/practice"), timeout=600_000)
            p.wait_for_timeout(1500)
            if not self.token:
                p.reload()
                p.wait_for_timeout(3000)
        sys.exit("Não consegui sessão no ChiroTouch (a sessão expirou?). Faça login manualmente e rode de novo.")

    def api(self, path):
        url = f"{self.api_base}/{path.format(T=self.tenant)}"
        res = self.page.evaluate(
            """async ([u, t]) => { const r = await fetch(u, { headers: { 'X-ACCESS-TOKEN': t, 'Accept': 'application/json' } });
                 return { s: r.status, b: await r.text() }; }""", [url, self.token])
        if res["s"] >= 400:
            raise RuntimeError(f"API {res['s']} em {path}")
        try:
            return json.loads(res["b"])
        except Exception:
            return res["b"]

    # --- clicking helpers (Playwright CSS/text locators pierce ChiroTouch's shadow DOM) ---
    def click_text(self, *texts, timeout=6000):
        for t in texts:
            try:
                self.page.get_by_text(t, exact=True).first.click(timeout=timeout)
                return True
            except Exception:
                continue
        return False

    def wait_pdf(self, label, auto, dest: Path, c):
        """Run `auto` (clicks). Then wait for a PDF blob (print/statement) or a real download.
        If nothing arrives, ask the user to do the clicks and keep waiting."""
        p = self.page
        p.evaluate("window.__pcBlobs = []")
        downloads = []
        handler = lambda d: downloads.append(d)
        p.context.on("download", handler)
        before = set(c["downloads_dir"].glob("*.pdf")) if c["downloads_dir"].exists() else set()
        tried = False
        try:
            tried = bool(auto())
        except Exception as e:
            warn(f"clique automático falhou ({e.__class__.__name__})")
        deadline = time.time() + (120 if tried else 5)
        asked = False
        while True:
            if downloads:
                downloads[0].save_as(dest); ok(f"{label}: {dest.name}"); break
            n = p.evaluate("window.__pcBlobs.length")
            if n:
                b64 = p.evaluate("""async () => { const b = window.__pcBlobs[window.__pcBlobs.length-1].blob;
                    const buf = new Uint8Array(await b.arrayBuffer()); let s=''; for (let i=0;i<buf.length;i+=32768) s+=String.fromCharCode.apply(null, buf.subarray(i,i+32768)); return btoa(s); }""")
                dest.write_bytes(base64.b64decode(b64)); ok(f"{label}: {dest.name}"); break
            new = set(c["downloads_dir"].glob("*.pdf")) - before if c["downloads_dir"].exists() else set()
            new = [f for f in new if not f.name.endswith(".crdownload")]
            if new:
                f = max(new, key=lambda x: x.stat().st_mtime); time.sleep(1)
                dest.write_bytes(f.read_bytes()); ok(f"{label}: {dest.name} (de Downloads/{f.name})"); break
            if time.time() > deadline and not asked:
                asked = True
                say(f"Não consegui baixar '{label}' sozinho. Faça os cliques no Chrome — eu capturo o PDF assim que aparecer.")
                deadline = time.time() + 900
            elif time.time() > deadline:
                warn(f"desisti de esperar por {label}"); break
            p.wait_for_timeout(1000)
        p.context.remove_listener("download", handler)
        return dest.exists()

# ----------------------------------------------------------------------------- letter engine (same code as the web page)
class Engine:
    def __init__(self, context):
        handler = functools.partial(QuietHandler, directory=str(DOCS))
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.page = context.new_page()
        self.page.goto(f"http://127.0.0.1:{self.srv.server_address[1]}/index.html")
        self.page.wait_for_function("window.PC && window.PC.readStatement && window.PCApp")

    def read_statement(self, pdf: Path):
        return self.page.evaluate("async b64 => { const b = Uint8Array.from(atob(b64), c => c.charCodeAt(0)); const r = await PC.readStatement(b); delete r.visits; return r; }",
                                  base64.b64encode(pdf.read_bytes()).decode())

    def combine(self, pdfs):
        return self.page.evaluate("""async list => { const rs = []; for (const b64 of list) rs.push(await PC.readStatement(Uint8Array.from(atob(b64), c => c.charCodeAt(0))));
                 const r = PC.combineStatements(rs); delete r.visits; return r; }""", [base64.b64encode(p.read_bytes()).decode() for p in pdfs])

    def generate(self, data, intake: Path = None, statements=()):
        files = {"intake": base64.b64encode(intake.read_bytes()).decode() if intake and intake.exists() else None,
                 "statements": [base64.b64encode(s.read_bytes()).decode() for s in statements]}
        return self.page.evaluate("""async ([d, f]) => {
            const dec = s => s ? Uint8Array.from(atob(s), c => c.charCodeAt(0)) : null;
            const A = await PC.loadAssets(async n => new Uint8Array(await (await fetch('assets/' + n)).arrayBuffer()));
            const r = await PC.generateAll(A, d, { intake: dec(f.intake), statements: f.statements.map(dec) });
            const enc = u => { let s=''; for (let i=0;i<u.length;i+=32768) s+=String.fromCharCode.apply(null, u.subarray(i,i+32768)); return btoa(s); };
            return { folderName: r.folderName, lettersFolder: r.lettersFolder, files: r.files.map(x => ({ folder: x.folder, name: x.name, b64: enc(x.bytes) })) };
        }""", [data, files])

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-store"); super().end_headers()

# ----------------------------------------------------------------------------- commands
def launch(pw, c):
    c["chrome_profile"].mkdir(parents=True, exist_ok=True)
    kw = dict(user_data_dir=str(c["chrome_profile"]), headless=False, accept_downloads=True,
              viewport={"width": 1500, "height": 950})
    try:
        ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception:
        ctx = pw.chromium.launch_persistent_context(**kw)
    ctx.add_init_script(INIT_JS)
    return ctx

def find_patient(ct, mrn):
    res = ct.api("billing-api/api/v5/tenants/{T}/patients" + f"?search={mrn}")
    items = res if isinstance(res, list) else pick(res, "data", "items", "results", "patients", default=[])
    for p in items:
        if str(pick(p, "medicalRecordNumber", "mrn", "patientNumber", "accountNumber")) == str(mrn):
            return p
    if len(items) == 1:
        return items[0]
    raise SystemExit(f"Paciente {mrn} não encontrado pela busca do ChiroTouch ({len(items)} resultados).")

def patient_folder(c, mrn, name):
    root = c["discharge_dir"]
    for d in root.glob(f"{mrn} - *"):
        if d.is_dir():
            return d
    d = root / safe(f"{mrn} - {name}")
    d.mkdir(parents=True, exist_ok=True)
    return d

def cmd_patient(args):
    c = cfg()
    if not c["discharge_dir"].exists():
        sys.exit(f"Pasta não encontrada: {c['discharge_dir']}  (ajuste em local/config.json)")
    with sync_playwright() as pw:
        ctx = launch(pw, c)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ct = CT(c, page)
        say("Abrindo o ChiroTouch…"); ct.open(); ok("sessão ativa")

        say(f"Buscando o paciente {args.mrn}…")
        p = find_patient(ct, args.mrn)
        pid = pick(p, "id", "patientId")
        name = name_from_chirotouch(p)
        a1, a2 = address_lines(p)
        data = {"mrn": str(args.mrn), "fullName": name,
                "gender": "M" if str(pick(p, "sex", "gender")).upper().startswith("M") else "F",
                "dob": mmddyyyy(pick(p, "dateOfBirth", "dob", "birthDate")),
                "address1": a1, "address2": a2, "letterDate": today()}
        raw = {"patient": p}
        try:
            raw["alerts"] = ct.api(f"core-api/api/v1/tenants/{{T}}/patients/{pid}/alerts")
        except Exception as e:
            warn(f"alertas: {e}")
        try:
            raw["cases"] = ct.api(f"billing-api/api/v1/tenants/{{T}}/patients/{pid}/cases")
        except Exception as e:
            warn(f"cases: {e}")
        try:
            raw["totals"] = ct.api(f"billing-api/api/v4/tenants/{{T}}/patients/{pid}/totals")
        except Exception as e:
            warn(f"totals: {e}")
        cases = raw.get("cases") or []
        cases = cases if isinstance(cases, list) else pick(cases, "data", "items", default=[])
        mva = next((k for k in cases if re.search(r"mva|auto|accident", json.dumps(k), re.I)), cases[0] if cases else {})
        data["dol"] = mmddyyyy(pick(mva, "accidentDate", "injuryDate", "dateOfInjury", "onsetDate"))
        alerts_txt = json.dumps(raw.get("alerts", ""), ensure_ascii=False)
        m = re.search(r"DOL[:\s]*(\d{1,2}/\d{1,2}/\d{2,4})", alerts_txt)
        if m and mmddyyyy(m[1]) != data["dol"]:
            warn(f"DOL diferente: Case = {data['dol'] or '—'}, alerta = {mmddyyyy(m[1])}")
            data["dol"] = ask("Qual DOL usar? (MM/DD/YYYY — na dúvida confira a SOAP note)", mmddyyyy(m[1]))
        ok(f"{name} · DOB {data['dob']} · DOL {data['dol']} · {a1}, {a2}")

        folder = patient_folder(c, args.mrn, name)
        base = f"{args.mrn} - {name}"
        ok(f"pasta: {folder}")
        (folder / "_chirotouch.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False))

        pat_url = c["practice_url"] + f"/#/patients/{pid}"
        # ---- 1. SOAP notes
        soap = folder / f"{base} - All Soap Notes.pdf"
        if soap.exists() and not args.redownload:
            ok("SOAP notes já existem")
        else:
            say("Baixando as SOAP notes (Clinical → Encounters → ⋮ → Select All → Print Selected Encounters)…")
            def auto_soap():
                page.goto(pat_url + "/overview/profile"); page.wait_for_timeout(2500); page.reload(); page.wait_for_timeout(3000)
                ct.click_text("Clinical"); page.wait_for_timeout(1500)
                ct.click_text("Encounters"); page.wait_for_timeout(2000)
                page.locator("[icon*='more'], [name*='more'], neb-button-icon, .icon-more").first.click(timeout=5000)
                ct.click_text("Select All"); page.wait_for_timeout(500)
                return ct.click_text("Print Selected Encounters")
            ct.wait_pdf("SOAP notes", auto_soap, soap, c)

        # ---- 2. Case statement
        stmt = folder / f"{base} - Statement.pdf"
        if stmt.exists() and not args.redownload:
            ok("Statement já existe")
        else:
            say("Gerando o Case Statement (Cases → MVA → Case Statements → Generate New Case Statement)…")
            def auto_stmt():
                page.goto(pat_url + "/cases"); page.wait_for_timeout(2500); page.reload(); page.wait_for_timeout(3500)
                page.get_by_text(re.compile(r"MVA", re.I)).first.click(timeout=6000); page.wait_for_timeout(1500)
                ct.click_text("Case Statements"); page.wait_for_timeout(1500)
                ct.click_text("Generate New Case Statement"); page.wait_for_timeout(2000)
                frm = page.get_by_label(re.compile(r"Date of Service From", re.I)).first
                frm.fill(c["statement_from"]); frm.press("Tab")
                to = page.get_by_label(re.compile(r"Date of Service To", re.I)).first
                to.fill(today()); to.press("Tab")
                for lbl in ("Include Payment Details", "Include Purchase/Fee Charges"):
                    cb = page.get_by_label(lbl).first
                    if not cb.is_checked():
                        cb.check()
                return ct.click_text("Generate", "Generate Statement")
            ct.wait_pdf("Statement", auto_stmt, stmt, c)

        # ---- 3. Intake
        intake = next(iter(sorted(folder.glob("*Intake*.pdf"))), folder / f"{base} - Intake.pdf")
        if intake.exists() and not args.redownload:
            ok("Intake já existe")
        else:
            intake = folder / f"{base} - Intake.pdf"
            say('Baixando o Intake (Documents → "Intake - MRN - Nome" → Download)…')
            def auto_intake():
                page.goto(pat_url + "/documents"); page.wait_for_timeout(2500); page.reload(); page.wait_for_timeout(3500)
                row = page.get_by_text(re.compile(rf"Intake\s*-\s*{args.mrn}", re.I)).first
                row.hover(timeout=6000)
                row.locator("xpath=ancestor::*[contains(@class,'row')][1]").locator("neb-button-actions, [icon*='more']").first.click(timeout=4000)
                return ct.click_text("Download")
            ct.wait_pdf("Intake", auto_intake, intake, c)

        # ---- 4. figures from the Statement(s)
        eng = Engine(ctx)
        stmts = sorted(folder.glob("*Statement*.pdf"))
        if not stmts:
            sys.exit("Sem Statement na pasta — não dá para preencher datas e valores.")
        s = eng.combine(stmts)
        data.update({"initialExam": s["initialExam"], "dischargeDate": s["lastVisit"],
                     "charges": round(s["charges"], 2), "paid": round(s["paid"], 2)})
        ok(f"Statement: initial {s['initialExam']} · último atendimento {s['lastVisit']} · charges ${s['charges']:,.2f} · pago ${s['paid']:,.2f}")

        # ---- 5. attorney / LOR / final exam
        att_list = json.loads((DOCS / "assets" / "attorneys.json").read_text())
        st_att = s.get("attorney") or {}
        cand = st_att.get("name") or ""
        if not cand:
            m = re.search(r"(Brack|Altman|Brooks|Michael Kelly|Perrault|Jantzen|Sweeney|Glassman|Joyce|Georges Cote|Kaufman|Campione)", alerts_txt, re.I)
            cand = m[1] if m else ""
        hit = next((a for a in att_list if any(k in cand.lower() for k in a["match"])), None)
        att = {"name": hit["name"], "addr1": hit["addr1"], "addr2": hit["addr2"]} if hit else {k: st_att.get(k, "") for k in ("name", "addr1", "addr2")}
        if not att.get("name"):
            warn("Nenhum advogado encontrado no Statement nem nas notas.")
            nm = ask('Nome do escritório (ou "No Attorney")', "No Attorney")
            att = {"name": nm, "addr1": "", "addr2": ""} if nm.lower() == "no attorney" else {"name": nm, "addr1": ask("Endereço linha 1"), "addr2": ask("Endereço linha 2")}
        ok(f"Advogado: {att['name']} — {att.get('addr1','')} / {att.get('addr2','')}")
        data["attorney"] = att

        lor_files = [f.name for f in folder.iterdir() if re.search(r"\bLOR\b|letter of rep", f.name, re.I)]
        lor_note = bool(re.search(r"\bLOR\b", alerts_txt))
        if args.lor:
            data["lor"] = yes(args.lor)
        else:
            hint = "s" if (lor_files or lor_note) else "n"
            if lor_files: ok(f"arquivos de LOR na pasta: {', '.join(lor_files)}")
            if lor_note: ok("as notas do ChiroTouch mencionam LOR")
            data["lor"] = yes(ask("Lien With LOR? (s/n)", hint, ["s", "n", "y", "sim", "nao", "não", "no", "yes"]))
        if args.final_exam:
            data["finalExam"] = yes(args.final_exam)
        else:
            data["finalExam"] = yes(ask(f"O último atendimento ({s['lastVisit']}) foi 'MVA - Final Exam'? (s/n)", "s", ["s", "n", "y", "sim", "nao", "não", "no", "yes"]))
        data["assignmentPages"] = [int(x) for x in (args.assign_pages or ",".join(map(str, c["default_assignment_pages"]))).split(",")]
        if args.tracking:
            data["tracking"] = args.tracking

        (folder / "patient.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
        write_letters(c, eng, data, folder, intake, stmts)

        if args.inactivate:
            say("Marcar como Inactive: Demographics → Update Patient → chave 'Active' → Save. Abrindo a tela…")
            page.goto(pat_url + "/overview/profile"); page.wait_for_timeout(3000)
            input("  Desligue 'Active', salve, e aperte Enter aqui…")
        ctx.close()

def write_letters(c, eng, data, folder, intake, stmts):
    say("Gerando as cartas…")
    if data.get("assignmentPages"):
        warn(f"Irrevocable Assignment = páginas {data['assignmentPages']} do Intake — confira o 'Lien.pdf' gerado.")
    r = eng.generate(data, intake, stmts if data.get("lor") else [])
    letters = c["letters_dir"] / r["lettersFolder"]
    letters.mkdir(parents=True, exist_ok=True)
    for f in r["files"]:
        dest = (letters if f["folder"] == "letters" else folder) / f["name"]
        dest.write_bytes(base64.b64decode(f["b64"]))
        ok(str(dest).replace(str(Path.home()), "~"))
    say("Pronto.")

def cmd_letters(args):
    c = cfg()
    folder = next((d for d in c["discharge_dir"].glob(f"{args.mrn} - *") if d.is_dir()), None)
    if not folder or not (folder / "patient.json").exists():
        sys.exit("patient.json não encontrado — rode primeiro:  primecare_run.py patient MRN")
    data = json.loads((folder / "patient.json").read_text())
    data["letterDate"] = args.date or today()
    intake = next(iter(sorted(folder.glob("*Intake*.pdf"))), None)
    stmts = sorted(folder.glob("*Statement*.pdf"))
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="chrome")
        except Exception:
            b = pw.chromium.launch()
        ctx = b.new_context()
        write_letters(c, Engine(ctx), data, folder, intake, stmts)
        b.close()

def cmd_finals(args):
    c = cfg()
    day = args.date or (dt.date.today() - dt.timedelta(days=1)).strftime("%m/%d/%Y")
    with sync_playwright() as pw:
        ctx = launch(pw, c)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ct = CT(c, page)
        found = []
        def grab(resp):
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            try:
                body = resp.text()
            except Exception:
                return
            if "Final" in body:
                for m in re.finditer(r'\{[^{}]*?Final Exam[^{}]*\}', body):
                    found.append(m[0])
        page.on("response", grab)
        say("Abrindo o ChiroTouch…"); ct.open()
        page.goto(c["practice_url"] + "/#/practice-ledger/ledger/activity/encounters"); page.wait_for_timeout(3000)
        say(f"No Chrome, filtre From/To = {day} na lista de Encounters (se eu não conseguir sozinho).")
        try:
            for lbl in ("From", "To"):
                f = page.get_by_label(re.compile(rf"^{lbl}", re.I)).first
                f.fill(day, timeout=4000); f.press("Tab")
        except Exception:
            input("  Ajuste o filtro de datas e aperte Enter…")
        page.wait_for_timeout(5000)
        names = set()
        for blob in found:
            d = re.search(r'"(?:patientName|name)"\s*:\s*"([^"]+)"', blob)
            n = re.search(r'"(?:medicalRecordNumber|mrn)"\s*:\s*"?(\d+)', blob)
            if d or n:
                names.add(f"{n[1] if n else '?'} - {d[1] if d else '?'}")
        if names:
            say(f"Final Exam em {day}:"); [print("   •", x) for x in sorted(names)]
        else:
            say("Não identifiquei automaticamente. Veja a lista na tela do Chrome (coluna do tipo de encounter = 'MVA - Final Exam').")
            input("  Aperte Enter para fechar…")
        ctx.close()

def main():
    ap = argparse.ArgumentParser(description="Primecare — Discharge Packet (automação local)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("patient", help="baixa documentos do ChiroTouch e gera todas as cartas")
    a.add_argument("mrn")
    a.add_argument("--lor", help="s/n (se omitido, pergunta)")
    a.add_argument("--final-exam", help="s/n (se omitido, pergunta); n = gera Letter 112")
    a.add_argument("--assign-pages", help="páginas do Irrevocable Assignment no Intake, ex. 9,10")
    a.add_argument("--tracking", help="número do certified mail")
    a.add_argument("--redownload", action="store_true", help="baixa de novo mesmo se os PDFs já existem")
    a.add_argument("--inactivate", action="store_true", help="no fim, abre a tela para marcar Inactive")
    a.set_defaults(fn=cmd_patient)
    l = sub.add_parser("letters", help="regenera só as cartas a partir do patient.json")
    l.add_argument("mrn"); l.add_argument("--date", help="data da carta MM/DD/YYYY (padrão: hoje)")
    l.set_defaults(fn=cmd_letters)
    f = sub.add_parser("finals", help="lista quem teve Final Exam (padrão: ontem)")
    f.add_argument("--date"); f.set_defaults(fn=cmd_finals)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
