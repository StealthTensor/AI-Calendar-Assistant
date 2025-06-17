import datetime
import os
import json
import requests
import time
import logging
import signal
import sys
import threading
import pytz
import platform
from dotenv import load_dotenv
from colorama import Fore, Style, init
from plyer import notification
import tkinter as tk
from tkinter import messagebox, scrolledtext
import winsound

# Initialize Colorama for Windows compatibility
init(autoreset=True)

# Set up logging
debug_level = os.getenv('DEBUG_LEVEL', 'info').lower()
logging.basicConfig(filename='app.log', level=logging.DEBUG if debug_level == 'debug' else logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
notification_logger = logging.getLogger('notifications')
notification_logger.setLevel(logging.INFO)
fh = logging.FileHandler('notifications.log')
fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
notification_logger.addHandler(fh)

# Log platform and plyer version
import plyer
logging.info(f"Platform: {platform.system()} {platform.release()}, Plyer version: {plyer.__version__}")

# Load environment variables
load_dotenv()

# --- Configuration Loading ---
def load_config():
    """Loads configuration from config.json."""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            config['version'] = config.get('version', '1.0.0')
            return config
    except FileNotFoundError:
        logging.error("config.json not found.")
        print(f"{Fore.RED}Error: config.json not found. Please create it.")
        sys.exit(1)
    except json.JSONDecodeError:
        logging.error("Invalid JSON in config.json.")
        print(f"{Fore.RED}Error: Invalid JSON in config.json.")
        sys.exit(1)

CONFIG = load_config()
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_API_URL = CONFIG.get('openrouter_api_url', 'https://openrouter.ai/api/v1/chat/completions')
LLM_MODEL = CONFIG.get('llm_model', 'meta-llama/llama-3-8b-instruct')
JOURNAL_FOLDER = CONFIG.get('journal_folder', 'C:\\EXPANSION\\Calander\\Journal')
TIMETABLE_FILE = CONFIG.get('timetable_file', 'timetable.json')
NOTIFICATION_INTERVAL_SECONDS = CONFIG.get('notification_interval_seconds', 1800)
MINUTES_BEFORE_TASK = CONFIG.get('minutes_before_task', 15)
GRACE_MINUTES_AFTER_START = CONFIG.get('grace_minutes_after_start', 2)
TIMEZONE = pytz.timezone(CONFIG.get('timezone', 'Asia/Kolkata'))
DEBUG_MODE = CONFIG.get('debug_mode', False)
NOTIFICATION_SOUND = CONFIG.get('notification_sound')
TIMEZONE_LIST = CONFIG.get('timezone_list', ['Asia/Kolkata', 'UTC', 'America/New_York'])

# Global variables
global timetable
timetable = []
current_day_journal_entries = []
last_journal_date = None
last_in_task_notification_time = datetime.datetime(1970, 1, 1, tzinfo=TIMEZONE)
sleep_notification_count = 0
max_sleep_notifications = 2
sleep_follow_up_interval = 15  # minutes
shutdown_flag = False
task_completion_status = {}

# --- Helper Functions ---
def get_llm_response(prompt, conversation_history=None, retries=3):
    """Sends a prompt to the LLM via OpenRouter with exponential backoff and fallback."""
    if not OPENROUTER_API_KEY:
        logging.error("OPENROUTER_API_KEY not set.")
        print(f"{Fore.RED}Error: OPENROUTER_API_KEY not set. LLM features disabled.")
        return "LLM unavailable: Set OPENROUTER_API_KEY in .env."

    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    messages = conversation_history or []
    messages.append({"role": "user", "content": prompt})

    data = {"model": LLM_MODEL, "messages": messages}
    
    for attempt in range(retries):
        try:
            response = requests.post(OPENROUTER_API_URL, headers=headers, data=json.dumps(data), timeout=10)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            logging.info(f"LLM response received: {content[:50]}...")
            return content
        except requests.exceptions.RequestException as e:
            logging.error(f"API error (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return f"API error: {str(e)[:50]}... (Check app.log for details)"
    return "Max retries reached for LLM API."

def save_completion_status(status):
    """Saves task completion status to a file."""
    with open('completion.json', 'w', encoding='utf-8') as f:
        json.dump(status, f)
    logging.info("Task completion status saved.")

def load_completion_status():
    """Loads task completion status from a file."""
    try:
        with open('completion.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.error("Invalid JSON in completion.json. Starting fresh.")
        return {}

def set_timezone(tz_name):
    """Updates the global TIMEZONE."""
    global TIMEZONE
    try:
        TIMEZONE = pytz.timezone(tz_name)
        logging.info(f"Timezone changed to {tz_name}")
        print(f"{Fore.GREEN}Timezone changed to {tz_name}")
        update_gui_timetable()  # Refresh timetable with new timezone
    except pytz.exceptions.UnknownTimeZoneError:
        logging.error(f"Unknown timezone: {tz_name}")
        print(f"{Fore.RED}Unknown timezone: {tz_name}")

def send_notification(title, message):
    """Sends a desktop notification with console fallback and sound."""
    notification_logger.info(f"{title} - {message}")
    max_message_length = 200
    original_message = message
    if len(message) > max_message_length:
        logging.warning(f"Notification message truncated: {original_message}")
        message = message[:max_message_length] + "..."

    try:
        notification.notify(title=title, message=message, app_name='AI Calendar Assistant', timeout=10)
        if NOTIFICATION_SOUND:
            winsound.PlaySound(NOTIFICATION_SOUND, winsound.SND_ASYNC)
        logging.info(f"Notification sent: {title} - {message}")
    except Exception as e:
        logging.error(f"Notification failed: {type(e).__name__}: {str(e)}")
        print(f"{Fore.RED}Notification failed: {str(e)}", flush=True)
        print(f"{Fore.CYAN}Fallback Notification: {title} - {message}", flush=True)
    
    if DEBUG_MODE:
        print(f"{Fore.YELLOW}DEBUG: Notification attempted: {title} - {original_message}", flush=True)

def parse_duration(duration_str):
    """Parses duration string (e.g., '1h30m') to minutes."""
    if not duration_str:
        return None
    total_minutes = 0
    num = ""
    for char in duration_str:
        if char.isdigit():
            num += char
        elif char in ('h', 'm'):
            if num:
                value = int(num)
                if char == 'h':
                    total_minutes += value * 60
                elif char == 'm':
                    total_minutes += value
                num = ""
    return total_minutes

def load_timetable(file_path):
    """Loads the daily timetable from a JSON file."""
    global timetable
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            timetable = json.load(f)
            for entry in timetable:
                entry['duration_minutes'] = parse_duration(entry.get('duration', ''))
                entry['notes'] = entry.get('notes', '')
            return timetable
    except FileNotFoundError:
        logging.error(f"Timetable file not found: {file_path}")
        print(f"{Fore.RED}Error: Timetable file not found at {file_path}.")
        return []
    except json.JSONDecodeError:
        logging.error(f"Invalid JSON in {file_path}")
        print(f"{Fore.RED}Error: Invalid JSON in {file_path}.")
        return []

def get_current_active_timetable_entry(current_time):
    """Gets the currently active task based on time and duration."""
    sorted_timetable = sorted(timetable, key=lambda x: x['time'])
    
    active_task_entry = None
    for i, entry in enumerate(sorted_timetable):
        task_time_obj = datetime.datetime.strptime(entry['time'], '%H:%M').time()
        task_dt = datetime.datetime.combine(current_time.date(), task_time_obj, tzinfo=TIMEZONE)
        
        duration_minutes = entry.get('duration_minutes')
        if duration_minutes:
            end_dt = task_dt + datetime.timedelta(minutes=duration_minutes)
        else:
            if i + 1 < len(sorted_timetable):
                next_task_time_obj = datetime.datetime.strptime(sorted_timetable[i+1]['time'], '%H:%M').time()
                end_dt = datetime.datetime.combine(current_time.date(), next_task_time_obj, tzinfo=TIMEZONE)
            else:
                end_dt = datetime.datetime.combine(current_time.date(), datetime.time(23, 59, 59), tzinfo=TIMEZONE)
        
        # Inclusive of end time to handle transitions correctly
        if task_dt <= current_time <= end_dt:
            active_task_entry = entry
            break
        # If at the exact start of the next task, consider the previous task active until the new one begins
        elif i > 0 and current_time == task_dt and current_time > datetime.datetime.combine(current_time.date(), sorted_timetable[i-1]['time'], tzinfo=TIMEZONE) + datetime.timedelta(minutes=sorted_timetable[i-1].get('duration_minutes', 0)):
            active_task_entry = sorted_timetable[i-1]
            break
    return active_task_entry

def write_journal_entry(date, entry_text):
    """Appends an entry to the daily journal file."""
    journal_filename = date.strftime('%d-%m-%Y') + ".txt"
    journal_filepath = os.path.join(JOURNAL_FOLDER, journal_filename)

    os.makedirs(JOURNAL_FOLDER, exist_ok=True)
    with open(journal_filepath, 'a', encoding='utf-8') as f:
        f.write(entry_text + "\n")
    logging.info(f"Journal updated: {journal_filename}")
    print(f"{Fore.GREEN}Journal updated for {date.strftime('%d-%m-%Y')}.")

def add_manual_journal_entry(entry):
    """Adds a manual journal entry."""
    if entry.strip():
        entry_text = f"Manual Entry ({datetime.datetime.now(TIMEZONE).strftime('%I:%M %p')}): {entry}\n"
        current_day_journal_entries.append(entry_text)
        logging.info(f"Manual journal entry added: {entry}")
        update_gui_journal()
    else:
        print(f"{Fore.YELLOW}Warning: Empty journal entry ignored.")

# --- GUI Functions ---
def update_gui_timetable():
    timetable_list.delete(0, tk.END)
    for entry in timetable:
        task_time = datetime.datetime.strptime(entry['time'], '%H:%M').replace(tzinfo=TIMEZONE).strftime('%H:%M %Z')
        timetable_list.insert(tk.END, f"{task_time} - {entry['task']} ({entry.get('duration', 'N/A')}) - {entry.get('notes', '')}")

def update_gui_journal():
    journal_text.delete(1.0, tk.END)
    journal_text.insert(tk.END, "".join(current_day_journal_entries))

def notify_button():
    get_smart_notification(timetable)
    messagebox.showinfo("Notification", "Manual notification triggered!")

def set_tz_button():
    tz = timezone_var.get()
    set_timezone(tz)
    messagebox.showinfo("Timezone", f"Set to {tz}")

# --- Core Logic Functions ---
def get_daily_summary(day_summary):
    """Generates a journal entry based on the user's day summary."""
    now_local = datetime.datetime.now(TIMEZONE)
    prompt = (
        f"Today is {now_local.strftime('%d-%m-%Y')}. Based on this user-provided summary: '{day_summary}', "
        f"write a natural, casual journal entry in past tense summarizing my day. Include personal reflections "
        f"or deviations (e.g., breaks, unexpected events) and notes from tasks if available. Keep it under 500 characters. Avoid motivational phrases."
    )
    llm_summary = get_llm_response(prompt)
    
    summary = llm_summary or f"Today, I recorded no detailed summary: {day_summary}."
    current_day_journal_entries.append(f"Daily Journal ({now_local.strftime('%I:%M %p')}):\n{summary}\n")
    update_gui_journal()
    return summary

def get_smart_notification():
    """Generates smart notifications."""
    global last_in_task_notification_time, sleep_notification_count
    now_local = datetime.datetime.now(TIMEZONE)
    
    if not timetable:
        logging.warning("No timetable loaded for notifications.")
        return

    current_active_task_entry = get_current_active_timetable_entry(now_local)
    current_active_task_name = current_active_task_entry['task'] if current_active_task_entry else "no specific task"
    
    upcoming_task_entry = None
    time_until_upcoming_task_minutes = 0
    is_upcoming_task_just_started = False
    sorted_timetable = sorted(timetable, key=lambda x: x['time'])
    
    for entry in sorted_timetable:
        task_time_obj = datetime.datetime.strptime(entry['time'], '%H:%M').time()
        task_dt_local = datetime.datetime.combine(now_local.date(), task_time_obj, tzinfo=TIMEZONE)
        
        if task_dt_local >= now_local - datetime.timedelta(minutes=GRACE_MINUTES_AFTER_START):
            upcoming_task_entry = entry
            time_until_upcoming_task_minutes = int((task_dt_local - now_local).total_seconds() / 60)
            
            if now_local >= task_dt_local and (now_local - task_dt_local).total_seconds() <= GRACE_MINUTES_AFTER_START * 60:
                is_upcoming_task_just_started = True
            break

    prompt = None
    notification_title = None
    notification_message = None

    if current_active_task_entry and current_active_task_name.lower() != "no specific task":
        if current_active_task_name.lower() == "sleep" and sleep_notification_count < max_sleep_notifications:
            time_since_task_start = (now_local - task_dt_local).total_seconds() / 60
            if time_since_task_start <= 0 or (
                time_since_task_start >= sleep_follow_up_interval and sleep_notification_count == 1
            ):
                notification_title = "Current Task: Sleep"
                notification_message = "It's sleep time! Time to unwind and rest for tomorrow!"
                send_notification(notification_title, notification_message)
                sleep_notification_count += 1
                last_in_task_notification_time = now_local
            return
        
        if (now_local - last_in_task_notification_time).total_seconds() >= NOTIFICATION_INTERVAL_SECONDS:
            notes = current_active_task_entry.get('notes', '')
            prompt = (
                f"Current task: '{current_active_task_name}' with notes: '{notes}'.\n"
                f"Provide a concise (under 100 chars), encouraging check-in for this task."
            )
            notification_title = f"Check-in: {current_active_task_name}"
            last_in_task_notification_time = now_local

    elif upcoming_task_entry and (
        datetime.timedelta(seconds=0) <= (task_dt_local - now_local) <= datetime.timedelta(minutes=MINUTES_BEFORE_TASK) or
        is_upcoming_task_just_started
    ):
        display_minutes = time_until_upcoming_task_minutes
        if is_upcoming_task_just_started:
            minutes_passed = int((now_local - task_dt_local).total_seconds() / 60)
            time_description = "just started" if minutes_passed == 0 else f"started {minutes_passed} minutes ago"
        elif display_minutes == 0:
            time_description = "is starting now"
        else:
            time_description = f"in {abs(display_minutes)} minutes"

        if upcoming_task_entry['task'].lower() == "sleep":
            notification_title = "Time for Bed!"
            if time_until_upcoming_task_minutes > 0:
                notification_message = f"Scheduled to sleep in {time_until_upcoming_task_minutes} minutes. Wind down!"
            else:
                notification_message = f"It's past {upcoming_task_entry['time']}. Time to rest!"
        else:
            notes = upcoming_task_entry.get('notes', '')
            prompt = (
                f"Current task: '{current_active_task_name}'.\n"
                f"Next task: '{upcoming_task_entry['task']}' at {upcoming_task_entry['time']} ({time_description}) with notes: '{notes}'.\n"
                f"Generate a concise (under 100 chars), encouraging notification to transition to the next task."
            )
            notification_title = f"Upcoming: {upcoming_task_entry['task']}"

    if notification_message:
        send_notification(notification_title, notification_message)
    elif prompt and notification_title:
        llm_notification = get_llm_response(prompt)
        logging.info(f"Full LLM notification response: {llm_notification}")
        if llm_notification:
            send_notification(notification_title, llm_notification)
        else:
            if "Upcoming:" in notification_title:
                send_notification(notification_title, f"Prepare for: {upcoming_task_entry['task']} at {upcoming_task_entry['time']}.")
            elif "Check-in:" in notification_title:
                send_notification(notification_title, f"Doing: {current_active_task_name}. Keep it up!")

def handle_user_input():
    """Handles manual journal entries, notification triggers, and timezone changes via console input."""
    while not shutdown_flag:
        try:
            user_input = input()
            if user_input.lower().startswith("journal "):
                entry = user_input[8:].strip()
                add_manual_journal_entry(entry)
            elif user_input.lower() == "notify":
                print(f"{Fore.CYAN}Triggering manual notification...")
                get_smart_notification()
            elif user_input.lower().startswith("set_tz "):
                tz = user_input[7:].strip()
                if tz in TIMEZONE_LIST:
                    set_timezone(tz)
                else:
                    print(f"{Fore.YELLOW}Invalid timezone. Use one of: {', '.join(TIMEZONE_LIST)}")
        except KeyboardInterrupt:
            continue

def main():
    global last_journal_date, current_day_journal_entries, sleep_notification_count, shutdown_flag, task_completion_status, timetable
    
    # Load timetable globally
    timetable = load_timetable(TIMETABLE_FILE)
    if not timetable:
        logging.error("Timetable is empty or could not be loaded.")
        print(f"{Fore.RED}FATAL: Timetable is empty or could not be loaded. Exiting.")
        sys.exit(1)

    # Load persistent task completion status
    task_completion_status = load_completion_status()

    # Test notification at startup
    send_notification("AI Calendar Assistant", "App started! Testing notifications.")

    # GUI Setup
    root = tk.Tk()
    root.title(f"AI Calendar Assistant v{CONFIG['version']}")
    root.geometry("400x500")

    tk.Label(root, text="Timetable").pack()
    global timetable_list
    timetable_list = tk.Listbox(root, height=10)
    timetable_list.pack(padx=10, pady=5)
    update_gui_timetable()

    tk.Label(root, text="Journal").pack()
    global journal_text
    journal_text = scrolledtext.ScrolledText(root, height=10, width=50)
    journal_text.pack(padx=10, pady=5)
    update_gui_journal()

    tk.Button(root, text="Notify", command=notify_button).pack(pady=5)
    timezone_var = tk.StringVar(value=TIMEZONE.zone)
    tk.OptionMenu(root, timezone_var, *TIMEZONE_LIST).pack(pady=5)
    tk.Button(root, text="Set Timezone", command=set_tz_button).pack(pady=5)

    tk.Button(root, text="Exit", command=lambda: [shutdown_flag.set(), root.quit()]).pack(pady=5)

    # Handle Ctrl+C
    def signal_handler(sig, frame):
        global shutdown_flag
        shutdown_flag = True
        root.destroy()  # Close GUI
        print(f"{Fore.BLUE}Shutting down. Please summarize your day (or press Enter to skip):")
        day_summary = input().strip() or "No summary provided."
        get_daily_summary(day_summary)
        save_completion_status(task_completion_status)
        if current_day_journal_entries and last_journal_date:
            write_journal_entry(last_journal_date, "".join(current_day_journal_entries))
        print(f"{Fore.GREEN}Journal updated. Goodbye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start input thread for manual journal entries and notifications
    input_thread = threading.Thread(target=handle_user_input, args=(), daemon=True)
    input_thread.start()

    current_date = datetime.datetime.now(TIMEZONE).date()
    if last_journal_date is None or last_journal_date < current_date:
        current_day_journal_entries = [f"--- Journal for {current_date.strftime('%d-%m-%Y')} ---\n"]
        last_journal_date = current_date
    else:
        journal_filename = current_date.strftime('%d-%m-%Y') + ".txt"
        journal_filepath = os.path.join(JOURNAL_FOLDER, journal_filename)
        if os.path.exists(journal_filepath):
            with open(journal_filepath, 'r', encoding='utf-8') as f:
                current_day_journal_entries = f.readlines()
            print(f"{Fore.CYAN}Loaded existing journal entries for today.")
        else:
            current_day_journal_entries = [f"--- Journal for {current_date.strftime('%d-%m-%Y')} ---\n"]

    # Start GUI loop with notification checks
    def check_notifications():
        if not shutdown_flag:
            get_smart_notification()
            root.after(NOTIFICATION_INTERVAL_SECONDS * 1000, check_notifications)  # Convert to milliseconds

    check_notifications()
    root.mainloop()

if __name__ == '__main__':
    main()