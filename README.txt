# SmartStudyMate AI

## Files
- `index.html`: frontend
- `app.py`: Flask backend and Gemini API connection
- `requirements.txt`: Python packages
- `.env.example`: template for your private API key

## Setup on Windows / VS Code
1. Open this folder in VS Code.
2. In the terminal, make sure the terminal is inside this folder.
3. Create a virtual environment (recommended):
   `py -m venv .venv`
4. Activate it in PowerShell:
   `.venv\Scripts\Activate.ps1`
   If PowerShell blocks activation, use Command Prompt and run:
   `.venv\Scripts\activate.bat`
5. Install packages:
   `python -m pip install -r requirements.txt`
6. Copy `.env.example` to a new file named `.env`.
7. Put your Gemini API key after `GEMINI_API_KEY=` in `.env`. Do not share it.
8. Start the server:
   `python app.py`
9. Open `http://127.0.0.1:5000` in Chrome.

Keep the terminal running while you use the app. Press Ctrl+C in the terminal to stop it.

## Notes
- AI MCQ generation uses Gemini with Google Search grounding. Availability depends on your API key, model access, and current Google API support.
- The AI Tutor uses Gemini without web search.
- Notes, tasks and profile are saved in this browser's localStorage.
- The PDF library in this starter version stores selected PDFs in page memory only; reloading the page clears the library.
- This is a local learning project. Do not deploy Flask debug mode publicly.
