/* Primecare Discharge Packet engine.
 * Builds the letters (Discharge Packet, Lien Notice, Lien Notice Final, Carta de Agradecimento,
 * Letter 112) as PDFs on top of the clinic letterhead, entirely in the browser (or Node for tests).
 * Requires globals: PDFLib (pdf-lib 1.17.1), fontkit (@pdf-lib/fontkit 1.1.1).
 * Nothing here talks to the network except loading the files in ./assets.
 */
(function (root) {
  'use strict';
  const PAGE_H = 792;
  const ASC = 0.952;      // Carlito ascender (top of line box -> baseline)
  const PITCH = 1.2207;   // Word "single" line pitch for Carlito/Calibri

  // ---------- small helpers ----------
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];
  function ordinal(n) {
    const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  function parseDate(s) { // MM/DD/YYYY -> Date
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(s || '').trim());
    return m ? new Date(+m[3], +m[1] - 1, +m[2]) : null;
  }
  function fmtDate(d) {
    return String(d.getMonth() + 1).padStart(2, '0') + '/' + String(d.getDate()).padStart(2, '0') + '/' + d.getFullYear();
  }
  function today() { return fmtDate(new Date()); }
  function ordinalDate(s) { // "11/18/2026" -> "18th day of November, 2026"
    const d = parseDate(s); return ordinal(d.getDate()) + ' day of ' + MONTHS[d.getMonth()] + ', ' + d.getFullYear();
  }
  function money(x) {
    const n = typeof x === 'number' ? x : parseFloat(String(x).replace(/[$,\s]/g, '')) || 0;
    return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function num(x) { return typeof x === 'number' ? x : parseFloat(String(x || '0').replace(/[$,\s]/g, '')) || 0; }
  function firstName(full) { return String(full).trim().split(/\s+/)[0]; }
  function safe(s) { return String(s).replace(/[\/\\:*?"<>|]/g, '-').trim(); }

  // ---------- asset + font loading ----------
  async function loadAssets(loader) {
    const names = ['letterhead_doc.pdf', 'carta_blank.pdf', 'signature.png',
      'fonts/Carlito-Regular.ttf', 'fonts/Carlito-Bold.ttf', 'fonts/Carlito-Italic.ttf',
      'fonts/Carlito-BoldItalic.ttf', 'fonts/DejaVuSerif-Italic.ttf', 'fonts/LiberationSans-Regular.ttf'];
    const out = {};
    await Promise.all(names.map(async n => { out[n] = await loader(n); }));
    return out;
  }

  async function newDoc(assets, template) {
    const { PDFDocument } = root.PDFLib;
    const doc = await PDFDocument.create();
    doc.registerFontkit(root.fontkit);
    const f = {
      R: await doc.embedFont(assets['fonts/Carlito-Regular.ttf'], { subset: false }),
      B: await doc.embedFont(assets['fonts/Carlito-Bold.ttf'], { subset: false }),
      I: await doc.embedFont(assets['fonts/Carlito-Italic.ttf'], { subset: false }),
      BI: await doc.embedFont(assets['fonts/Carlito-BoldItalic.ttf'], { subset: false }),
      SCRIPT: await doc.embedFont(assets['fonts/DejaVuSerif-Italic.ttf'], { subset: false }),
      ARIAL: await doc.embedFont(assets['fonts/LiberationSans-Regular.ttf'], { subset: false }),
    };
    const tpl = await PDFDocument.load(assets[template]);
    const [embedded] = await doc.embedPdf(tpl, [0]);
    const addPage = () => {
      const p = doc.addPage([612, 792]);
      p.drawPage(embedded, { x: 0, y: 0, width: 612, height: 792 });
      return p;
    };
    return { doc, f, addPage };
  }

  // ---------- text layout ----------
  // runs: [{t:'text', s:'R'|'B'|'I'|'BI', u:bool}]
  function toWords(runs) {
    const words = []; let cur = [];
    for (const r of runs) {
      const parts = String(r.t).split(/( )/);
      for (const part of parts) {
        if (part === ' ') { if (cur.length) words.push(cur); cur = []; }
        else if (part) cur.push({ t: part, s: r.s || 'R', u: r.u });
      }
    }
    if (cur.length) words.push(cur);
    return words;
  }
  const wWidth = (f, w, size) => w.reduce((a, p) => a + f[p.s].widthOfTextAtSize(p.t, size), 0);

  function drawLine(page, f, size, x, baseline, pieces) {
    const { rgb } = root.PDFLib;
    for (const p of pieces) {
      page.drawText(p.t, { x, y: PAGE_H - baseline, size, font: f[p.s], color: rgb(0, 0, 0) });
      const w = f[p.s].widthOfTextAtSize(p.t, size);
      if (p.u) page.drawLine({ start: { x, y: PAGE_H - baseline - 1.4 }, end: { x: x + w, y: PAGE_H - baseline - 1.4 }, thickness: 0.72, color: rgb(0, 0, 0) });
      x += w;
    }
    return x;
  }

  // Lays out a paragraph; returns the top of the line after the last line.
  function para(page, f, o) {
    const size = o.size || 12, pitch = o.pitch || size * PITCH;
    const words = toWords(o.runs);
    let top = o.top, first = true, i = 0;
    while (i < words.length) {
      const x0 = first ? o.x0 + (o.indent || 0) : o.x0, avail = o.x1 - x0;
      const line = []; let w = 0;
      while (i < words.length) {
        const ww = wWidth(f, words[i], size);
        const sp = line.length ? f[words[i][0].s].widthOfTextAtSize(' ', size) : 0;
        if (line.length && w + sp + ww > avail + (o.slack ?? 2.5)) break;
        line.push(words[i]); w += sp + ww; i++;
      }
      const last = i >= words.length;
      const gaps = line.length - 1;
      const extra = (o.align === 'justify' && !last && gaps > 0) ? (avail - w) / gaps : 0;
      let x = x0; const baseline = top + ASC * size;
      line.forEach((word, k) => {
        if (k > 0) {
          const sp = f[word[0].s].widthOfTextAtSize(' ', size) + extra;
          if (word[0].u && line[k - 1][line[k - 1].length - 1].u) drawLine(page, f, size, x, baseline, [{ t: ' ', s: word[0].s, u: true }]);
          x += sp;
        }
        x = drawLine(page, f, size, x, baseline, word);
      });
      top += pitch; first = false;
    }
    return top;
  }
  function text(page, f, x, top, size, runs) {
    if (typeof runs === 'string') runs = [{ t: runs }];
    return drawLine(page, f, size, x, top + ASC * size, toWords(runs).flatMap((w, k, all) => k ? [{ t: ' ', s: w[0].s, u: w[0].u && all[k - 1][all[k - 1].length - 1].u }, ...w] : w));
  }
  function rightText(page, f, xRight, top, size, s, style) {
    const w = f[style || 'R'].widthOfTextAtSize(s, size);
    text(page, f, xRight - w, top, size, [{ t: s, s: style || 'R' }]);
  }

  // ---------- normalise patient data ----------
  function prep(p) {
    const d = Object.assign({}, p);
    d.letterDate = d.letterDate || today();
    d.name = String(d.fullName || '').replace(/\s+/g, ' ').trim();
    d.male = String(d.gender || '').toUpperCase().startsWith('M');
    d.hon = d.male ? 'Mr.' : 'Ms.';
    d.poss = d.male ? 'his' : 'her';
    d.chargesN = num(d.charges); d.paidN = num(d.paid);
    d.outN = (d.outstanding !== undefined && d.outstanding !== '') ? num(d.outstanding) : d.chargesN - d.paidN;
    d.att = d.attorney || {};
    d.noAttorney = !d.att.name || /^no attorney$/i.test(d.att.name);
    d.tracking = d.tracking && String(d.tracking).trim() ? String(d.tracking).trim() : '_________________________';
    return d;
  }

  // ---------- letters ----------
  async function dischargePacket(A, p) {
    const d = prep(p);
    const { doc, f, addPage } = await newDoc(A, 'letterhead_doc.pdf');
    // page 1 — cover letter to the attorney
    let pg = addPage();
    const X = 85.2, XR = 578.1;
    text(pg, f, X, 71.08, 12, d.letterDate);
    const firm = d.noAttorney ? ['No Attorney', '', ''] : [d.att.name, d.att.addr1 || '', d.att.addr2 || ''];
    firm.forEach((s, k) => s && text(pg, f, X, 144.33 + k * 14.65, 12, [{ t: s, s: 'B' }]));
    text(pg, f, X, 202.93, 12, 'RE:');
    [['Patient:', d.name], ['DOI:', d.dol], ['DOB:', d.dob]].forEach(([l, v], k) => {
      text(pg, f, 120.6, 202.93 + k * 14.65, 12, l); text(pg, f, 191.4, 202.93 + k * 14.65, 12, v || '');
    });
    const P = { x0: X, x1: XR, indent: 36, size: 11, align: 'justify' };
    const gap = 13.98; // empty line between paragraphs (measured from Word output)
    let top = 290.2;
    top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: `As you are aware, ${d.hon} ${d.name} was treated in our office for injuries sustained in a motor vehicle accident that occurred on ${d.dol}. The care provided during this period was directly related to ${d.poss} injuries from the aforementioned accident.` }] })) + gap;
    top = para(pg, f, Object.assign({}, P, { top, runs: [
      { t: `As of today, ${d.hon} ${d.name} has an ` }, { t: `outstanding balance of ${money(d.outN)}`, s: 'B' },
      { t: ' with our clinic. We respectfully request that you verify with our office whether any balance remains ' },
      { t: 'before', s: 'B' }, { t: ' finalizing any settlement on this case.' }] })) + gap;
    top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'Any reduction in the outstanding balance must be approved in writing and signed by our office.' }] })) + gap;
    top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: "If a settlement is reached without satisfying the patient's financial obligations to our clinic, we will be left with no alternative but to forward the account to our collection attorneys and initiate legal action to recover the full amount due." }] })) + gap;
    top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'If you have any questions or require additional information, please do not hesitate to contact us.' }] })) + gap;
    text(pg, f, 121.2, top, 11, 'Sincerely,');
    text(pg, f, X, top + 42.1, 12, 'Primecare Chiropractic');

    // page 2 — Patient Discharge Record
    pg = addPage();
    text(pg, f, 109.3, 71.3, 22, 'PATIENT DISCHARGE RECORD');
    const rows = [['DATE:', d.letterDate], ['PATIENT:', d.name], ['DATE OF LOSS:', d.dol],
      ['INITIAL EXAM:', d.initialExam], ['DISCHARGE DATE:', d.dischargeDate],
      ['DATES OF TREATMENT:', `${d.initialExam} – ${d.dischargeDate}`],
      ['TOTAL CHARGES:', money(d.chargesN)], ['TOTAL PAID:', money(d.paidN)], ['NET OUTSTANDING:', money(d.outN)]];
    rows.forEach(([l, v], k) => {
      const t = 120.1 + k * 34; const end = text(pg, f, X, t, 18, l);
      text(pg, f, Math.max(262.1, end + 4.5), t, 18, v || '');
    });
    text(pg, f, X, 447.9, 12, 'STATEMENT OF MEDICAL REPORTS UNDER M.G.L. CHAPTER 233, SECTION 79G');
    para(pg, f, { x0: X, x1: 578.5, size: 12, align: 'justify', top: 477.2, runs: [{ t: 'The undersigned, a duly authorized representative of Primecare Chiropractic, a duly licensed medical facility under the laws of the State of Massachusetts, state that the attached medical bills and medical records are true and accurate copies.' }] });
    text(pg, f, X, 535.8, 12, `Signed under the pains and penalties of perjury this ${ordinalDate(d.letterDate)}.`);
    text(pg, f, X, 594.4, 12, '______________________');
    return doc.save();
  }

  async function lienNotice(A, p) {
    const d = prep(p);
    const { doc, f, addPage } = await newDoc(A, 'letterhead_doc.pdf');
    const pg = addPage();
    const X = 85.0, XR = 578.2, L = 14.65;
    const lor = !!d.lor;
    let top = lor ? 107.9 : 94.2;
    text(pg, f, X, top, 12, d.letterDate); top += 2 * L;
    const fs = lor ? 'R' : 'B';
    [d.att.name, d.att.addr1, d.att.addr2].forEach(s => { if (s) text(pg, f, X, top, 12, [{ t: s, s: fs }]); top += L; });
    top += L;
    text(pg, f, X, top, 12, [{ t: 'Ref.: ', s: 'B' }, { t: 'Notice of LIEN', s: 'B', u: true }]); top += 2 * L;
    [`Your client: ${d.name}`, `DOB: ${d.dob}`, `Address: ${d.address1}`, d.address2, `DOL: ${d.dol}`,
      'Our Name: Primecare Chiropractic', 'Our Address: 600 Main St., Malden, MA 02148'].forEach(s => { text(pg, f, X, top, 12, s || ''); top += L; });
    top += L;
    text(pg, f, X, top, 12, [{ t: `Sent via regular and certified mail: ${d.tracking}`, s: lor ? 'R' : 'I' }]); top += 2 * L;
    const P = { x0: X, x1: XR, size: 12, align: 'justify' };
    if (lor) {
      top += 2 * L;
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'In summary, your immediate attention to this matter is imperative.' }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'Please be advised that the patient, your client, named above, has signed the enclosed “' }, { t: 'Irrevocable Assignment of Benefits, Authorization and Lien', s: 'I' }, { t: '”.' }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'At this time, your client has an outstanding balance with Primecare Chiropractic, as per the bills also enclosed.' }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'Please take this as ' }, { t: 'NOTICE', s: 'BI' }, { t: ' of our intent to be paid in full regarding the services rendered to this patient due to the motor vehicle accident that occurred on the aforementioned date.' }] }));
      top += L;
      text(pg, f, X, top, 12, 'Sincerely,');
      top += 97.2;
    } else {
      text(pg, f, X, top, 12, 'Dear Attorney,'); top += L;
      const n = d.name;
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'We are writing to bring to your immediate attention a matter concerning the Motor Vehicle Accident that happened with ' }, { t: n, s: 'B' }, { t: ' on ' }, { t: d.dol + '.', s: 'B' }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: `We have been informed that you are representing ${n} in this matter. It is imperative that we address the outstanding balance with Primecare Chiropractic for services rendered to our patient, your client, related to the aforementioned accident.` }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: `Enclosed with this letter, you will find an "Irrevocable Assignment of Benefits, Authorization, and Lien" document, duly signed by ${n}. This document authorizes us to pursue payment for the outstanding balance directly from any settlement or judgment related to the accident.` }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: `In order to facilitate the resolution of this matter, we kindly request your prompt action. We are prepared to provide you with the necessary bills and medical records as soon as we receive the Letter of Representation, along with the HIPAA authorization signed by ${n}.` }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'Please consider this letter as formal ' }, { t: 'NOTICE', s: 'BI' }, { t: ` of our intent to seek payment in full for the services provided to ${n} in connection with the MVA referenced above.` }] }));
      top = para(pg, f, Object.assign({}, P, { top, runs: [{ t: 'Thank you for your cooperation.' }] }));
      top += 82.6 - L;
    }
    // "PC" signature sits right on the signature line
    const sig = await doc.embedPng(A['signature.png']);
    const sw = 71.98, sh = sw * sig.height / sig.width;
    pg.drawImage(sig, { x: 85.05, y: PAGE_H - top, width: sw, height: sh });
    text(pg, f, X, top, 12, '____________________________');
    text(pg, f, X, top + L, 12, 'Primecare Chiropractic');
    return doc.save();
  }

  async function letter112(A, p) {
    const d = prep(p);
    const { doc, f, addPage } = await newDoc(A, 'letterhead_doc.pdf');
    const pg = addPage();
    const X = 72.1, XR = 542.85, L = 14.65, G = 32.75;
    text(pg, f, X, 121.5, 12, d.letterDate);
    text(pg, f, X, 160.8, 12, [{ t: d.name, s: 'B' }]);
    text(pg, f, X, 175.5, 12, d.address1 || '');
    text(pg, f, X, 190.1, 12, d.address2 || '');
    text(pg, f, X, 239.4, 12, [{ t: 'Ref.: Treatment for Vehicle Accident – ' }, { t: `DOL: ${d.dol}`, s: 'B' }]);
    text(pg, f, X, 278.7, 12, 'Dear Patient,');
    const P = { x0: X, x1: XR, size: 12, align: 'justify' };
    let top = 308.0;
    const paras = [
      [{ t: 'We must inform you that you have failed to show for your scheduled appointments within ' }, { t: 'the last thirty (30) days', s: 'B' }, { t: '. We have recently had difficulties on contacting you by phone, several occasions, regarding your appointment schedule, and you have never returned our messages.' }],
      [{ t: 'Since we have been unable to contact you for the last 30 days, it is impossible for us to know your intentions about your care in our clinic, reason why we will be withdrawing you from care at this time.' }],
      [{ t: 'If you wish to continue with our services, a ' }, { t: 'referral letter', s: 'B' }, { t: ' from your primary care doctor is necessary to do so.' }],
      [{ t: 'Please call us as soon as possible on the phone number (857) 410-1800 so we can provide you the care you deserve.' }],
      [{ t: 'If we do not hear from you within 10 days of this letter, we will be permanently closing your case, your attorney and insurance company will be notified of such.' }]];
    paras.forEach(r => { top = para(pg, f, Object.assign({}, P, { top, runs: r })) - L + G; });
    text(pg, f, X, top, 12, 'Warm Regards,');
    text(pg, f, X, top + 35.1, 12, [{ t: 'Management team', s: 'SCRIPT' }]);
    text(pg, f, X, top + 49.3, 12, 'Primecare Chiropractic');
    rightText(pg, f, 540.0, top + 49.3, 12, 'Letter # 112');
    return doc.save();
  }

  async function carta(A, p) {
    const d = prep(p);
    const { doc, f, addPage } = await newDoc(A, 'carta_blank.pdf');
    const pg = addPage();
    const X = 85.0;
    drawLine(pg, f, 10, X, 126.9, [{ t: d.letterDate, s: 'ARIAL' }]);
    text(pg, f, X, 147.3, 11, [{ t: d.name, s: 'B' }]);
    text(pg, f, X, 160.7, 11, d.address1 || '');
    text(pg, f, X, 174.1, 11, d.address2 || '');
    text(pg, f, 120.0, 204.3, 11, `DOI: ${d.dol}`);
    text(pg, f, X, 247.6, 11, `Dear ${d.name},`);
    const xb = 447.6;
    const end = text(pg, f, xb, 329.3, 11, [{ t: money(d.outN), s: 'B' }]);
    text(pg, f, end, 329.3, 11, '. Please');
    return doc.save();
  }

  // ---------- PDF utilities ----------
  async function extractPages(bytes, pages /* 1-based */) {
    const { PDFDocument } = root.PDFLib;
    const src = await PDFDocument.load(bytes, { ignoreEncryption: true });
    const out = await PDFDocument.create();
    const copied = await out.copyPages(src, pages.map(n => n - 1));
    copied.forEach(pg => out.addPage(pg));
    return out.save();
  }
  async function merge(list) {
    const { PDFDocument } = root.PDFLib;
    const out = await PDFDocument.create();
    for (const b of list) {
      if (!b) continue;
      const src = await PDFDocument.load(b, { ignoreEncryption: true });
      (await out.copyPages(src, src.getPageIndices())).forEach(pg => out.addPage(pg));
    }
    return out.save();
  }

  /* Build everything for one patient.
   * p: patient data (see README); files: { intake, statements:[...], assignment } as Uint8Array
   * returns [{ folder:'patient'|'letters', name, bytes }]
   */
  async function generateAll(A, p, files) {
    files = files || {};
    const d = prep(p);
    const mrn = String(d.mrn || '').trim();
    const base = `${mrn} - ${d.name}`;
    const out = [];
    const add = (folder, name, bytes) => out.push({ folder, name: safe(name), bytes });

    const onlyLetter112 = d.noAttorney && d.only112;
    if (!onlyLetter112) add('patient', `${d.name} - Discharge Packet.pdf`, await dischargePacket(A, d));

    let assignment = files.assignment || null;
    if (!assignment && files.intake && d.assignmentPages && d.assignmentPages.length) {
      assignment = await extractPages(files.intake, d.assignmentPages);
    }
    if (assignment) add('patient', `${base} - Lien.pdf`, assignment);

    if (!d.noAttorney) {
      const lien = await lienNotice(A, d);
      add('patient', `${d.name} - Lien Notice (${d.lor ? 'With' : 'Without'} LOR).pdf`, lien);
      const parts = [lien, assignment];
      if (d.lor) (files.statements || []).forEach(s => parts.push(s));
      add('patient', `${base} - Lien Notice Final.pdf`, await merge(parts));
    }
    const cartaPdf = await carta(A, d);
    add('patient', `${mrn} - ${firstName(d.name)} - Carta de Agradecimento.pdf`, cartaPdf);
    add('letters', `${mrn} - ${firstName(d.name)} - Carta de Agradecimento.pdf`, cartaPdf);
    if (d.finalExam === false) {
      const l = await letter112(A, d);
      add('patient', `${mrn} - ${firstName(d.name)} - Letter 112.pdf`, l);
      add('letters', `${mrn} - ${firstName(d.name)} - Letter 112.pdf`, l);
    }
    return { folderName: safe(base), lettersFolder: d.letterDate.replace(/\//g, '-'), files: out };
  }

  root.PC = Object.assign(root.PC || {}, {
    loadAssets, generateAll, dischargePacket, lienNotice, letter112, carta, extractPages, merge,
    util: { ordinal, ordinalDate, money, num, fmtDate, parseDate, today, firstName, safe },
  });
})(typeof window !== 'undefined' ? window : globalThis);
