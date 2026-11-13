import os
import re
import json
import time
import random
import uuid
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from flask import Flask, request, jsonify, send_from_directory

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==================================================
# 1. CONFIGURATION
# ==================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
app = Flask(__name__)
API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", os.getenv("MODEL_NAME", "gemini-3.5-flash-lite"))
client = genai.Client(api_key=API_KEY) if API_KEY else None
TASK_FILE = os.path.join(BASE_DIR, "study_tasks.json")

# ==================================================
# 2. GEMINI HELPERS
# ==================================================
def require_client():
    if client is None:
        raise RuntimeError("Gemini API key is missing. Add GEMINI_API_KEY to your .env file.")


def generate_with_retry(prompt, config=None):
    require_client()
    for attempt in range(5):
        try:
            kwargs = {"model": MODEL, "contents": prompt}
            if config is not None:
                kwargs["config"] = config
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            error_text = str(exc).upper()
            temporary_error = "503" in error_text or "UNAVAILABLE" in error_text
            if temporary_error and attempt < 4:
                wait_time = 3 * (2 ** attempt)
                app.logger.warning("Gemini temporarily unavailable. Retrying in %s seconds.", wait_time)
                time.sleep(wait_time)
                continue
            raise
    raise RuntimeError("Gemini is temporarily unavailable. Please try again later.")


def handle_error(exc, feature):
    error_text = str(exc).upper()
    app.logger.exception("%s request failed", feature)
    if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "QUOTA EXCEEDED" in error_text:
        return jsonify({"error": "Gemini API quota or rate limit reached. Check your Google AI Studio usage and try again when quota is available."}), 429
    if "503" in error_text or "UNAVAILABLE" in error_text:
        return jsonify({"error": "Gemini is temporarily busy. Please wait and try again."}), 503
    if "404" in error_text or "NOT_FOUND" in error_text:
        return jsonify({"error": "The configured Gemini model is unavailable. Check the GEMINI_MODEL setting in your .env file."}), 404
    return jsonify({"error": str(exc)}), 500


def extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("The AI did not return valid JSON. Please try again.")
    return json.loads(text[start:end + 1])


# ==================================================
# 3. LOCAL MCQ QUESTION BANK
# ==================================================
LOCAL_MCQ_BANK = [
    {"question": "Which symbol is used to create a list in Python?", "options": ["( )", "{ }", "[ ]", "< >"], "answer_index": 2, "explanation": "Square brackets are used to create a Python list."},
    {"question": "Which of the following is a valid Python list?", "options": ["[10, 20, 30]", "(10, 20, 30)", "{10: 20}", "10, 20, 30"], "answer_index": 0, "explanation": "A list is created using square brackets."},
    {"question": "Which function is used to find the number of elements in a list?", "options": ["count()", "len()", "size()", "length()"], "answer_index": 1, "explanation": "The len() function returns the number of elements."},
    {"question": "What is the index of the first element in a Python list?", "options": ["1", "-1", "0", "2"], "answer_index": 2, "explanation": "Python list indexing starts from zero."},
    {"question": "Which method adds an element to the end of a list?", "options": ["insert()", "append()", "add()", "extend()"], "answer_index": 1, "explanation": "The append() method adds one element at the end."},
    {"question": "Which method removes an element using its value?", "options": ["remove()", "pop()", "delete()", "clear()"], "answer_index": 0, "explanation": "The remove() method removes the first matching value."},
    {"question": "What is the output of len([5, 10, 15, 20])?", "options": ["3", "4", "5", "20"], "answer_index": 1, "explanation": "The list contains four elements."},
    {"question": "Which method reverses the elements of a list in place?", "options": ["sort()", "reverse()", "reversed()", "flip()"], "answer_index": 1, "explanation": "The reverse() method reverses the list in place."},
    {"question": "What does the pop() method do when no index is specified?", "options": ["Removes the first element", "Removes the last element", "Sorts the list", "Deletes the entire list"], "answer_index": 1, "explanation": "Without an index, pop() removes and returns the last element."},
    {"question": "Which operator is used to join two lists?", "options": ["+", "-", "*", "/"], "answer_index": 0, "explanation": "The + operator concatenates two lists."},
    {"question": "What is the output of [2, 4, 6][1]?", "options": ["2", "4", "6", "1"], "answer_index": 1, "explanation": "Index 1 refers to the second element, which is 4."},
    {"question": "Which method adds multiple elements from another iterable?", "options": ["append()", "extend()", "remove()", "index()"], "answer_index": 1, "explanation": "The extend() method adds elements from an iterable."},
    {"question": "Which method returns the index of a specified element?", "options": ["find()", "search()", "index()", "locate()"], "answer_index": 2, "explanation": "The index() method returns the position of the first matching element."},
    {"question": "What is the output of [1, 2, 3] * 2?", "options": ["[1, 2, 3, 2]", "[2, 4, 6]", "[1, 2, 3, 1, 2, 3]", "[1, 2, 3]"], "answer_index": 2, "explanation": "Multiplying a list by 2 repeats its elements twice."},
    {"question": "Which method removes all elements from a list?", "options": ["remove()", "pop()", "clear()", "empty()"], "answer_index": 2, "explanation": "The clear() method removes all elements from a list."}
]


def generate_local_mcqs(subject, portion, number):
    search_text = (subject + " " + portion).lower()
    is_computer_science = "computer science" in search_text or "python" in search_text or subject.strip().lower() in ("cs", "computer science")
    if "list" not in search_text or not is_computer_science:
        return None
    return random.sample(LOCAL_MCQ_BANK, min(number, len(LOCAL_MCQ_BANK)))


# ==================================================
# 4. HOME PAGE & FAVICON ROUTE
# ==================================================
@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        app.root_path,
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )

# ==================================================
# 5. UNIFIED AI TUTOR & VOICE TUTOR
# ==================================================
@app.route("/api/ask", methods=["POST"])
def ask_tutor():
    try:
        require_client()
        data = request.get_json(silent=True) or {}
        question = str(data.get("question", "")).strip()
        voice_mode = bool(data.get("voice_mode", False))
        if not question:
            return jsonify({"error": "Please enter or speak a question."}), 400

        voice_instruction = (
            "The student is using Voice Tutor mode. Keep sentences clear, conversational, and easy to follow when read aloud."
            if voice_mode else ""
        )

        prompt = f"""You are SmartStudyMate AI, a helpful school study and voice tutor.
Explain the answer clearly and accurately in clean plain text only.
{voice_instruction}

IMPORTANT FORMATTING RULES:
- Do NOT use markdown asterisks (**bold** or *italic*).
- Do NOT use hash symbols (# or ### for headings).
- Do NOT use LaTeX dollar signs ($) around math equations or numbers.
- Write math equations naturally (e.g., write 1 + 1 = 2 instead of $1 + 1 = 2$).

Instructions:
1. Use simple student-friendly language.
2. Explain difficult concepts step by step.
3. Include examples when useful.
4. Use standard numbers (1., 2., 3.) for steps.
5. For mathematics, show the clear step-by-step working.
6. If unsure, say so.
7. Do not invent facts.

Student question:
{question}
"""
        response = generate_with_retry(prompt)
        answer = response.text
        if not answer:
            raise ValueError("The AI returned an empty answer. Please try again.")
        return jsonify({"answer": answer})
    except Exception as exc:
        return handle_error(exc, "AI Tutor")


# ==================================================
# 6. ONE-MARK MCQ GENERATOR
# ==================================================
@app.route("/api/one-mark", methods=["POST"])
def one_mark():
    data = request.get_json(silent=True) or {}
    class_name = str(data.get("class", "")).strip()
    subject = str(data.get("subject", "")).strip()
    portion = str(data.get("portion", "")).strip()
    try:
        number = int(data.get("number", 10))
    except (ValueError, TypeError):
        return jsonify({"error": "Number of questions must be a whole number."}), 400
    number = max(1, min(number, 30))
    if not class_name or not subject or not portion:
        return jsonify({"error": "Please provide class, subject and syllabus portion."}), 400

    prompt = f"""You are an expert school examination question setter.
Create exactly {number} original one-mark multiple-choice questions.
CLASS: {class_name}
SUBJECT: {subject}
SYLLABUS PORTION: {portion}

Stay strictly within the specified syllabus portion. Match the difficulty to the student's class.
Make questions factually accurate. Each question must have exactly four different options and one correct answer.
Give a short explanation. Avoid duplicate questions and out-of-syllabus questions.

Return ONLY valid JSON:
{{
  "mcqs": [
    {{"question": "Question text", "options": ["Option A", "Option B", "Option C", "Option D"], "answer_index": 0, "explanation": "Short explanation"}}
  ]
}}
answer_index: 0 = first option, 1 = second, 2 = third, 3 = fourth.
Generate exactly {number} questions.
"""
    try:
        require_client()
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        result = extract_json(response.text)
        questions = result.get("mcqs", [])
        if not isinstance(questions, list) or len(questions) != number:
            raise ValueError(f"Expected {number} questions, but received {len(questions) if isinstance(questions, list) else 0}.")
        for question in questions:
            if (not isinstance(question, dict) or not isinstance(question.get("question"), str)
                or not isinstance(question.get("options"), list) or len(question["options"]) != 4
                or not all(isinstance(option, str) for option in question["options"])
                or type(question.get("answer_index")) is not int or question["answer_index"] not in (0, 1, 2, 3)
                or not isinstance(question.get("explanation"), str)):
                raise ValueError("The AI returned an invalid MCQ format.")
        return jsonify({"mcqs": questions, "sources": [], "generation_mode": "gemini"})
    except Exception as exc:
        error_text = str(exc).upper()
        quota_error = "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "QUOTA EXCEEDED" in error_text
        if quota_error:
            app.logger.warning("Gemini quota exhausted. Trying local MCQ backup.")
            local_questions = generate_local_mcqs(subject, portion, number)
            if local_questions:
                return jsonify({
                    "mcqs": local_questions, "sources": [], "generation_mode": "local",
                    "message": "Gemini quota is currently unavailable. These questions were generated from the built-in local question bank."
                })
        return handle_error(exc, "MCQ Generator")


# ==================================================
# 7. AI QUESTION PAPER GENERATOR (NEW)
# ==================================================
@app.route("/api/generate-question-paper", methods=["POST"])
def generate_question_paper():
    try:
        require_client()
        data = request.get_json(silent=True) or {}
        class_name = str(data.get("class", "")).strip()
        subject = str(data.get("subject", "")).strip()
        chapters = str(data.get("chapters", "")).strip()
        difficulty = str(data.get("difficulty", "Medium")).strip()
        duration = str(data.get("duration", "3 Hours")).strip()
        try:
            total_marks = int(data.get("total_marks", 80))
        except (ValueError, TypeError):
            return jsonify({"error": "Total marks must be a valid number."}), 400

        if not class_name or not subject or not chapters:
            return jsonify({"error": "Please select class, subject, and enter the chapters/topics."}), 400
        if not 10 <= total_marks <= 100:
            return jsonify({"error": "Total marks must be between 10 and 100."}), 400

        prompt = f"""You are a senior CBSE/school board examination paper setter.
Generate a complete, well-structured examination question paper with a complete answer key.

CLASS: {class_name}
SUBJECT: {subject}
CHAPTERS / SYLLABUS: {chapters}
TOTAL MARKS: {total_marks}
DIFFICULTY LEVEL: {difficulty}
TIME ALLOWED: {duration}

Rules:
1. Divide the paper into logical sections (e.g., Section A: 1-mark Objective/MCQ, Section B: 2-mark Short Answer, Section C: 3-mark Short Answer, Section D: 5-mark Long Answer / Case Study) so that the sum of all question marks equals EXACTLY {total_marks}.
2. Match the difficulty level ({difficulty}) and class standard ({class_name}).
3. Every single question MUST have a clear, accurate marking-scheme answer in the answer key.
4. Do NOT use LaTeX $ symbols; write formulas in clean readable plain text.

Return ONLY valid JSON in this exact format:
{{
  "paper_title": "{class_name} - {subject} Examination Paper",
  "total_marks": {total_marks},
  "duration": "{duration}",
  "difficulty": "{difficulty}",
  "general_instructions": [
    "All questions are compulsory.",
    "Marks for each question are indicated against it."
  ],
  "sections": [
    {{
      "section_name": "Section A - Objective Type Questions",
      "section_instructions": "Attempt all questions. Each question carries 1 mark.",
      "questions": [
        {{
          "q_no": 1,
          "question": "Question text here",
          "marks": 1,
          "answer": "Complete marking-scheme answer / key points"
        }}
      ]
    }}
  ]
}}
"""
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        result = extract_json(response.text)
        if not isinstance(result.get("sections"), list) or not result["sections"]:
            raise ValueError("Invalid question paper format returned by AI.")
        return jsonify(result)
    except Exception as exc:
        return handle_error(exc, "AI Question Paper Generator")


# ==================================================
# 8. AI REVISION ASSISTANT (NEW)
# ==================================================
@app.route("/api/revision-assistant", methods=["POST"])
def revision_assistant():
    try:
        require_client()
        data = request.get_json(silent=True) or {}
        class_name = str(data.get("class", "")).strip()
        subject = str(data.get("subject", "")).strip()
        chapter = str(data.get("chapter", "")).strip()
        custom_notes = str(data.get("notes", "")).strip()

        if not class_name or not subject or not chapter:
            return jsonify({"error": "Please enter class, subject, and chapter name."}), 400

        extra_context = f"\nSTUDENT'S REFERENCE NOTES:\n{custom_notes[:4000]}" if custom_notes else ""

        prompt = f"""You are SmartStudyMate AI, an expert exam revision coach.
Generate a complete, high-impact revision kit for the chapter below.

CLASS: {class_name}
SUBJECT: {subject}
CHAPTER / TOPIC: {chapter}
{extra_context}

Rules:
1. Automatic Chapter Summary: Clear, structured overview of core concepts in student-friendly language.
2. Important Questions: Include 6-8 high-probability exam questions (1-mark, 2-mark, 3-mark, and 5-mark) with concise model answers.
3. Last-Minute Revision Notes: Crisp bullet points, key formulas/syntax, definitions, and common exam mistakes to avoid.
4. Do NOT use LaTeX $ signs; write math/code cleanly in plain text.

Return ONLY valid JSON:
{{
  "chapter_title": "{chapter}",
  "summary": "Detailed yet clear chapter summary text...",
  "important_questions": [
    {{
      "question": "Important exam question",
      "marks": "3 Marks",
      "answer": "Concise key points for full marks"
    }}
  ],
  "last_minute_notes": [
    "Key point / formula / rule 1",
    "Key point / formula / rule 2"
  ],
  "common_mistakes": [
    "Common mistake students make in exams and how to avoid it"
  ]
}}
"""
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        result = extract_json(response.text)
        if not result.get("summary"):
            raise ValueError("Invalid revision guide returned by AI.")
        return jsonify(result)
    except Exception as exc:
        return handle_error(exc, "AI Revision Assistant")


# ==================================================
# 9. STUDENT PERFORMANCE ANALYTICS (NEW)
# ==================================================
@app.route("/api/performance-analytics", methods=["POST"])
def performance_analytics():
    try:
        require_client()
        data = request.get_json(silent=True) or {}
        tasks = data.get("tasks", load_tasks())
        quiz_history = data.get("quiz_history", [])
        notes_count = int(data.get("notes_count", 0))

        prompt = f"""You are SmartStudyMate AI, an academic performance data analyst.
Evaluate the student's study tasks and quiz performance history below.

STUDY TASKS: {json.dumps(tasks[:100], ensure_ascii=False)}
QUIZ PERFORMANCE HISTORY: {json.dumps(quiz_history[:50], ensure_ascii=False)}
SAVED REVISION NOTES COUNT: {notes_count}

Analyze:
1. Overall performance trajectory (weekly and monthly study consistency).
2. Subject-wise strengths and weak areas.
3. Specific chapters or topics that need urgent revision based on pending tasks or lower quiz accuracy.
4. Actionable 7-day improvement strategy.

Return ONLY valid JSON:
{{
  "executive_summary": "Clear, encouraging summary of overall performance",
  "weekly_report": "Summary of recent 7-day study and quiz momentum",
  "monthly_outlook": "Longer-term exam readiness and consistency report",
  "topics_needing_revision": [
    {{
      "subject": "Subject name",
      "topic": "Chapter/topic name",
      "reason": "Why it needs attention (e.g., pending task or low quiz score)"
    }}
  ],
  "action_plan": [
    "Specific actionable step 1",
    "Specific actionable step 2"
  ]
}}
"""
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        result = extract_json(response.text)
        return jsonify(result)
    except Exception as exc:
        return handle_error(exc, "Student Performance Analytics")


# ==================================================
# 10. SMART TASK MANAGEMENT
# ==================================================
def load_tasks():
    if not os.path.exists(TASK_FILE):
        return []
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as file:
            tasks = json.load(file)
        return tasks if isinstance(tasks, list) else []
    except (OSError, json.JSONDecodeError):
        app.logger.exception("Could not read study_tasks.json")
        return []


def save_tasks(tasks):
    temporary_file = TASK_FILE + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(tasks, file, indent=4, ensure_ascii=False)
    os.replace(temporary_file, TASK_FILE)


@app.route("/api/tasks", methods=["GET", "POST"])
def manage_tasks():
    if request.method == "GET":
        return jsonify({"tasks": load_tasks()})

    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title:
        return jsonify({"error": "Task title is required."}), 400
    if len(title) > 200:
        return jsonify({"error": "Task title must be 200 characters or fewer."}), 400

    due_date = str(data.get("due_date", "")).strip()
    if due_date:
        try:
            date.fromisoformat(due_date)
        except ValueError:
            return jsonify({"error": "Invalid due date. Use YYYY-MM-DD."}), 400

    priority = str(data.get("priority", "Medium")).title()
    if priority not in ("Low", "Medium", "High"):
        return jsonify({"error": "Priority must be Low, Medium or High."}), 400

    task = {
        "id": str(uuid.uuid4()),
        "title": title,
        "subject": str(data.get("subject", "")).strip(),
        "chapter": str(data.get("chapter", "")).strip(),
        "due_date": due_date,
        "priority": priority,
        "status": "Pending",
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    return jsonify({"message": "Task added successfully.", "task": task}), 201


@app.route("/api/tasks/<task_id>", methods=["PUT", "DELETE"])
def update_task(task_id):
    tasks = load_tasks()
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        return jsonify({"error": "Task not found."}), 404

    if request.method == "DELETE":
        tasks.remove(task)
        save_tasks(tasks)
        return jsonify({"message": "Task deleted successfully."})

    data = request.get_json(silent=True) or {}
    for field in ("title", "subject", "chapter", "due_date", "priority", "status"):
        if field not in data:
            continue
        value = str(data[field]).strip()
        if field == "title" and (not value or len(value) > 200):
            return jsonify({"error": "Invalid task title."}), 400
        if field == "due_date" and value:
            try:
                date.fromisoformat(value)
            except ValueError:
                return jsonify({"error": "Invalid due date. Use YYYY-MM-DD."}), 400
        if field == "priority":
            value = value.title()
            if value not in ("Low", "Medium", "High"):
                return jsonify({"error": "Priority must be Low, Medium or High."}), 400
        if field == "status":
            value = value.title()
            if value not in ("Pending", "In Progress", "Completed"):
                return jsonify({"error": "Status must be Pending, In Progress or Completed."}), 400
        task[field] = value
    save_tasks(tasks)
    return jsonify({"message": "Task updated successfully.", "task": task})


# ==================================================
# 11. AI STUDY SCHEDULE GENERATOR
# ==================================================
@app.route("/api/study-schedule", methods=["POST"])
def study_schedule():
    try:
        data = request.get_json(silent=True) or {}
        class_name = str(data.get("class", "")).strip()
        subjects = data.get("subjects", [])
        chapters = data.get("chapters", [])
        exam_date = str(data.get("exam_date", "")).strip()
        try:
            daily_hours = float(data.get("daily_hours", 3))
        except (ValueError, TypeError):
            return jsonify({"error": "Daily study hours must be a number."}), 400

        if not class_name or not isinstance(subjects, list) or not subjects:
            return jsonify({"error": "Please provide class and at least one subject."}), 400
        if not all(isinstance(item, str) for item in subjects):
            return jsonify({"error": "Subjects must be a list of text."}), 400
        if not isinstance(chapters, list) or not all(isinstance(item, str) for item in chapters):
            return jsonify({"error": "Chapters must be a list of text."}), 400
        if not 0.5 <= daily_hours <= 12:
            return jsonify({"error": "Daily study hours must be between 0.5 and 12."}), 400

        prompt = f"""You are SmartStudyMate AI, an expert academic planner.
Create a realistic personalized weekly study timetable.
CLASS: {class_name}
SUBJECTS: {json.dumps(subjects)}
CHAPTERS: {json.dumps(chapters)}
EXAM DATE: {exam_date or 'Not specified'}
AVAILABLE DAILY STUDY HOURS: {daily_hours}

Distribute study time fairly. Give extra attention to difficult chapters.
Include short breaks, revision sessions and a weekly revision session.
Do not exceed the available daily study hours.
Return ONLY JSON:
{{"schedule":[{{"day":"Monday","sessions":[{{"time":"4:00 PM - 5:00 PM","subject":"Mathematics","chapter":"Algebra","activity":"Concept learning","duration_minutes":60}}]}}],"recommendations":["Revise important formulas regularly."]}}
"""
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        result = extract_json(response.text)
        if not isinstance(result.get("schedule"), list):
            raise ValueError("Invalid schedule returned by AI.")
        return jsonify(result)
    except Exception as exc:
        return handle_error(exc, "Study Schedule Generator")


# ==================================================
# 12. AI PROGRESS ANALYSIS
# ==================================================
@app.route("/api/progress-analysis", methods=["POST"])
def progress_analysis():
    try:
        data = request.get_json(silent=True) or {}
        tasks = data.get("tasks", load_tasks())
        if not isinstance(tasks, list) or len(tasks) > 500 or not all(isinstance(item, dict) for item in tasks):
            return jsonify({"error": "Invalid task data. Maximum 500 task records are allowed."}), 400

        total = len(tasks)
        completed = sum(1 for task in tasks if task.get("status") == "Completed")
        pending = sum(1 for task in tasks if task.get("status") == "Pending")
        in_progress = sum(1 for task in tasks if task.get("status") == "In Progress")
        percentage = round(completed / total * 100, 2) if total else 0

        prompt = f"""You are SmartStudyMate AI, an academic progress analyst.
Analyse the task data below and provide constructive, realistic advice.
TASKS: {json.dumps(tasks, ensure_ascii=False)}
TOTAL: {total}; COMPLETED: {completed}; PENDING: {pending}; IN PROGRESS: {in_progress}; COMPLETION: {percentage}%.
Return ONLY JSON:
{{"overall_analysis":"Student-friendly progress analysis","strengths":["Strength 1"],"areas_to_improve":["Area 1"],"recommendations":["Recommendation 1"],"next_steps":["Next step 1"]}}
Do not invent marks, grades or academic results.
"""
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        analysis = extract_json(response.text)
        return jsonify({
            "statistics": {"total": total, "completed": completed, "pending": pending,
                           "in_progress": in_progress, "completion_percentage": percentage},
            "analysis": analysis
        })
    except Exception as exc:
        return handle_error(exc, "Progress Analysis")


# ==================================================
# 13. EXAM PREPARATION MODE
# ==================================================
@app.route("/api/exam-preparation", methods=["POST"])
def exam_preparation():
    try:
        data = request.get_json(silent=True) or {}
        raw_class = str(data.get("class", "")).strip()
        exam_date = str(data.get("exam_date", "")).strip()
        subjects = data.get("subjects", [])
        chapters = data.get("chapters", [])

        match = re.fullmatch(r"(?:class\s*)?(1[0-2]|[1-9])", raw_class, flags=re.IGNORECASE)
        if not match:
            return jsonify({"error": "Please select a class between Class 1 and Class 12."}), 400

        class_number = int(match.group(1))
        class_name = f"Class {class_number}"

        if not isinstance(subjects, list) or not subjects or not all(isinstance(x, str) and x.strip() for x in subjects):
            return jsonify({"error": "Please provide at least one valid subject."}), 400
        if not isinstance(chapters, list) or not all(isinstance(x, str) for x in chapters):
            return jsonify({"error": "Chapters must be a list of text."}), 400

        subjects = [s.strip() for s in subjects]
        chapters = [c.strip() for c in chapters if c.strip()]

        try:
            target_date = date.fromisoformat(exam_date)
        except (ValueError, TypeError):
            return jsonify({"error": "Enter a valid examination date in YYYY-MM-DD format."}), 400

        days_remaining = (target_date - date.today()).days
        if days_remaining < 0:
            return jsonify({"error": "The examination date has already passed."}), 400

        if class_number <= 5:
            learning_level = "The student is in primary school. Use very simple language, short learning activities, fun revision activities, and frequent breaks."
        elif class_number <= 9:
            learning_level = "The student is in middle or secondary school. Include concept understanding, chapter-wise revision, textbook exercises, and practice questions."
        elif class_number == 10:
            learning_level = "The student is preparing for Class 10 board examinations. Include concept revision, important topics, written practice, and sample papers."
        elif class_number == 11:
            learning_level = "The student is in Class 11. Focus on subject-specific concepts, chapter understanding, numerical/practical exercises, and revision."
        else:
            learning_level = "The student is in Class 12 board examinations. Include detailed subject revision, important concepts, written practice, and mock papers."

        prompt = f"""You are SmartStudyMate AI, an expert school examination preparation assistant.
Create a realistic, personalized examination preparation plan.
STUDENT CLASS: {class_name}
EXAMINATION DATE: {exam_date}
DAYS REMAINING: {days_remaining}
SUBJECTS: {json.dumps(subjects, ensure_ascii=False)}
CHAPTERS: {json.dumps(chapters, ensure_ascii=False)}
STUDENT LEVEL INSTRUCTIONS: {learning_level}

Return ONLY valid JSON in this format:
{{
    "exam_countdown_days": {days_remaining},
    "preparation_plan": [
        {{
            "day": 1,
            "subject": "Mathematics",
            "tasks": ["Revise the selected chapter", "Complete suitable practice questions"]
        }}
    ],
    "important_recommendations": [
        "Revise regularly.",
        "Take short breaks between study sessions."
    ]
}}
"""
        config = types.GenerateContentConfig(response_mime_type="application/json")
        response = generate_with_retry(prompt, config=config)
        result = extract_json(response.text)
        if not isinstance(result.get("preparation_plan"), list):
            raise ValueError("Invalid preparation plan returned by AI.")
        if not isinstance(result.get("important_recommendations"), list):
            raise ValueError("Invalid recommendations returned by AI.")
        result["exam_countdown_days"] = days_remaining
        return jsonify(result)
    except Exception as exc:
        return handle_error(exc, "Exam Preparation")


# ==================================================
# 14. OFFICIAL CBSE SAMPLE PAPER LIBRARY
# ==================================================
class CBSEPaperParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current_row = None
        self.current_cell = None
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th") and self.current_row is not None:
            self.current_cell = {"text": "", "links": []}
        elif tag == "a" and self.current_cell is not None:
            self.current_link = {"text": "", "href": attrs.get("href", "")}

    def handle_data(self, data):
        if self.current_cell is not None:
            self.current_cell["text"] += data
        if self.current_link is not None:
            self.current_link["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current_link is not None:
            if self.current_cell is not None:
                self.current_cell["links"].append(self.current_link)
            self.current_link = None
        elif tag in ("td", "th") and self.current_cell is not None:
            self.current_row.append(self.current_cell)
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            if self.current_cell is not None:
                self.current_row.append(self.current_cell)
                self.current_cell = None
            self.rows.append(self.current_row)
            self.current_row = None


def get_official_cbse_url(class_name, session):
    class_codes = {"10": "CLASSX", "12": "CLASSXII"}
    if class_name not in class_codes:
        raise ValueError("Only Class 10 and Class 12 are supported.")
    if not re.fullmatch(r"\d{4}-\d{2}", session):
        raise ValueError("Invalid academic session. Use YYYY-YY, for example 2026-27.")
    start_year, end_year = map(int, session.split("-"))
    if end_year != (start_year + 1) % 100:
        raise ValueError("Invalid academic session format.")
    return f"https://cbseacademic.nic.in/SQP_{class_codes[class_name]}_{session}.html"


def fetch_cbse_papers(class_name, session):
    page_url = get_official_cbse_url(class_name, session)
    req = Request(page_url, headers={"User-Agent": "Mozilla/5.0 SmartStudyMateAI/1.0"})
    with urlopen(req, timeout=20) as response:
        final_url = response.geturl()
        if urlparse(final_url).hostname != "cbseacademic.nic.in":
            raise ValueError("The request did not remain on the official CBSE domain.")
        html = response.read().decode("utf-8", errors="replace")

    parser = CBSEPaperParser()
    parser.feed(html)
    papers = []

    for row in parser.rows:
        if not row:
            continue
        subject = re.sub(r"\s+", " ", row[0]["text"]).strip()
        if not subject or subject.lower() == "subject":
            continue
        paper_url = None
        marking_url = None
        for cell in row:
            for link in cell["links"]:
                label = re.sub(r"\s+", " ", link["text"]).strip().upper()
                href = urljoin(page_url, link["href"])
                parsed = urlparse(href)
                if parsed.scheme != "https" or parsed.hostname != "cbseacademic.nic.in":
                    continue
                if label in ("SQP", "SAMPLE PAPER", "SAMPLE QUESTION PAPER") or "SAMPLE QUESTION" in label:
                    paper_url = href
                elif label in ("MS", "MARKING SCHEME") or "MARKING SCHEME" in label:
                    marking_url = href
        if paper_url or marking_url:
            papers.append({"subject": subject, "sample_paper": paper_url,
                           "marking_scheme": marking_url, "source": "Official CBSE"})

    return {"class": class_name, "academic_session": session,
            "official_page": page_url, "source": "CBSE Academic Website", "papers": papers}


@app.route("/api/cbse-papers", methods=["GET"])
def cbse_papers():
    class_name = request.args.get("class", "10").strip()
    session = request.args.get("session", "2026-27").strip()
    subject_filter = request.args.get("subject", "").strip().lower()
    try:
        result = fetch_cbse_papers(class_name, session)
        if subject_filter:
            result["papers"] = [p for p in result["papers"] if subject_filter in p["subject"].lower()]
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("CBSE paper retrieval failed")
        official_page = get_official_cbse_url(class_name, session) if class_name in ("10", "12") and re.fullmatch(r"\d{4}-\d{2}", session) else "https://cbseacademic.nic.in/"
        return jsonify({
            "error": "Could not retrieve CBSE papers right now. Open the official CBSE page directly.",
            "official_page": official_page
        }), 503



# ==================================================
# 15. NEW STUDY TOOLS: FLASHCARDS, REFERENCE LIBRARY, ROOMS & CHECKLISTS
# ==================================================
FLASHCARD_FILE = os.path.join(BASE_DIR, "flashcards.json")
REFERENCE_FILE = os.path.join(BASE_DIR, "reference_library.json")
ROOM_FILE = os.path.join(BASE_DIR, "study_rooms.json")
CHECKLIST_FILE = os.path.join(BASE_DIR, "exam_checklists.json")

def read_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        return value
    except (OSError, json.JSONDecodeError):
        return default

def write_json_file(path, value):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
    os.replace(temp, path)

@app.route("/api/flashcards", methods=["GET", "POST"])
def flashcards_api():
    cards = read_json_file(FLASHCARD_FILE, [])
    if request.method == "GET":
        today = date.today().isoformat()
        return jsonify({"cards": cards, "due_cards": [c for c in cards if c.get("next_review", "") <= today]})
    data = request.get_json(silent=True) or {}
    front, back = str(data.get("front", "")).strip(), str(data.get("back", "")).strip()
    if not front or not back:
        return jsonify({"error": "Both question and answer are required."}), 400
    card = {"id": str(uuid.uuid4()), "front": front[:2000], "back": back[:4000], "interval": 0, "ease": 2.5, "repetitions": 0, "next_review": date.today().isoformat(), "created_at": datetime.now().isoformat(timespec="seconds")}
    cards.append(card); write_json_file(FLASHCARD_FILE, cards)
    return jsonify({"card": card, "message": "Flashcard added."}), 201

@app.route("/api/flashcards/<card_id>", methods=["PUT", "DELETE"])
def flashcard_detail(card_id):
    cards = read_json_file(FLASHCARD_FILE, [])
    card = next((c for c in cards if c.get("id") == card_id), None)
    if card is None: return jsonify({"error": "Flashcard not found."}), 404
    if request.method == "DELETE":
        cards.remove(card); write_json_file(FLASHCARD_FILE, cards); return jsonify({"message": "Deleted."})
    rating = str((request.get_json(silent=True) or {}).get("rating", "")).title()
    if rating not in ("Hard", "Good", "Easy"): return jsonify({"error": "Rating must be Hard, Good or Easy."}), 400
    intervals = {"Hard": 1, "Good": 3, "Easy": 7}
    if rating == "Hard": card["interval"] = 1; card["repetitions"] = 0
    else:
        card["repetitions"] = int(card.get("repetitions", 0)) + 1
        card["interval"] = intervals[rating] if card["repetitions"] == 1 else max(intervals[rating], round(float(card.get("interval", 1)) * (2.0 if rating == "Good" else 2.8)))
    card["ease"] = max(1.3, float(card.get("ease", 2.5)) + (0.15 if rating == "Easy" else -0.15 if rating == "Hard" else 0))
    card["next_review"] = (date.today() + timedelta(days=card["interval"])).isoformat()
    write_json_file(FLASHCARD_FILE, cards); return jsonify({"card": card, "message": "Review saved."})

@app.route("/api/reference-library", methods=["GET", "POST"])
def reference_library_api():
    entries = read_json_file(REFERENCE_FILE, [])
    if request.method == "GET":
        term = request.args.get("q", "").lower().strip()
        found = [e for e in entries if not term or term in (e.get("title", "") + " " + e.get("category", "") + " " + e.get("content", "")).lower()]
        return jsonify({"entries": found})
    data = request.get_json(silent=True) or {}
    title, category, content = (str(data.get(k, "")).strip() for k in ("title", "category", "content"))
    if not title or not content: return jsonify({"error": "Title and code/formula are required."}), 400
    entry = {"id": str(uuid.uuid4()), "title": title[:150], "category": category[:80] or "General", "content": content[:10000], "created_at": datetime.now().isoformat(timespec="seconds")}
    entries.append(entry); write_json_file(REFERENCE_FILE, entries); return jsonify({"entry": entry}), 201

@app.route("/api/reference-library/<entry_id>", methods=["DELETE"])
def reference_library_delete(entry_id):
    entries = read_json_file(REFERENCE_FILE, []); updated = [e for e in entries if e.get("id") != entry_id]
    if len(updated) == len(entries): return jsonify({"error": "Entry not found."}), 404
    write_json_file(REFERENCE_FILE, updated); return jsonify({"message": "Deleted."})

@app.route("/api/study-rooms", methods=["POST"])
def create_study_room():
    rooms = read_json_file(ROOM_FILE, {})
    code = uuid.uuid4().hex[:6].upper()
    rooms[code] = {"code": code, "quiz": None, "updated_at": datetime.now().isoformat(timespec="seconds")}
    write_json_file(ROOM_FILE, rooms); return jsonify({"code": code, "share_url": request.host_url.rstrip("/") + "/?room=" + code}), 201

@app.route("/api/study-rooms/<code>", methods=["GET", "POST"])
def study_room(code):
    code = code.upper(); rooms = read_json_file(ROOM_FILE, {})
    if code not in rooms: return jsonify({"error": "Study room not found."}), 404
    room = rooms[code]
    # Upgrade and normalize older room data without deleting saved questions.
    quiz = room.get("quiz")
    if quiz and isinstance(quiz, dict):
        if "questions" not in quiz:
            legacy_question = {
                "id": str(uuid.uuid4()),
                "question": quiz.get("question", ""),
                "options": quiz.get("options", []) if isinstance(quiz.get("options", []), list) else [],
                "responses": quiz.get("responses", {}) if isinstance(quiz.get("responses", {}), dict) else {},
            }
            quiz["questions"] = [legacy_question] if legacy_question["question"] else []
            quiz.pop("question", None); quiz.pop("options", None); quiz.pop("responses", None)
        if not isinstance(quiz.get("questions"), list):
            quiz["questions"] = []
        for saved_question in quiz["questions"]:
            if not isinstance(saved_question, dict):
                continue
            if not isinstance(saved_question.get("options"), list):
                saved_question["options"] = []
            if not isinstance(saved_question.get("responses"), dict):
                saved_question["responses"] = {}
            saved_question.setdefault("id", str(uuid.uuid4()))
            saved_question.setdefault("question", "")
        room["quiz"] = quiz
        rooms[code] = room
        write_json_file(ROOM_FILE, rooms)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        action = data.get("action", "add_question")
        quiz = room.get("quiz") or {"title": "Study Challenge", "questions": [], "ends_at": None, "started": False}
        quiz.setdefault("questions", [])
        if action == "add_question":
            question = str(data.get("question", "")).strip()
            options = data.get("options", [])
            if not question or not isinstance(options, list) or len(options) != 4 or any(not str(o).strip() for o in options):
                return jsonify({"error": "Enter a question and all four options before adding it."}), 400
            quiz["questions"].append({"id": str(uuid.uuid4()), "question": question[:1000], "options": [str(o).strip()[:500] for o in options], "responses": {}})
            quiz["title"] = str(data.get("title") or quiz.get("title") or "Study Challenge")[:120]
            quiz["started"] = False
        elif action == "start_quiz":
            if not quiz.get("questions"):
                return jsonify({"error": "Add at least one question before starting the challenge."}), 400
            minutes = max(1, min(180, int(data.get("duration", 5))))
            quiz["title"] = str(data.get("title") or quiz.get("title") or "Study Challenge")[:120]
            quiz["ends_at"] = (datetime.now() + timedelta(minutes=minutes)).isoformat()
            quiz["started"] = True
        elif action == "clear_questions":
            quiz["questions"] = []; quiz["started"] = False; quiz["ends_at"] = None
        else:
            return jsonify({"error": "Unknown study-room action."}), 400
        room["quiz"] = quiz
        room["updated_at"] = datetime.now().isoformat(timespec="seconds")
        write_json_file(ROOM_FILE, rooms)
    response_payload = dict(room)
    response_payload["server_now"] = datetime.now().isoformat()
    return jsonify(response_payload)

@app.route("/api/study-rooms/<code>/answers", methods=["POST"])
def submit_study_room_answer(code):
    code = code.upper(); rooms = read_json_file(ROOM_FILE, {})
    if code not in rooms: return jsonify({"error": "Study room not found."}), 404
    data = request.get_json(silent=True) or {}
    quiz = rooms[code].get("quiz") or {}
    if not quiz.get("started") or not quiz.get("ends_at") or (datetime.fromisoformat(quiz["ends_at"]) + timedelta(seconds=2)) < datetime.now():
        return jsonify({"error": "This challenge has ended or has not started."}), 400
    question = next((q for q in quiz.get("questions", []) if q.get("id") == data.get("question_id")), None)
    member = str(data.get("member", "")).strip()
    answer = data.get("answer")
    if question is None or not member or not isinstance(answer, int) or answer not in range(4):
        return jsonify({"error": "Invalid answer submission."}), 400
    question.setdefault("responses", {})[member] = answer
    write_json_file(ROOM_FILE, rooms)
    return jsonify({"message": "Answer submitted.", "response_count": len(question["responses"])})

@app.route("/api/exam-checklist", methods=["GET", "POST"])
def exam_checklist():
    data = request.get_json(silent=True) or {} if request.method == "POST" else request.args
    exam_date = str(data.get("exam_date", "")).strip()
    try: target = datetime.combine(date.fromisoformat(exam_date), datetime.min.time())
    except ValueError: return jsonify({"error": "Enter the exam date in YYYY-MM-DD format."}), 400
    hours_left = (target - datetime.now()).total_seconds() / 3600
    if hours_left < 0: return jsonify({"error": "The exam date has passed."}), 400
    checklist = read_json_file(CHECKLIST_FILE, {})
    key = exam_date
    if key not in checklist:
        checklist[key] = {"exam_date": key, "items": [{"label": "Hall ticket / admit card", "done": False}, {"label": "Required stationery (pens, pencils, eraser, ruler)", "done": False}, {"label": "School ID and permitted documents", "done": False}, {"label": "Digital backup of permitted study materials", "done": False}, {"label": "Confirm exam venue and travel plan", "done": False}, {"label": "Prepare a calm 2-hour revision warm-up", "done": False}, {"label": "Pack water and other permitted essentials", "done": False}], "generated_at": datetime.now().isoformat(timespec="seconds")}
        write_json_file(CHECKLIST_FILE, checklist)
    return jsonify({"checklist": checklist[key], "hours_until_exam": round(hours_left, 1), "is_48_hour_window": 0 <= hours_left <= 48})

@app.route("/api/exam-checklist", methods=["PUT"])
def update_exam_checklist():
    data = request.get_json(silent=True) or {}; exam_date = str(data.get("exam_date", "")); index = data.get("index"); done = bool(data.get("done"))
    checklist = read_json_file(CHECKLIST_FILE, {})
    if exam_date not in checklist or not isinstance(index, int) or not 0 <= index < len(checklist[exam_date]["items"]): return jsonify({"error": "Checklist item not found."}), 404
    checklist[exam_date]["items"][index]["done"] = done; write_json_file(CHECKLIST_FILE, checklist); return jsonify({"checklist": checklist[exam_date]})

# ==================================================
# 16. START APPLICATION
# ==================================================
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
