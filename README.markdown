# AI Calendar Assistant

A Python-based tool to manage your daily schedule, send notifications, and generate journals using an LLM via OpenRouter.

## Description

This application helps you track tasks from a timetable, receive desktop notifications, and create a natural journal entry based on your daily summary. It features a GUI, persistent task status, and customizable settings.

## Setup

1. Clone the repository:

   ```
   git clone https://github.com/StealthTensor/AI-Calendar-Assistant
   cd Calander
   ```
2. Create a virtual environment and install dependencies:

   ```
   python -m venv venv
   .\venv\Scripts\activate
   pip install requests plyer python-dotenv colorama pytz tkinter
   ```
3. Create a `.env` file with your OpenRouter API key:

   ```
   OPENROUTER_API_KEY=your_key_here
   ```
4. Ensure `config.json`, `timetable.json`, and other files are in the directory.

## Usage

- Run the script: `python main.py`
- Interact via the GUI:
  - View timetable and journal.
  - Click "Notify" for a manual notification.
  - Select and set a timezone.
- Press Ctrl+C to shut down and summarize your day.
- Notifications appear every 30 minutes (configurable).

## Dependencies

- `requests`, `plyer`, `python-dotenv`, `colorama`, `pytz`, `tkinter`

## Version

1.0.0

## License

\[MIT License\]
