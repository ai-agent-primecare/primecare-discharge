/* Reads a ChiroTouch "Itemized Statement of Charges" PDF and pulls out the numbers the letters need.
 * Requires global pdfjsLib (pdfjs-dist 4.x). Works on the text layer only (Statements are real text).
 */
(function (root) {
  'use strict';

  // Rebuild visual lines from pdf.js text items (group by y, sort by x).
  async function pdfLines(bytes) {
    const pdf = await root.pdfjsLib.getDocument({ data: bytes.slice ? bytes.slice(0) : bytes, isEvalSupported: false }).promise;
    const lines = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const tc = await page.getTextContent();
      const rows = [];
      for (const it of tc.items) {
        if (!it.str || !it.str.trim()) continue;
        const y = it.transform[5], x = it.transform[4];
        let row = rows.find(r => Math.abs(r.y - y) < 2.5);
        if (!row) { row = { y, items: [] }; rows.push(row); }
        row.items.push({ x, w: it.width || 0, s: it.str });
      }
      rows.sort((a, b) => b.y - a.y);
      for (const r of rows) {
        r.items.sort((a, b) => a.x - b.x);
        let s = '', end = null;
        for (const it of r.items) {
          if (end !== null) s += (it.x - end > 8) ? '  ' : ' ';
          s += it.s.trim(); end = it.x + it.w;
        }
        lines.push(s);
      }
    }
    return lines;
  }

  const MONEY = /\$[\d,]+\.\d{2}/g;
  const toNum = s => parseFloat(s.replace(/[$,]/g, ''));
  const dkey = s => { const [m, d, y] = s.split('/'); return `${y}${m}${d}`; };

  function parseLines(lines) {
    const r = { visits: [], charges: 0, paid: 0, attorney: null, patientLine: '', mrn: '', statementDate: '' };
    // Attorney block: the lines right after the "NPI: ... Tax ID" line, before "Itemized Statement"
    const npi = lines.findIndex(l => /NPI:/.test(l));
    const item = lines.findIndex(l => /Itemized Statement/i.test(l));
    if (npi >= 0 && item > npi) {
      const blk = lines.slice(npi + 1, item).map(s => s.trim()).filter(Boolean);
      const phone = blk.find(s => /^\(?\d{3}\)?[\s.-]*\d{3}-\d{4}$/.test(s));
      const addr = blk.filter(s => s !== phone);
      if (addr.length) r.attorney = { name: addr[0] || '', addr1: addr[1] || '', addr2: addr[2] || '', phone: phone || '' };
    }
    const sd = lines.findIndex(l => /Statement Date/i.test(l));
    if (sd >= 0) { const m = lines.slice(sd, sd + 4).join(' ').match(/\d{2}\/\d{2}\/\d{4}/); if (m) r.statementDate = m[0]; }
    const pi = lines.findIndex(l => /^Patient:/.test(l.trim()));
    if (pi >= 0) {
      let s = lines[pi].replace(/^Patient:\s*/, '');
      if (/-\s*$/.test(s) && lines[pi + 1] && /^\d+$/.test(lines[pi + 1].trim())) s += ' ' + lines[pi + 1].trim();
      r.patientLine = s.replace(/\s+/g, ' ').trim();
      const m = r.patientLine.match(/^(.*?),\s*(.*?)\s*-\s*(\d+)\s*$/);
      if (m) { r.last = m[1].trim(); r.first = m[2].trim(); r.mrn = m[3]; r.fullName = `${r.first} ${r.last}`; }
    }
    let section = 'proc';
    for (const l of lines) {
      if (/Payer Payments/i.test(l)) section = 'pay';
      if (/Patient Payments/i.test(l)) section = 'ppay';
      const m = l.match(/^(\d{2}\/\d{2}\/\d{4})\s+(\d{5})\b/);
      if (m && section === 'proc') r.visits.push({ date: m[1], code: m[2] });
      if (/^Totals\b/.test(l.trim()) || /\bTotals\s+\$/.test(l)) {
        const ms = l.match(MONEY) || [];
        if (section === 'proc' && ms.length && !r.charges) r.charges = toNum(ms[0]);
        else if ((section === 'pay' || section === 'ppay') && ms.length) r.paid += toNum(ms[ms.length - 1]);
      }
    }
    const dates = [...new Set(r.visits.map(v => v.date))].sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);
    r.firstVisit = dates[0] || '';
    r.lastVisit = dates[dates.length - 1] || '';
    const init = r.visits.filter(v => v.code === '99203').map(v => v.date).sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);
    const fin = r.visits.filter(v => v.code === '99213').map(v => v.date).sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);
    r.initialExam = init[0] || r.firstVisit;
    r.lastReExam = fin[fin.length - 1] || '';
    r.finalExamOnLastVisit = !!r.lastReExam && r.lastReExam === r.lastVisit;
    return r;
  }

  // Combine one or more statements (e.g. "Statement 1" and "Statement 2")
  function combine(list) {
    const all = { visits: [], charges: 0, paid: 0, attorney: null };
    for (const s of list) {
      all.visits.push(...s.visits); all.charges += s.charges; all.paid += s.paid;
      if (s.attorney && s.attorney.name) all.attorney = s.attorney;
      for (const k of ['mrn', 'fullName', 'first', 'last', 'statementDate']) if (s[k] && !all[k]) all[k] = s[k];
    }
    const x = parseLines([]); Object.assign(x, all);
    const dates = [...new Set(all.visits.map(v => v.date))].sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);
    x.firstVisit = dates[0] || ''; x.lastVisit = dates[dates.length - 1] || '';
    const sorted = c => all.visits.filter(v => v.code === c).map(v => v.date).sort((a, b) => dkey(a) < dkey(b) ? -1 : 1);
    x.initialExam = sorted('99203')[0] || x.firstVisit;
    const f = sorted('99213'); x.lastReExam = f[f.length - 1] || '';
    x.finalExamOnLastVisit = !!x.lastReExam && x.lastReExam === x.lastVisit;
    return x;
  }

  async function readStatement(bytes) { return parseLines(await pdfLines(bytes)); }

  root.PC = Object.assign(root.PC || {}, { readStatement, parseStatementLines: parseLines, combineStatements: combine, pdfLines });
})(typeof window !== 'undefined' ? window : globalThis);
