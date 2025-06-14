from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
import google.generativeai as genai
from PIL import Image
from pdf2image import convert_from_bytes
import tempfile
import os
import re
from fpdf import FPDF
import arabic_reshaper
from bidi.algorithm import get_display

# === Initialize FastAPI ===
app = FastAPI()

# === Configure Gemini ===
api_key = os.environ.get("Rue")
if not api_key:
    raise RuntimeError("❌ Missing Gemini API key. Set secret 'Rue' in Hugging Face.")
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-1.5-flash")

# === Prompt ===
PROMPT = """You are a highly precise data analyst specializing in interpreting complex reports for a general audience. Your task is to analyze the provided text from a clinical or body composition report.
Part 1: Internal Analysis (Your Thought Process)
Before generating any output, you must perform the following steps internally:
Analyze Layout: First, understand the report's structure. If there are tables, you must read them horizontally, one full row at a time. Connect the test name on the left with its corresponding 'Result' and 'Normal Range' in the same row. This is the most important step to avoid errors.
Verify Data: Carefully double-check every number you extract for accuracy. Be meticulous with decimal points.
Filter Information: Strictly ignore all personal patient information (like name, age, gender) and administrative details (like dates, report ID). Focus only on the test results.
Part 2: Final Output Generation (The Required Structure)
After completing your internal analysis, your entire output must follow this exact five-part structure and use these precise headings. All output should be in English, except for the 'Comment' under 'Analysis of Your Results,' which must be in Egyptian Arabic.
1. Analysis of Your Results
(For each test indicator found in the report, provide a comment. Follow this format for every single one.)
Indicator: [Name of the Test]
Your Result: [Value from the report]
Normal Range: [Range from the report]
Comment: [Provide the explanation in Egyptian Arabic. For example: ".مستوى الهيموجلوبين عندك اللي بيساعد على نقل الأكسجين في الدم في المعدل الطبيعي والصحي" only arabic should be in this]
2. Recommendations
(Based on all the results, provide a bulleted list of actionable recommendations in in Egyptian arabic.)
To Improve: [Simple, practical advice for any abnormal results.]
To Maintain: [Encouraging tips to keep the good results.]
3. Summary
(Provide a brief, easy-to-understand paragraph in English that summarizes the overall findings.)
"Overall, this report shows that..."
4. Final Score
(Provide the score found on the report or calculate one. Explain its meaning in Egyptian arabic.)
Your Health Score: [Score]
Metric:[Explanation of the score's meaning in Egyptian arabic.]
5. Medical Disclaimer
(Conclude with this exact, mandatory statement in in Egyptian arabic.)
"""


# === PDF Class ===
class FinalPerfectPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=False)
        self.add_page()

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        self.add_font("DejaVu", "", font_path, uni=True)
        self.add_font("DejaVu", "B", font_bold, uni=True)
        self.set_font("DejaVu", "", 13)

    def check_space(self, needed_height):
        if self.get_y() + needed_height > 260:
            self.add_page()

    def write_arabic_multiline(self, text, height=8, bullet=False):
        text = str(text).strip()
        available_width = self.w - self.l_margin - self.r_margin
        bullet_space = self.get_string_width("• ") if bullet else 0
        reshaped = arabic_reshaper.reshape(text)
        display_text = get_display(reshaped)

        if bullet:
            display_text = f"{get_display('•')} {display_text}"

        if self.get_string_width(display_text) <= available_width:
            self.multi_cell(0, height, display_text, align="R")
            return

        words = text.split()
        lines_of_words = []
        current_line_words = []

        for word in words:
            candidate_line = " ".join(current_line_words + [word])
            reshaped_line = arabic_reshaper.reshape(candidate_line)
            display_line = get_display(reshaped_line)
            if bullet and not lines_of_words:
                display_line = f"{get_display('•')} {display_line}"
            if self.get_string_width(display_line) <= available_width:
                current_line_words.append(word)
            else:
                if current_line_words:
                    lines_of_words.append(current_line_words)
                current_line_words = [word]

        if current_line_words:
            lines_of_words.append(current_line_words)

        for i, word_list in enumerate(lines_of_words):
            full_line = " ".join(word_list)
            reshaped_line = arabic_reshaper.reshape(full_line)
            display_line = get_display(reshaped_line)
            if bullet and i == 0:
                display_line = f"{get_display('•')} {display_line}"
            self.multi_cell(0, height, display_line, align="R")

    def add_section(self, title):
        self.ln(6)
        self.check_space(20)
        self.set_font("DejaVu", "B", 14)
        self.set_text_color(195, 60, 84)
        self.cell(0, 10, title, ln=True)
        self.set_text_color(0, 0, 0)
        self.set_font("DejaVu", "", 12)
        self.ln(6)

    def add_test_result(self, indicator, result, normal_range, comment):
        self.check_space(40)
        y_start = self.get_y()
        self.set_x(12)
        self.set_font("DejaVu", "B", 12)
        h1 = self.get_string_width(f"Indicator: {indicator}") / 190 * 8 + 8
        self.set_font("DejaVu", "", 12)
        h2 = self.get_string_width(f"Result: {result}") / 190 * 7 + 7
        h3 = self.get_string_width(f"Normal Range: {normal_range}") / 190 * 7 + 7
        h4 = len(comment) / 80 * 7 + 7
        total_height = h1 + h2 + h3 + h4 + 6

        self.set_fill_color(245, 245, 245)
        self.set_draw_color(210, 210, 210)
        self.set_line_width(0.2)
        self.rect(10, y_start, 190, total_height, "DF")

        self.set_y(y_start + 2)
        self.set_x(12)
        self.set_font("DejaVu", "B", 12)
        self.multi_cell(0, 8, f"Indicator: {indicator}")

        self.set_font("DejaVu", "", 12)
        self.set_x(12)
        self.multi_cell(0, 7, f"Result: {result}")
        self.set_x(12)
        self.multi_cell(0, 7, f"Normal Range: {normal_range}")
        self.set_x(12)
        if is_arabic(comment):
            self.write_arabic_multiline(comment, height=7)
        else:
            self.multi_cell(0, 7, comment)

        self.set_y(y_start + total_height + 2)

    def add_bullets(self, title, items):
        estimated_height = 10 + len(items) * 8 + 4
        if self.get_y() + estimated_height > 260:
            self.add_page()

        self.set_font("DejaVu", "B", 13)
        self.cell(0, 10, title, ln=True)
        self.set_font("DejaVu", "", 12)
        for item in items:
            if is_arabic(item):
                self.write_arabic_multiline(item.strip(" .،:"), height=8, bullet=True)
            else:
                self.multi_cell(0, 8, f"• {item}")
        self.ln(2)

    def add_paragraph(self, text):
        self.check_space(10)
        self.set_font("DejaVu", "", 12)
        if is_arabic(text):
            self.write_arabic_multiline(text.strip(), height=8)
        else:
            self.multi_cell(0, 8, text.strip())


# === Helpers ===
def is_image(filename: str) -> bool:
    return filename.lower().endswith((".png", ".jpg", ".jpeg"))


def load_images(file: UploadFile):
    if is_image(file.filename):
        return [Image.open(file.file)]
    elif file.filename.lower().endswith(".pdf"):
        return convert_from_bytes(file.file.read())
    else:
        raise ValueError("Unsupported file format. Use JPG, PNG, or PDF.")


def is_arabic(text):
    return any("\u0600" <= c <= "\u06ff" for c in text)


# === Analyze Endpoint ===
@app.post("/analyze/")
async def analyze_report(file: UploadFile = File(...)):
    try:
        images = load_images(file)
        response_text = ""
        for img in images:
            gemini_rsp = model.generate_content([PROMPT, img], stream=False)
            response_text += gemini_rsp.text + "\n"

        pdf = FinalPerfectPDF()
        rsp = response_text

        # Section 1
        pdf.add_section("1. Analysis of Your Results")
        results_section = rsp.split("**2. Recommendations**")[0]
        tests = results_section.split("Indicator:")
        for test in tests[1:]:
            lines = test.strip().splitlines()
            indicator = lines[0].strip()
            result = next(
                (l.split(":", 1)[1].strip() for l in lines if "Your Result:" in l), ""
            )
            normal = next(
                (l.split(":", 1)[1].strip() for l in lines if "Normal Range:" in l), ""
            )
            comment = next(
                (l.split(":", 1)[1].strip() for l in lines if "Comment:" in l), ""
            )
            pdf.add_test_result(indicator, result, normal, comment)

        # Section 2
        pdf.add_section("2. Recommendations")
        rec_section = rsp.split("**2. Recommendations**")[1].split("**3. Summary**")[0]
        to_improve, to_maintain, current = [], [], None
        for line in rec_section.splitlines():
            line = line.strip()
            if "To Improve" in line:
                current = to_improve
            elif "To Maintain" in line:
                current = to_maintain
            elif line.startswith("*") and current is not None:
                current.append(line[1:].strip())
        pdf.add_bullets("To Improve:", to_improve)
        pdf.add_bullets("To Maintain:", to_maintain)

        # Section 3
        pdf.add_section("3. Summary")
        summary = rsp.split("**3. Summary**")[1].split("**4. Final Score**")[0].strip()
        pdf.add_paragraph(summary)

        # Section 4
        pdf.add_section("4. Final Score")
        score = (
            rsp.split("**4. Final Score**")[1]
            .split("**5. Medical Disclaimer**")[0]
            .strip()
        )
        for line in score.splitlines():
            line = line.strip()
            if "Metric:" in line:
                line = line.replace("Metric:", "").strip()
            pdf.add_paragraph(line)

        # Section 5
        pdf.add_section("5. Medical Disclaimer")
        disclaimer = rsp.split("**5. Medical Disclaimer**")[1].strip().strip('"')
        pdf.add_paragraph(disclaimer)

        temp_dir = tempfile.gettempdir()
        output_path = os.path.join(temp_dir, "health_report.pdf")
        pdf.output(output_path)

        return {
            "message": "✅ Report analyzed and PDF generated.",
            "gemini_text": response_text,
            "pdf_file_url": "/download",
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/download")
def download_pdf():
    temp_dir = tempfile.gettempdir()
    path = os.path.join(temp_dir, "health_report.pdf")
    return FileResponse(
        path, filename="health_report.pdf", media_type="application/pdf"
    )


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html>
        <head><title>Clinical Report Analyzer</title></head>
        <body>
            <h2>🚀 FastAPI is running!</h2>
            <p>Visit <a href='/docs'>/docs</a> to use the API.</p>
        </body>
    </html>
    """
