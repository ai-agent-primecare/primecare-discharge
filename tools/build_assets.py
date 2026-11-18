import pymupdf as fitz, shutil, sys
SRC=sys.argv[1] if len(sys.argv)>1 else './src/'  # folder with the approved sample letters; OUT='docs/assets/'
NONE=dict(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
# 1) letterhead: Carta template with all body text removed (footer kept)
d=fitz.open(SRC+'Carta de Agradecimento.pdf'); p=d[0]
p.add_redact_annot(fitz.Rect(0,0,612,700), fill=False); p.apply_redactions(**NONE)
pass  # carta letterhead alone is not needed
# 2) carta blank: template minus variable fields
d=fitz.open(SRC+'Carta de Agradecimento.pdf'); p=d[0]
for r in [(80,115,300,130.5),(80,145,300,186),(118,202,300,216.5),(80,244,400,259.5),(446.5,328,560,341.5)]:
    p.add_redact_annot(fitz.Rect(*r), fill=False)
p.apply_redactions(**NONE)
d.save(OUT+'carta_blank.pdf', garbage=4, deflate=True)
for f in ['Carlito-Regular','Carlito-Bold','Carlito-Italic','Carlito-BoldItalic']:
    shutil.copy(f'/usr/share/fonts/truetype/crosextra/{f}.ttf', OUT+'fonts/')
shutil.copy('/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf', OUT+'fonts/')
shutil.copy(SRC+'Primecare - Signature.png', OUT+'signature.png')
for f in ['carta_blank.pdf']:
    t=fitz.open(OUT+f)[0].get_text(); print(f, repr(t))
# 3) letterhead for Word-made letters (watermark at the docx position): Lucas lien minus text + signature
d=fitz.open(SRC+'2830 - Lucas Santos Sena/Lucas Santos Sena - Lien Notice (Without LOR).pdf'); p=d[0]
for im in p.get_images(full=True):
    if im[2]==131: p.delete_image(im[0])
p.add_redact_annot(fitz.Rect(0,0,612,700), fill=False); p.apply_redactions(**NONE)
for r in [(110,194,182,196.5),(84,254,176,270.5)]:
    p.add_redact_annot(fitz.Rect(*r), fill=False)
p.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
d.save(OUT+'letterhead_doc.pdf', garbage=4, deflate=True)
t=fitz.open(OUT+'letterhead_doc.pdf')[0]; print('doc lh', repr(t.get_text()), [i[2] for i in t.get_images()], len(t.get_drawings()))
# 4) pre-subset fonts to Latin-1 + common punctuation (pdf-lib's own subsetter drops glyphs on Carlito)
import subprocess
U='U+0020-007E,U+00A0-00FF,U+2013,U+2014,U+2018,U+2019,U+201C,U+201D,U+2022,U+2026,U+20AC'
fonts={'Carlito-Regular':'/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf','Carlito-Bold':'/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf',
 'Carlito-Italic':'/usr/share/fonts/truetype/crosextra/Carlito-Italic.ttf','Carlito-BoldItalic':'/usr/share/fonts/truetype/crosextra/Carlito-BoldItalic.ttf',
 'DejaVuSerif-Italic':'/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf','LiberationSans-Regular':'/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'}
for n,src in fonts.items():
    subprocess.run(['pyftsubset',src,f'--unicodes={U}',f'--output-file={OUT}fonts/{n}.ttf','--layout-features=kern','--no-hinting'],check=True)

# 5) strip metadata so no sample-patient info survives in the templates
for n in ['letterhead_doc.pdf','carta_blank.pdf']:
    d=fitz.open(OUT+n); d.set_metadata({}); d.del_xml_metadata(); d.save(OUT+n+'.tmp',garbage=4,deflate=True); d.close()
    import os; os.replace(OUT+n+'.tmp',OUT+n)
