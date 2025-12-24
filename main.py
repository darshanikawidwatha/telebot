import os
import pickle
import asyncio
import shutil
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events, Button
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- RENDER PORT WORKAROUND ---
server = Flask('')

@server.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Render provides a PORT environment variable automatically
    port = int(os.environ.get("PORT", 8080))
    server.run(host='0.0.0.0', port=port)

# --- Configuration ---
API_ID = 5211221
API_HASH = 'a11b62696c1172bc9a392df48513e51a'
BOT_TOKEN = '8234543245:AAFkHc5xgxz6PwLCQ54YYT9-7zYLoIg2rmI'

CLIENT_SECRETS_FILE = 'client_secrets.json'
SCOPES = ['https://www.googleapis.com/auth/drive']
REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'

bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- Progress & Disk Helpers ---
def get_progress_bar(current, total):
    if not total or total == 0: return "[░░░░░░░░░░] 0%"
    percentage = current / total
    bar = "█" * int(percentage * 10) + "░" * (10 - int(percentage * 10))
    return f"[{bar}] {percentage:.1%}"

async def fast_download(client, message, path, status_msg):
    current_downloaded = 0
    total_size = message.file.size
    
    # Check disk space (Render free tier has ~500MB-1GB disk limit)
    total, used, free = shutil.disk_usage("/")
    if free < (total_size + 100 * 1024 * 1024):
        raise Exception(f"Insufficient Disk Space! File: {total_size/1024**2:.1f}MB, Free: {free/1024**2:.1f}MB")

    def progress_callback(received, total):
        nonlocal current_downloaded
        current_downloaded = received

    download_task = asyncio.create_task(
        client.download_media(message, file=path, progress_callback=progress_callback)
    )
    
    last_pc = -1
    while not download_task.done():
        pc = int((current_downloaded / total_size) * 100) if total_size else 0
        if pc != last_pc:
            try:
                await status_msg.edit(f"📥 **Downloading...**\n{get_progress_bar(current_downloaded, total_size)}")
                last_pc = pc
            except: pass
        await asyncio.sleep(5)
    
    return await download_task

# --- Bot Logic ---
DATA_FILE = 'users.pickle'
def save_creds(d):
    with open(DATA_FILE, 'wb') as f: pickle.dump(d, f)
def load_creds():
    return pickle.load(open(DATA_FILE, 'rb')) if os.path.exists(DATA_FILE) else {}

user_creds = load_creds()

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(prompt='consent')
    await event.respond(f"🚀 **Bot Active on Render**\n\n1. [Authorize Google Drive]({auth_url})\n2. Paste code here.", link_preview=False)

@bot.on(events.NewMessage())
async def main_handler(event):
    user_id = event.sender_id
    if event.raw_text and len(event.raw_text) > 20 and not event.file:
        try:
            flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
            flow.fetch_token(code=event.raw_text.strip())
            user_creds[user_id] = flow.credentials
            save_creds(user_creds)
            await event.respond("✅ Drive connected!")
        except Exception as e: await event.respond(f"❌ Error: {str(e)[:100]}")
        return

    if event.file:
        if user_id not in user_creds:
            return await event.respond("Connect Drive first via /start")
        status_msg = await event.respond("⚡ **Processing...**")
        
        temp_filename = f"dl_{user_id}.dat"
        full_path = os.path.join(os.getcwd(), temp_filename)
        
        try:
            await fast_download(bot, event, full_path, status_msg)
            await asyncio.sleep(2)

            service = build('drive', 'v3', credentials=user_creds[user_id])
            media = MediaFileUpload(full_path, resumable=True, chunksize=10*1024*1024)
            request = service.files().create(
                body={'name': event.file.name or "file"}, 
                media_body=media, 
                fields='id, webViewLink',
                supportsAllDrives=True 
            )
            
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    await status_msg.edit(f"📤 **Uploading...**\n{get_progress_bar(status.resumable_progress, status.total_size)}")
            
            await status_msg.delete()
            await event.respond(f"✅ **Success!**", buttons=[Button.inline("🔓 Make Public", data=f"pub_{response.get('id')}")])
        except Exception as e: await event.respond(f"❌ Error: {str(e)}")
        finally: 
            if os.path.exists(full_path): os.remove(full_path)

@bot.on(events.CallbackQuery(pattern=b'pub_'))
async def pub_callback(event):
    file_id = event.data.decode().split('_')[1]
    service = build('drive', 'v3', credentials=user_creds[event.sender_id])
    service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}, supportsAllDrives=True).execute()
    file = service.files().get(fileId=file_id, fields='webViewLink', supportsAllDrives=True).execute()
    await event.edit(f"✅ **Public Link:**\n{file.get('webViewLink')}")

if __name__ == '__main__':
    # Start the Flask web server in a separate thread
    t = Thread(target=run_flask)
    t.start()
    print("Web server started, now running Bot...")
    bot.run_until_disconnected()
