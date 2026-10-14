from pypdf import PdfReader
import pypdfium2 as p

# reads the PDF that test_reports.py writes (run test_reports.py first)
r = PdfReader("/tmp/wpc_report_test/report.pdf")
print("pages:", len(r.pages))
txt = "\n".join(pg.extract_text() for pg in r.pages)
print("chars:", len(txt))
for probe in ["WikiPortraits Eval report", "Method", "Summary", "ranked by P(infobox-ready)",
              "Per-image determinations", "infobox_ready", "commons.wikimedia.org/wiki"]:
    print(f"  {probe!r:38s} present={probe in txt}")
print("\n--- page 1 text ---")
print(r.pages[0].extract_text()[:1200])

pdf = p.PdfDocument("/tmp/wpc_report_test/report.pdf")
for i in range(len(pdf)):
    pdf[i].render(scale=1.5).to_pil().save(f"/tmp/app_pdf_{i+1}.png")
print("\nrendered", len(pdf), "pages to /tmp/app_pdf_*.png")
