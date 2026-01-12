import 
import uuid
import re
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pdf2image import convert_from_path
from PIL import Image
import google.generativeai as genai
from fpdf import FPDF
import arabic_reshaper
from bidi.algorithm import get_display


app = FastAPI(
    title="Report Analyzer API",
    description="Upload a report (PDF/Image) to get a detailed analysis and a downloadable PDF summary.",
    version="1.0.0"
)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

API_KEY = os.getenv("Rue")
if not API_KEY:
    raise ValueError("FATAL: GOOGLE_API_KEY environment variable not set.")
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")


# --- PDF LAYOUT HELPERS ---
def process_arabic(text):
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def clean_superscripts(text):
    return re.sub(r"<sup>(.*?)</sup>", r"^\1", text)


def is_arabic(text):
    return any('\u0600' <= c <= '\u06FF' for c in text)


# (Keep all your other code the same)


class FinalPerfectPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=False)
        self.add_page()
        # Make sure your font paths are correct for your environment
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        font_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        self.add_font("DejaVu", "", font_path, uni=True)
        self.add_font("DejaVu", "B", font_bold, uni=True)
        self.set_font("DejaVu", "", 13)

    def check_space(self, needed_height):
        if self.get_y() + needed_height > 260:
            self.add_page()

    def write_arabic_multiline(self, text, height=8, bullet=False):
        """
        Processes and writes a multi-line Arabic string to the PDF,
        letting FPDF's multi_cell handle the wrapping.
        """
        text = str(text).strip()
        if not text:
            return

        # Reshape the entire paragraph at once for correct contextual forms.
        reshaped_text = arabic_reshaper.reshape(text)
        display_text = get_display(reshaped_text)

        if bullet:
            # Prepend the bullet to the properly shaped text.
            display_text = f".{display_text} {get_display('•')}"

        # Use multi_cell ONCE.
        # width=0 makes it span the full available page width.
        # align='R' handles the right-to-left alignment and wrapping correctly.
        # ln=1 moves the cursor down after the cell is drawn.
        self.multi_cell(0, height, display_text, align='R', ln=1)

    def add_section(self, title):
        self.check_space(20)
        self.ln(2)
        self.set_font("DejaVu", "B", 14)
        self.set_text_color(195, 60, 84)
        self.cell(0, 10, title, ln=True)
        self.set_text_color(0, 0, 0)
        self.set_font("DejaVu", "", 12)
        #self.ln(6)

    def add_test_result(self, indicator, result, normal_range, comment):
        # This function doesn't need to change, as it correctly calls
        # the (now fixed) write_arabic_multiline method.
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
        self.rect(10, y_start, 190, total_height, 'DF')
        self.set_y(y_start + 2)
        self.set_x(12)
        self.set_font("DejaVu", "B", 12)
        self.multi_cell(0, 8, f"Indicator: {indicator}")
        self.set_font("DejaVu", "", 12)
        self.set_x(12)
        self.multi_cell(0, 7, f"Result: {clean_superscripts(result)}")
        self.set_x(12)
        self.multi_cell(0, 7, f"Normal Range: {clean_superscripts(normal_range)}")
        self.set_x(12)
        if is_arabic(comment):
            self.write_arabic_multiline(comment, height=7)
        else:
            self.multi_cell(0, 7, comment)
        self.set_y(y_start + total_height + 2)

    def add_bullets(self, title, items):
        # This function also requires no changes.
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
                self.multi_cell(0, 8, f"{item}•")
        self.ln(2)

    def add_paragraph(self, text):
        # No changes needed here either.
        self.check_space(10)
        self.set_font("DejaVu", "", 12)
        if is_arabic(text):
            self.write_arabic_multiline(text.strip(), height=8)
        else:
            self.multi_cell(0, 8, text.strip())


# --- FASTAPI ENDPOINTS ---


@app.post("/analyze_report/", summary="Analyze a report and generate a styled PDF")
async def analyze_report(file: UploadFile = File(...)):
    temp_file_path = None
    try:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".pdf", ".jpg", ".jpeg", ".png"]:
            raise HTTPException(status_code=400, detail="Unsupported file format. Use PDF, JPG, or PNG.")
        temp_file_path = f"/tmp/{uuid.uuid4()}{ext}"
        with open(temp_file_path, "wb") as f:
            f.write(await file.read())

        if ext == ".pdf":
            images = convert_from_path(temp_file_path, timeout=120)
        else:
            images = [Image.open(temp_file_path)]

        gemini_input = [ # prompt first, then images
            """You are a highly precise data analyst specializing in interpreting complex reports for a general audience. Your task is to analyze the provided text from a clinical or body composition report.

            Part 1: Internal Analysis (Your Thought Process)

            Before generating any output, you must perform the following steps internally:

            Analyze Layout: First, understand the report's structure. If there are tables, you must read them horizontally, one full row at a time. Connect the test name on the left with its corresponding 'Result' and 'Normal Range' in the same row. This is the most important step to avoid errors.

            Verify Data: Carefully double-check every number you extract for accuracy. Be meticulous with decimal points.

            Filter Information: Strictly ignore all personal patient information (like name, age, gender) and administrative details (like dates, report ID). Focus only on the test results.

            Part 2: Final Output Generation (The Required Structure)

            After completing your internal analysis, your entire output must follow this exact five-part structure and use these precise headings. All output should be in English, except for the 'Comment' under 'Analysis of Your Results,' which must be in Egyptian Arabic with 15 words max.

            1. Analysis of Your Results

            (For each test indicator found in the report, provide a comment. Follow this format for every single one.)

            Indicator: [Name of the Test]

            Your Result: [Value from the report]

            Normal Range: [Range from the report]

            Comment: [Provide the explanation in Egyptian Arabic. For example: "مستوى الهيموجلوبين عندك اللي بيساعد على نقل الأكسجين في الدم في المعدل الطبيعي والصحي" only arabic.]

            2. Recommendations

            (Based on all the results, provide a bulleted list of actionable recommendations in in Egyptian arabic.)

            To Improve: [Simple, practical advice for any abnormal results each one fits only one line.]

            To Maintain: [Encouraging tips to keep the good results each one fits only one line.]

            3. Summary

            (Provide a brief, easy-to-understand paragraph in English that summarizes the overall findings.)

            "Overall, this report shows that..."

            4. Final Score

            (Provide the score found on the report or calculate one. Explain its meaning in Egyptian arabic that fits one line in a pdf only.)

            Your Health Score: [Score/100]

            Metric:[Explanation of the score's meaning in Egyptian arabic that fits one line in a pdf only.]

            5. Medical Disclaimer

            (Conclude with this exact, mandatory statement in Egyptian arabic that fits one line in a pdf only.)

            

            """

        ] + images
        response = model.generate_content(gemini_input, stream=False)
        rsp = response.text

        pdf = FinalPerfectPDF()

        # --- SECTION 1: Results ---
        try:
            pdf.add_section("1. Analysis of Your Results")
            results_section = rsp.split("**2. Recommendations**")[0]
            tests = results_section.split("Indicator:")[1:]
            for test in tests:
                lines = test.strip().splitlines()
                indicator = lines[0].strip()
                result = next((l.split(":", 1)[1].strip() for l in lines if "Your Result:" in l), "")
                normal = next((l.split(":", 1)[1].strip() for l in lines if "Normal Range:" in l), "")
                comment = next((l.split(":", 1)[1].strip() for l in lines if "Comment:" in l), "")
                pdf.add_test_result(indicator, result, normal, comment)
        except Exception:
            pass

        # --- SECTION 2: Recommendations ---
        try:
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
        except Exception:
            pass

        # --- SECTION 3: Summary ---
        try:
            pdf.add_section("3. Summary")
            summary = rsp.split("**3. Summary**")[1].split("**4. Final Score**")[0].strip()
            pdf.add_paragraph(summary)
        except Exception:
            pass

        # --- SECTION 4: Final Score ---
        try:
            pdf.add_section("4. Final Score")
            score_text = rsp.split("**4. Final Score**")[1].split("**5. Medical Disclaimer**")[0].strip()
            score_text=score_text.replace("Metric:", "")
            pdf.add_paragraph(score_text)
        except Exception:
            pass

        # --- SECTION 5: Medical Disclaimer ---
        try:
            pdf.add_section("5. Medical Disclaimer")
            disclaimer = rsp.split("**5. Medical Disclaimer**")[1].strip().strip('"')
            pdf.add_paragraph(disclaimer)
        except Exception:
            pass

        pdf_filename = f"{uuid.uuid4()}.pdf"
        pdf_output_path = os.path.join("static", pdf_filename)
        pdf.output(pdf_output_path)
        pdf_url = f"/static/{pdf_filename}"

        return JSONResponse(content={
            "gemini_response": rsp,
            "pdf_url": pdf_url
        })

    except Exception as e:
        import traceback
        print("==== ERROR OCCURRED ====")
        traceback.print_exc()
        print("========================")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
