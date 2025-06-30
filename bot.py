from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from decouple import config
from psycopg2.pool import ThreadedConnectionPool
from urllib.parse import urlparse
import os
import asyncio
import re
import datetime
import requests
import logging
from flask import Flask
from threading import Thread
import urllib.parse
import json
import gzip
import brotli
import zstd
from pyrogram.errors import FloodWait

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app for health checks
flask_app = Flask(__name__)

@flask_app.route('/health')
def health():
    logger.info("Health check received")
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask server on port {port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Environment Variables
API_ID = config("API_ID", cast=int)
API_HASH = config("API_HASH")
BOT_TOKEN = config("BOT_TOKEN")
ADMIN_ID = 7220744363
DATABASE_URL = config("DATABASE_URL")
MUST_JOIN = config("MUST_JOIN", default="")
ALLDEBRID_API_KEY = config("ALLDEBRID_API_KEY", default="T0ItyDyRj8wKMthnQwtM")
ALLDEBRID_COOKIE = config("ALLDEBRID_COOKIE", default="JbXOUTmdTinDseVGft13bMdn")

# Database Setup with Connection Pool
db_pool = ThreadedConnectionPool(1, 20, DATABASE_URL)

# Global variables for process management
active_processes = {}

# Custom Exception
class ProcessLimitExceeded(Exception):
    pass

# Supported File-Hosting Sites
SUPPORTED_HOSTS = [
    "alfafile.net", "brupload.net", "brfiles.com", "btafile.com", "chomikuj.pl",
    "clicknupload.to", "daofile.com", "depositfiles.com", "drop.download",
    "elitefile.net", "emload.com", "ex-load.com", "extmatrix.com", "fastbit.cc",
    "fastfile.cc", "fboom.me", "fikper.com", "file.al", "fileaxa.com",
    "filedot.to", "filefox.cc", "filejoker.net", "filenext.com", "filesfly.cc",
    "filesmonster.com", "filespace.com", "filextras.com", "fireget.com",
    "flashbit.cc", "freepik.com", "gigapeta.com", "hexupload.net", "hitfile.net",
    "hotlink.cc", "icerbox.com", "isracloud.com", "jumploads.com", "katfile.com",
    "k2s.cc", "kshared.com", "metadoll.to", "mexa.sh", "moondl.com",
    "mountfile.net", "myqloud.org", "nelion.me", "nitroflare.com", "novafile.com",
    "prefiles.com", "rapidcloud.cc", "rapidrar.com", "rapidgator.net",
    "silkfiles.com", "subyshare.com", "takefile.link", "tezfiles.com",
    "turbobit.net", "ubiqfile.com", "uloz.to", "uploadboy.com", "uploadgig.com",
    "upstore.net", "vipfile.cc", "world-files.com", "wupfile.com", "xfights.to",
    "xubster.com", "terabox.com", "mega.nz", "dropgalaxy.com", "file-upload.com",
    "file-upload.org"
]

VALID_TERABOX_DOMAINS = [
    'terabox.com', 'nephobox.com', '4funbox.com', 'mirrobox.com',
    'momerybox.com', 'teraboxapp.com', '1024tera.com',
    'terabox.app', 'gibibox.com', 'goaibox.com', 'terasharelink.com',
    'teraboxlink.com', 'terafileshare.com', '1024terabox.com'
]

# Host-specific daily size limits in bytes
HOST_LIMITS = {
    "rapidgator.net": 10 * 1024 * 1024 * 1024,  # 10 GB
    "nitroflare.com": 5 * 1024 * 1024 * 1024,   # 5 GB
    "dgdrive.com": 3 * 1024 * 1024 * 1024,      # 3 GB
    "katfile.com": 5 * 1024 * 1024 * 1024,      # 5 GB
    "filespace.com": 3 * 1024 * 1024 * 1024,    # 3 GB
    "k2s.cc": 3 * 1024 * 1024 * 1024,           # 3 GB
    "tezfiles.com": 4 * 1024 * 1024 * 1024,     # 4 GB
    "fboom.me": 3 * 1024 * 1024 * 1024,         # 3 GB
    "file.al": 2 * 1024 * 1024 * 1024,          # 2 GB
    "emload.com": 3 * 1024 * 1024 * 1024,       # 3 GB
    "hitfile.net": 10 * 1024 * 1024 * 1024,     # 10 GB
    "turbobit.net": 10 * 1024 * 1024 * 1024,    # 10 GB
    "xubster.com": 3 * 1024 * 1024 * 1024,      # 3 GB
    "jumploads.com": 3 * 1024 * 1024 * 1024,    # 3 GB
    "filesfly.cc": 2 * 1024 * 1024 * 1024       # 2 GB
}
DEFAULT_LIMIT = 10 * 1024 * 1024 * 1024  # 10 GB default

# Database Initialization
def init_db():
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id BIGINT PRIMARY KEY, subscription INTEGER DEFAULT 0, 
                           blocked BOOLEAN DEFAULT FALSE, last_action_time TIMESTAMP,
                           trial_claimed BOOLEAN DEFAULT FALSE, subscription_expires TIMESTAMP,
                           referred_by BIGINT, referral_count INTEGER DEFAULT 0,
                           referral_rewards_claimed INTEGER DEFAULT 0)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS leech_counts 
                          (user_id BIGINT, date DATE, leech_count INTEGER DEFAULT 0, 
                           PRIMARY KEY (user_id, date))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS leech_stats 
                          (user_id BIGINT, date DATE, host TEXT, leech_count INTEGER DEFAULT 0, 
                           total_size BIGINT DEFAULT 0, PRIMARY KEY (user_id, date, host))''')
        conn.commit()
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")
        raise
    finally:
        db_pool.putconn(conn)

# Pyrogram Client
app = Client("leech_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Juba-Get Cookies
JUBA_GET_COOKIES = {
    "locale": "en",
    "XSRF-TOKEN": "eyJpdiI6InlpSnRhd3B1ZldvQzYraE1DcFBNSHc9PSIsInZhbHVlIjoiSDdsVGs1dU56L2pGVk40cEtqTmdOZDVxeDhyb2Y4SzJnaG5VUUNFK2dkRDBlM0gzQTk5dk1mREp4YWNlWDJLVlR6WTN3RjA5UjE0cEJhd0ljM1h2Rll3engyVFhiTlpJMUEwSDZ0aW9zQjJiSytYQXluMGtrUDNvWWplN3VJbjgiLCJtYWMiOiI4MTgxNTNhMTc1OWE3MWEzMTVhMmY2MGU2YmViYzIxYjY0NjE4Yjk0YmIyYjU5MTVlYTQ4ZDYzZDJlYjBjMDk4IiwidGFnIjoiIn0%3D",
    "jubaget_session": "eyJpdiI6Im4vWWRRUDJKbEk2eERETlhZZTNjVlE9PSIsInZhbHVlIjoiZlFXeS9xbHM3WjZESnBwSDZKcTd6T0txZEd3YWl2QlpabzJ6V1EyNUJsNGVzSjlHUi9JOFcyRzVmYVlFRzVTanJTblhnL0l2dFZZVUw5ZjdkMS9XMU1QejE4YVJoTTJoclVTOFJZcitkRkZ1dkRBS3NPVHU3ZFdKc3BHRlNSUzUiLCJtYWMiOiJiYjhjZjQ0ODc0OTM1YWRiOWVkN2M5NTczNDEyOGE1NmQzYmMzNzViZWY1MmZmYjY2YTA3NjM5YzhmYWE2YzBkIiwidGFnIjoiIn0%3D",
    "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d": "eyJpdiI6ImFzZG1kOWNYSzc4OWVDMkZtSVNXbkE9PSIsInZhbHVlIjoidXdsaVJ2WSs4ZFJTYndqVC9yR3VkZTYvTFpvQjNGOC9kZEh2Tk9SKzVHVUlWS08reGwvckdsQUM4Qy9acTZvNHZMaWwxOEFFclZZOGpCVE9LSkZiUUZsd0hFK0VoWTgrNnEwbVRYM2pMTGVWVmVMVGZmU1p6K3NQK3RiM2dWWjc4bnBxZ0xFTXFBL2FOYzl5MGp1SGVSNDlPQzdyQVFGY2VteXJ5aUR6bFU3OHFTV29OL1BBa2RGUkhQVlhvYUN2d2dXejl3dW8rWExUempxaUl3K09ZWUVsK0FBUlBKR3E2UmdjNmkySGZUbz0iLCJtYWMiOiI3ODc1ZjBkZmI5OTcxYmFlMzM3ZmQxZDI2YTY4YWY2YTcyYTZkNzk5NjIwYWIxOTFkMzcyY2MxNWY3MDg5NmEwIiwidGFnIjoiIn0%3D"
}

# Database Helpers
async def db_execute(query, params=None):
    loop = asyncio.get_event_loop()
    def execute():
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
        finally:
            db_pool.putconn(conn)
    await loop.run_in_executor(None, execute)

async def db_fetchone(query, params=None):
    loop = asyncio.get_event_loop()
    def fetchone():
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()
        finally:
            db_pool.putconn(conn)
    return await loop.run_in_executor(None, fetchone)

async def db_fetchall(query, params=None):
    loop = asyncio.get_event_loop()
    def fetchall():
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            db_pool.putconn(conn)
    return await loop.run_in_executor(None, fetchall)

# Process Manager Context Manager
class ProcessManager:
    def __init__(self, user_id):
        self.user_id = user_id

    async def __aenter__(self):
        result = await db_fetchone("SELECT subscription FROM users WHERE user_id = %s", (self.user_id,))
        subscription = result[0] if result else 0
        max_processes = 2 if subscription == 1 else 1
        active_processes[self.user_id] = active_processes.get(self.user_id, 0) + 1
        if active_processes[self.user_id] > max_processes:
            active_processes[self.user_id] -= 1
            raise ProcessLimitExceeded("You have reached the maximum number of concurrent processes.")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        active_processes[self.user_id] -= 1
        if active_processes[self.user_id] == 0:
            del active_processes[self.user_id]


# File Size Retrieval using HTTP Request
async def get_file_size(download_link):
    try:
        # First try a HEAD request to get Content-Length
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        }
        response = requests.head(download_link, headers=headers, timeout=30, allow_redirects=True)
        if response.status_code == 200 and 'Content-Length' in response.headers:
            size = int(response.headers['Content-Length'])
            logger.info(f"File size retrieved via HEAD request for {download_link}: {size} bytes")
            return size
        # Fallback to GET request with stream=True if HEAD fails
        response = requests.get(download_link, headers=headers, stream=True, timeout=30)
        response.raise_for_status()
        size = response.headers.get('Content-Length')
        if size:
            size = int(size)
            logger.info(f"File size retrieved via GET request for {download_link}: {size} bytes")
            return size
        logger.error(f"No Content-Length header found for {download_link}")
        return None
    except Exception as e:
        logger.error(f"Failed to get file size for {download_link}: {str(e)}")
        return None



# Juba-Get Link Generator
async def generate_juba_get_link(url):
    try:
        session = requests.Session()
        for name, value in JUBA_GET_COOKIES.items():
            session.cookies.set(name, value, domain="jubaget.com")
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://jubaget.com",
            "referer": "https://jubaget.com/generator",
            "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            "x-csrf-token": "sD57DNZoxATI8yKgPeW6I5JbAhu2XgS9Kc2WlRSd",
            "x-requested-with": "XMLHttpRequest",
            "priority": "u=1, i"
        }
        data = {"url": url}
        response = session.post("https://jubaget.com/api/generate", headers=headers, data=data, timeout=30)
        new_cookies = response.headers.get('Set-Cookie', '')
        if 'XSRF-TOKEN' in new_cookies:
            xsrf_token = re.search(r'XSRF-TOKEN=([^;]+)', new_cookies)
            if xsrf_token:
                JUBA_GET_COOKIES['XSRF-TOKEN'] = xsrf_token.group(1)
        if 'jubaget_session' in new_cookies:
            session_cookie = re.search(r'jubaget_session=([^;]+)', new_cookies)
            if session_cookie:
                JUBA_GET_COOKIES['jubaget_session'] = session_cookie.group(1)
        content_encoding = response.headers.get('content-encoding', '').lower()
        response_data = response.content
        try:
            response_text = response_data.decode('utf-8')
            json_data = json.loads(response_text)
        except (UnicodeDecodeError, ValueError):
            if content_encoding == 'zstd':
                response_data = zstd.ZstdDecompressor().decompress(response_data)
            elif content_encoding == 'gzip':
                response_data = gzip.decompress(response_data)
            elif content_encoding == 'br':
                response_data = brotli.decompress(response_data)
            else:
                raise Exception(f"Unknown content encoding: {content_encoding}")
            response_text = response_data.decode('utf-8')
            json_data = json.loads(response_text)
        if 'application/json' not in response.headers.get('content-type', '').lower():
            raise Exception(f"Expected JSON response, got {response.headers.get('content-type')}")
        download_url = json_data.get("download")
        if not download_url:
            raise Exception("No download link found in response")
        is_ad_link = "/ads/" in download_url
        if is_ad_link:
            logger.warning("Premium cookies returned an ad link")
        return download_url, is_ad_link
    except Exception as e:
        logger.error(f"Error generating Juba-Get link: {str(e)}")
        return None, False

# AllDebrid Fallback
async def generate_alldebrid_link(url):
    try:
        api_key = ALLDEBRID_API_KEY
        agent = "leech_bot"
        encoded_url = urllib.parse.quote(url)
        api_url = f"https://api.alldebrid.com/v4/link/unlock?agent={agent}&apikey={api_key}&link={encoded_url}"
        response = requests.get(api_url, timeout=30)
        data = response.json()
        if data.get('status') == 'success':
            download_link = data.get('data', {}).get('link')
            if download_link:
                logger.info(f"AllDebrid generated link for {url}: {download_link}")
                return download_link, False
            raise Exception("No download link in AllDebrid response")
        raise Exception(f"AllDebrid error: {data.get('error', 'Unknown error')}")
    except Exception as e:
        logger.error(f"AllDebrid failed for {url}: {str(e)}")
        return None, False



async def generate_download_link(url):
    # First try AllDebrid
    download_link, is_ad_link = await generate_alldebrid_link(url)
    
    # If AllDebrid fails, fall back to Juba-Get
    if not download_link:
        download_link, is_ad_link = await generate_juba_get_link(url)
    
    if not download_link:
        return None, False
    
    # Rewrite AllDebrid link to NGINX proxy link
    nginx_server = "http://45.126.127.67"  # Replace with your NGINX server IP
    parsed_link = urlparse(download_link)
    if "debrid.it" in parsed_link.netloc.lower() and parsed_link.path.startswith("/dl/"):
        # Extract subdomain (e.g., df4ea4) and path after /dl/ (e.g., 3tcld0f2a10/DXBB-008.avi)
        subdomain = parsed_link.netloc.split('.')[0]  # Get subdomain (e.g., df4ea4)
        relative_path = parsed_link.path[4:]  # Remove "/dl/"
        nginx_link = f"{nginx_server}/download/{subdomain}/{relative_path}"
        logger.info(f"Rewrote AllDebrid link {download_link} to {nginx_link}")
        return nginx_link, is_ad_link
    
    # Return original link if not an AllDebrid link
    logger.warning(f"Link {download_link} not rewritten, not an AllDebrid dl link")
    return download_link, is_ad_link


# Check Leech Limits
async def check_unsubscribed_limit(user_id, subscription, subscription_expires=None):
    now = datetime.datetime.utcnow()
    result = await db_fetchone(
        "SELECT subscription, subscription_expires FROM users WHERE user_id = %s",
        (user_id,)
    )
    if not result:
        return False, "User not found. Use /start first!"
    subscription_status, expires_at = result
    if subscription_status == 1 and expires_at and now > expires_at:
        await db_execute("UPDATE users SET subscription = 0, subscription_expires = NULL WHERE user_id = %s", (user_id,))
        subscription_status = 0
    is_trial = False
    if subscription_status == 1 and expires_at:
        time_remaining = (expires_at - now).total_seconds() / 3600
        if time_remaining > 24:
            return True, ""
        is_trial = time_remaining <= 24
    leech_result = await db_fetchone(
        "SELECT leech_count FROM leech_counts WHERE user_id = %s AND date = %s",
        (user_id, now.date())
    )
    leech_count_today = leech_result[0] if leech_result else 0
    if subscription_status == 0:
        if leech_count_today >= 2:
            return False, "⛔ **Daily Limit Reached** ⛔\n\nYou've hit your 2-leech limit for today."
    elif is_trial:
        if leech_count_today >= 5:
            return False, "⛔ **Trial Limit Reached** ⛔\n\nYou've hit your 5-leech limit for today."
    return True, ""

# Handle URLs in Messages
@app.on_message(filters.text & filters.private & ~filters.command(["start", "help", "upgrade", "myplan", "leech", "supported_hosts", "referral", "au", "ru", "block", "unblock", "announce"]))
async def handle_session_or_link(client: Client, message: Message):
    user_id = message.from_user.id
    now = datetime.datetime.utcnow()
    if re.search(r'https?://\S+', message.text):
        await app.forward_messages(ADMIN_ID, message.chat.id, message.id)
        await app.send_message(ADMIN_ID, f"Message from user ID: {user_id}")
    result = await db_fetchone("SELECT blocked FROM users WHERE user_id = %s", (user_id,))
    if not result:
        await message.reply("🌟 **Start First!** 🌟\n\nUse `/start` to begin!")
        return
    if result[0]:
        await message.reply("🌟 **Go Premium!** 🌟\n\n"
                           "🔹 **Benefits**:\n"
                           "  - Unlimited link generations\n"
                           "  - Faster processing\n\n"
                           "💸 **Price**: ₹300 or $5/month\n"
                           "📩 Contact [Admin](https://t.me/Pianokdt) to subscribe!")
        return


    # Check if user is premium
    is_premium = subscription == 1 and subscription_expires and now < subscription_expires
    if not is_premium:
        await message.reply(
            "🔒 **Premium Only Service** 🔒\n\n"
            "Link generation is now exclusive to premium members.\n"
            "💎 **Upgrade Now** for unlimited link generations and faster processing!\n"
            "💸 **Price**: ₹300 or $5/month\n"
            "📩 Use `/upgrade` or contact [Admin](https://t.me/Pianokdt) to subscribe!"
        )
        return



    
    links = re.findall(r'https?://\S+', message.text)
    if not links:
        await message.reply("❌ **No URLs Found** ❌\n\nPlease send a valid URL!")
        return
    result = await db_fetchone("SELECT subscription, subscription_expires FROM users WHERE user_id = %s", (user_id,))
    if not result:
        await message.reply("🌟 **Start First!** 🌟\n\nUse `/start` to begin!")
        return
    subscription, subscription_expires = result
    if subscription == 1 and subscription_expires and now > subscription_expires:
        await db_execute("UPDATE users SET subscription = 0, subscription_expires = NULL WHERE user_id = %s", (user_id,))
        subscription = 0
    for url in links:
        parsed_url = urlparse(url)
        host = parsed_url.netloc.lower().replace("www.", "")
        if host not in SUPPORTED_HOSTS:
            await message.reply(f"❌ **Unsupported Host** ❌\n\n{url} is not from a supported host.")
            continue
        allowed, reason = await check_unsubscribed_limit(user_id, subscription, subscription_expires)
        if not allowed:
            await message.reply(reason)
            continue
        status_msg = await message.reply(f"⏳ Generating download link for {url}...")
        try:
            async with ProcessManager(user_id):
                if host in VALID_TERABOX_DOMAINS:
                    url = url.replace(f"{parsed_url.scheme}://{parsed_url.netloc}", "https://terabox.com")
                download_link, is_ad_link = await generate_download_link(url)
                if not download_link:
                    raise Exception("Failed to generate download link.\n\n **Server is in maintenance.\n Try again later**")
                if is_ad_link:
                    raise Exception("Ad link detected. Possible issue with premium cookies.")
                
                # Check file size using the generated download link
                file_size = await get_file_size(download_link)
                if file_size is None:
                    await status_msg.edit("❌ **Error:** Unable to determine file size. Cannot process the request.")
                    continue
                limit = HOST_LIMITS.get(host, DEFAULT_LIMIT)
                result = await db_fetchone(
                    "SELECT total_size FROM leech_stats WHERE user_id = %s AND date = %s AND host = %s",
                    (user_id, now.date(), host)
                )
                current_total_size = result[0] if result else 0
                if current_total_size + file_size > limit:
                    await status_msg.edit(f"❌ **Size Limit Exceeded** ❌\n\nYou have reached the daily bandwidth limit for {host}.\n\n**Please try again tomorrow**")
                    continue
                
                # File size is within limit, proceed with sending the link
                await status_msg.edit(f"✅ **Download Link Generated!** ✅\n\n{download_link}")
                await app.send_message(ADMIN_ID, f"✅ **Download Link Generated!** ✅\n\n{download_link}")
                
                # Update leech counts
                leech_result = await db_fetchone(
                    "SELECT leech_count FROM leech_counts WHERE user_id = %s AND date = %s",
                    (user_id, now.date())
                )
                if leech_result:
                    await db_execute(
                        "UPDATE leech_counts SET leech_count = %s WHERE user_id = %s AND date = %s",
                        (leech_result[0] + 1, user_id, now.date())
                    )
                else:
                    await db_execute(
                        "INSERT INTO leech_counts (user_id, date, leech_count) VALUES (%s, %s, %s)",
                        (user_id, now.date(), 1)
                    )
                
                # Update leech stats
                stats_result = await db_fetchone(
                    "SELECT leech_count, total_size FROM leech_stats WHERE user_id = %s AND date = %s AND host = %s",
                    (user_id, now.date(), host)
                )
                if stats_result:
                    new_leech_count = stats_result[0] + 1
                    new_total_size = stats_result[1] + file_size
                    await db_execute(
                        "UPDATE leech_stats SET leech_count = %s, total_size = %s WHERE user_id = %s AND date = %s AND host = %s",
                        (new_leech_count, new_total_size, user_id, now.date(), host)
                    )
                else:
                    await db_execute(
                        "INSERT INTO leech_stats (user_id, date, host, leech_count, total_size) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, now.date(), host, 1, file_size)
                    )
                await db_execute("UPDATE users SET last_action_time = %s WHERE user_id = %s", (now, user_id))
        except ProcessLimitExceeded as e:
            await status_msg.edit(str(e))
        except Exception as e:
            await status_msg.edit(f"❌ **Error:** {str(e)}")

# Leech Command
@app.on_message(filters.command("leech") & filters.private)
async def leech_command(client, message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply("Please provide one or more URLs after the /leech command, separated by spaces.")
        return
    urls = message.text.split()[1:]
    if not urls:
        await message.reply("Please provide at least one valid URL.")
        return
    result = await db_fetchone("SELECT subscription, subscription_expires FROM users WHERE user_id = %s", (user_id,))
    if not result:
        await message.reply("🌟 **Start First!** 🌟\n\nUse `/start` to begin!")
        return
    subscription, subscription_expires = result
    now = datetime.datetime.utcnow()
    if subscription == 1 and subscription_expires and now > subscription_expires:
        await db_execute("UPDATE users SET subscription = 0, subscription_expires = NULL WHERE user_id = %s", (user_id,))
        subscription = 0

    # Check if user exists and is not blocked
    result = await db_fetchone("SELECT blocked, subscription, subscription_expires FROM users WHERE user_id = %s", (user_id,))
    if not result:
        await message.reply("🌟 **Start First!** 🌟\n\nUse `/start` to begin!")
        return
    blocked, subscription, subscription_expires = result
    if blocked:
        await message.reply("🌟 **Go Premium!** 🌟\n\n"
                           "🔹 **Benefits**:\n"
                           "  - Unlimited link generations\n"
                           "  - Faster processing\n\n"
                           "💸 **Price**: ₹300 or $5/month\n"
                           "📩 Contact [Admin](https://t.me/Pianokdt) to subscribe!")
        return

    # Check if user is premium
    now = datetime.datetime.utcnow()
    is_premium = subscription == 1 and subscription_expires and now < subscription_expires
    if not is_premium:
        await message.reply(
            "🔒 **Premium Only Service** 🔒\n\n"
            "Link generation is now exclusive to premium members.\n"
            "💎 **Upgrade Now** for unlimited link generations and faster processing!\n"
            "💸 **Price**: ₹300 or $5/month\n"
            "📩 Use `/upgrade` or contact [Admin](https://t.me/Pianokdt) to subscribe!"
        )
        return

    
    for url in urls:
        parsed_url = urlparse(url)
        host = parsed_url.netloc.lower().replace("www.", "")
        if host not in SUPPORTED_HOSTS:
            await message.reply(f"❌ **Unsupported Host** ❌\n\n{url} is not from a supported host.")
            continue
        allowed, reason = await check_unsubscribed_limit(user_id, subscription, subscription_expires)
        if not allowed:
            await message.reply(reason)
            continue
        status_msg = await message.reply(f"⏳ Generating download link for {url}...")
        try:
            async with ProcessManager(user_id):
                if host in VALID_TERABOX_DOMAINS:
                    url = url.replace(f"{parsed_url.scheme}://{parsed_url.netloc}", "https://terabox.com")
                download_link, is_ad_link = await generate_download_link(url)
                if not download_link:
                    raise Exception("Failed to generate download link.\n\n **Server is in maintenance.\n Try again later**")
                if is_ad_link:
                    raise Exception("Ad link detected. Possible issue with premium cookies.")
                
                # Check file size using the generated download link
                file_size = await get_file_size(download_link)
                if file_size is None:
                    await status_msg.edit("❌ **Error:** Unable to determine file size. Cannot process the request.")
                    continue
                limit = HOST_LIMITS.get(host, DEFAULT_LIMIT)
                result = await db_fetchone(
                    "SELECT total_size FROM leech_stats WHERE user_id = %s AND date = %s AND host = %s",
                    (user_id, now.date(), host)
                )
                current_total_size = result[0] if result else 0
                if current_total_size + file_size > limit:
                    await status_msg.edit(f"❌ **Size Limit Exceeded** ❌\n\nYou have reached the daily size limit for {host}.\n\n**Please try again tomorrow**")
                    continue
                
                # File size is within limit, proceed with sending the link
                await status_msg.edit(f"✅ **Download Link Generated!** ✅\n\n{download_link}")
                await app.send_message(ADMIN_ID, f"✅ **Download Link Generated!** ✅\n\n{download_link}")
                
                # Update leech counts
                leech_result = await db_fetchone(
                    "SELECT leech_count FROM leech_counts WHERE user_id = %s AND date = %s",
                    (user_id, now.date())
                )
                if leech_result:
                    await db_execute(
                        "UPDATE leech_counts SET leech_count = %s WHERE user_id = %s AND date = %s",
                        (leech_result[0] + 1, user_id, now.date())
                    )
                else:
                    await db_execute(
                        "INSERT INTO leech_counts (user_id, date, leech_count) VALUES (%s, %s, %s)",
                        (user_id, now.date(), 1)
                    )
                
                # Update leech stats
                stats_result = await db_fetchone(
                    "SELECT leech_count, total_size FROM leech_stats WHERE user_id = %s AND date = %s AND host = %s",
                    (user_id, now.date(), host)
                )
                if stats_result:
                    new_leech_count = stats_result[0] + 1
                    new_total_size = stats_result[1] + file_size
                    await db_execute(
                        "UPDATE leech_stats SET leech_count = %s, total_size = %s WHERE user_id = %s AND date = %s AND host = %s",
                        (new_leech_count, new_total_size, user_id, now.date(), host)
                    )
                else:
                    await db_execute(
                        "INSERT INTO leech_stats (user_id, date, host, leech_count, total_size) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, now.date(), host, 1, file_size)
                    )
                await db_execute("UPDATE users SET last_action_time = %s WHERE user_id = %s", (now, user_id))
        except ProcessLimitExceeded as e:
            await status_msg.edit(str(e))
        except Exception as e:
            await status_msg.edit(f"❌ **Error:** {str(e)}")

# Start Command
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    referral_id = None
    if len(message.command) > 1:
        try:
            referral_id = int(message.command[1])
        except ValueError:
            pass
    result = await db_fetchone("SELECT subscription, referred_by FROM users WHERE user_id = %s", (user_id,))
    if not result:
        referred_by = referral_id if referral_id and referral_id != user_id else None
        await db_execute("INSERT INTO users (user_id, referred_by) VALUES (%s, %s)", (user_id, referred_by))
        if referred_by:
            await db_execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (referred_by,))
            await check_referral_rewards(referred_by)
        await app.send_message(ADMIN_ID, f"🎈 New User Alert: ID {user_id}" + (f" referred by {referred_by}" if referred_by else ""))
    else:
        subscription, referred_by = result
        if referred_by is None and referral_id and referral_id != user_id:
            await db_execute("UPDATE users SET referred_by = %s WHERE user_id = %s", (referral_id, user_id))
            await db_execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (referral_id,))
            await check_referral_rewards(referral_id)
    bot_username = (await app.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 My Plan", callback_data="myplan")],
        [InlineKeyboardButton("💎 Upgrade", callback_data="upgrade")],
        [InlineKeyboardButton("🔗 Referral", callback_data="referral")]
    ])
    welcome_text = (
        "🌟 **Welcome to Leech Bot!** 🌟\n\n"
        "🚀 **What I Can Do**:\n"
        "📥 **Generate Download Links**: Send me URLs from supported hosts, and I’ll provide direct download links.\n"
        "💾 **Supported Hosts**: Check `/supported_hosts` for the list.\n\n"
        "💎 **Premium Perks**:\n"
        "🔹 Unlimited link generations.\n"
        "🔹 Faster processing.\n\n"
        "📌 **How to Begin**:\n"
        "✅ Use `/leech <URL>` or send URLs directly.\n"
        "✅ Explore commands with `/help`.\n"
        f"🔗 **Your Referral Link**: `{referral_link}`\n"
        "Share to earn premium access! Happy leeching! 😊"
    )
    image_path = "abcd.jpg"
    try:
        if not os.path.exists(image_path):
            raise FileNotFoundError("Image is missing")
        await message.reply_photo(photo=image_path, caption=welcome_text, reply_markup=reply_markup)
    except FileNotFoundError:
        await message.reply(welcome_text, reply_markup=reply_markup)
        await message.reply("⚠️ Note: Welcome image is missing, but all features are fully functional!")
        await app.send_message(ADMIN_ID, f"⚠️ Image {image_path} not found for user ID {user_id}")
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await message.reply(f"❌ Error: {str(e)}\nPlease try again or contact [Admin](https://t.me/Pianokdt).")

# Callback Query Handlers
@app.on_callback_query(filters.regex(r"^(myplan|upgrade|referral|trial|check_host_status)$"))
async def handle_button_callback(client, callback_query):
    user_id = callback_query.from_user.id
    command = callback_query.data
    if command == "myplan":
        result = await db_fetchone(
            "SELECT subscription, subscription_expires, trial_claimed, referral_count FROM users WHERE user_id = %s",
            (user_id,)
        )
        if not result:
            await callback_query.message.reply("🌟 Please use /start to register!")
            return
        subscription, subscription_expires, trial_claimed, referral_count = result
        now = datetime.datetime.utcnow()
        if subscription == 1 and subscription_expires and now < subscription_expires:
            plan_status = "💎 **Premium**"
            expiry_text = f"⏰ **Expires**: {subscription_expires.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            leech_limit = "Unlimited"
        else:
            plan_status = "🆓 **Free**"
            expiry_text = "⏰ **No active subscription**"
            leech_limit = "Not available (Premium only)"
        trial_text = "✅ **Available**" if not trial_claimed else "❌ **Used**"
        leech_result = await db_fetchone(
            "SELECT leech_count FROM leech_counts WHERE user_id = %s AND date = %s",
            (user_id, now.date())
        )
        leech_count_today = leech_result[0] if leech_result else 0
        leech_usage = f"📊 **Today’s Links**: {leech_count_today}/{leech_limit}"
        referral_text = f"🔗 **Referrals**: {referral_count} (Next reward at {50 - (referral_count % 50)} more)"
        reply_text = (
            f"📋 **Your Plan** 📋\n\n"
            f"**Status**: {plan_status}\n"
            f"{expiry_text}\n"
            f"🌟 **Trial Status**: {trial_text}\n"
            f"{leech_usage}\n"
            f"{referral_text}\n\n"
            f"💎 Link generation is a premium-only feature. Use /upgrade to subscribe!"
        )
        await callback_query.message.reply(reply_text, disable_web_page_preview=True)
        
    elif command == "upgrade":
        await callback_query.message.reply(
            "🌟 **Go Premium!** 🌟\n\n"
            "🔹 **Benefits**:\n"
            "  - Unlimited link generations\n"
            "  - Faster processing\n\n"
            "💸 **Price**: ₹300 or $5/month\n"
            "📩 Contact [Admin](https://t.me/Pianokdt) to subscribe!"
        )
    elif command == "referral":
        result = await db_fetchone("SELECT referral_count FROM users WHERE user_id = %s", (user_id,))
        if not result:
            await callback_query.message.reply("🌟 Please use /start first!")
            return
        referral_count = result[0]
        bot_username = (await app.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user_id}"
        await callback_query.message.reply(
            f"🔗 **Your Referral Link**: `{referral_link}`\n\n"
            f"📊 **Referrals**: {referral_count}\n"
            "🎁 **Reward**: 30 days premium per 50 referrals!"
        )
    elif command == "trial":
        result = await db_fetchone("SELECT trial_claimed FROM users WHERE user_id = %s", (user_id,))
        if not result:
            await callback_query.message.reply("🌟 Please use /start first!")
            return
        if result[0]:
            await callback_query.message.reply("❌ You've already claimed your trial!")
            return
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        await db_execute(
            "UPDATE users SET subscription = 1, subscription_expires = %s, trial_claimed = TRUE WHERE user_id = %s",
            (expires_at, user_id)
        )
        await callback_query.message.reply("🎉 **24-Hour Trial Activated!** Enjoy premium features!")
        await client.send_message(ADMIN_ID, f"🔔 **Trial Activated**\nUser ID: {user_id}\nExpires: {expires_at}")
    elif command == "check_host_status":
        now = datetime.datetime.utcnow().date()
        used_sizes = {}
        result = await db_fetchall(
            "SELECT host, total_size FROM leech_stats WHERE user_id = %s AND date = %s",
            (user_id, now)
        )
        for row in result:
            used_sizes[row[0]] = row[1]
        TELEGRAM_MAX_MESSAGE_LENGTH = 4096
        message_parts = []
        current_part = ["🌐 **Daily Host Status** 🌐\n"]
        current_length = len(current_part[0])
        for host in sorted(SUPPORTED_HOSTS):
            limit = HOST_LIMITS.get(host, DEFAULT_LIMIT)
            used = used_sizes.get(host, 0)
            remaining = max(limit - used, 0)
            limit_gb = limit / (1024**3)
            used_gb = used / (1024**3)
            remaining_gb = remaining / (1024**3)
            line = f"🔹 `{host}` - Used: {used_gb:.2f} GB / {limit_gb:.2f} GB ({remaining_gb:.2f} GB left)\n"
            if current_length + len(line) > TELEGRAM_MAX_MESSAGE_LENGTH - 100:
                message_parts.append("\n".join(current_part))
                current_part = ["🌐 **Daily Host Status (Continued)** 🌐\n"]
                current_length = len(current_part[0])
            current_part.append(line)
            current_length += len(line)
        if len(current_part) > 1:
            message_parts.append("\n".join(current_part))
        for part in message_parts:
            await callback_query.message.reply(part)
        await callback_query.answer("Host status displayed!")
    await callback_query.answer()

# Referral Rewards Check
async def check_referral_rewards(user_id):
    result = await db_fetchone(
        "SELECT referral_count, referral_rewards_claimed, subscription, subscription_expires FROM users WHERE user_id = %s",
        (user_id,)
    )
    if not result:
        return
    referral_count, referral_rewards_claimed, subscription, subscription_expires = result
    total_rewards = referral_count // 50
    new_rewards = total_rewards - referral_rewards_claimed
    if new_rewards > 0:
        now = datetime.datetime.utcnow()
        days_to_add = 30 * new_rewards
        if subscription == 1 and subscription_expires and subscription_expires > now:
            new_expires = subscription_expires + datetime.timedelta(days=days_to_add)
        else:
            new_expires = now + datetime.timedelta(days=days_to_add)
        await db_execute(
            "UPDATE users SET subscription = 1, subscription_expires = %s, referral_rewards_claimed = %s WHERE user_id = %s",
            (new_expires, total_rewards, user_id)
        )
        await app.send_message(
            user_id,
            f"🎉 **Referral Reward Unlocked!** 🎉\n\nYou’ve earned {days_to_add} days of premium for referring {new_rewards * 50} users!"
        )
        await app.send_message(
            ADMIN_ID,
            f"🎈 **Referral Reward Activated**\nUser ID: {user_id}\nNew Rewards: {new_rewards} ({days_to_add} days)"
        )

# Help Command
@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    help_text = (
        "🌟 **Welcome to Leech Bot!** 🌟\n\n"
        "📋 **What I Can Do**:\n"
        "- Generate direct download links from supported file hosts.\n\n"
        "📥 **How to Use**:\n"
        "- Send a URL directly or use `/leech <URL>`.\n"
        "- Check supported hosts with `/supported_hosts`.\n\n"
        "📊 **Limits**:\n"
        "- 🆓 **Free**: 2 links/day\n"
        "- 🧪 **Trial**: 5 links/day\n"
        "- 💎 **Premium**: Unlimited\n\n"
        "📌 **Commands**:\n"
        "- `/start`: Begin and get your referral link.\n"
        "- `/myplan`: View your plan and limits.\n"
        "- `/trial`: Get a 24-hour premium trial.\n"
        "- `/upgrade`: Go premium for unlimited access.\n"
        "- `/referral`: Earn rewards by inviting friends.\n\n"
        "🆘 **Need Help?** Contact [Admin](https://t.me/Pianokdt)."
    )
    await message.reply(help_text, disable_web_page_preview=True)

# Supported Hosts Command
@app.on_message(filters.command("supported_hosts") & filters.private)
async def supported_hosts_command(client, message):
    hosts_list = "\n".join([f"🔗 `{host}`" for host in SUPPORTED_HOSTS])
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Check Host Status", callback_data="check_host_status")]
    ])
    await message.reply(
        "📋 **Supported File-Hosting Sites** 📋\n\n"
        "Send URLs from these hosts to get direct download links:\n\n"
        f"{hosts_list}\n\n"
        "🔹 **Note**: Daily size limits apply (see below for details).",
        reply_markup=reply_markup
    )

# My Plan Command
# My Plan Command
@app.on_message(filters.command("myplan") & filters.private)
async def myplan_command(client, message):
    user_id = message.from_user.id
    result = await db_fetchone(
        "SELECT subscription, subscription_expires, trial_claimed, referral_count FROM users WHERE user_id = %s",
        (user_id,)
    )
    if not result:
        await message.reply("🌟 Please use /start to register!")
        return
    subscription, subscription_expires, trial_claimed, referral_count = result
    now = datetime.datetime.utcnow()
    if subscription == 1 and subscription_expires and now < subscription_expires:
        plan_status = "💎 **Premium**"
        expiry_text = f"⏰ **Expires**: {subscription_expires.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        leech_limit = "Unlimited"
    else:
        plan_status = "🆓 **Free**"
        expiry_text = "⏰ **No active subscription**"
        leech_limit = "Not available (Premium only)"
    trial_text = "✅ **Available**" if not trial_claimed else "❌ **Used**"
    leech_result = await db_fetchone(
        "SELECT leech_count FROM leech_counts WHERE user_id = %s AND date = %s",
        (user_id, now.date())
    )
    leech_count_today = leech_result[0] if leech_result else 0
    leech_usage = f"📊 **Today’s Links**: {leech_count_today}/{leech_limit}"
    referral_text = f"🔗 **Referrals**: {referral_count} (Next reward at {50 - (referral_count % 50)} more)"
    reply_text = (
        f"📋 **Your Plan** 📋\n\n"
        f"**Status**: {plan_status}\n"
        f"{expiry_text}\n"
        f"🌟 **Trial Status**: {trial_text}\n"
        f"{leech_usage}\n"
        f"{referral_text}\n\n"
        f"💎 Link generation is a premium-only feature. Use /upgrade to subscribe!"
    )
    await message.reply(reply_text, disable_web_page_preview=True)

# Admin Commands
@app.on_message(filters.command("block") & filters.user(ADMIN_ID))
async def block_user(client, message):
    if len(message.command) < 2:
        await message.reply("Usage: /block <user_id>")
        return
    try:
        target_user_id = int(message.command[1])
        result = await db_fetchone("SELECT blocked FROM users WHERE user_id = %s", (target_user_id,))
        if not result:
            await message.reply(f"User ID {target_user_id} not found!")
            return
        if result[0]:
            await message.reply(f"User ID {target_user_id} is already blocked!")
            return
        await db_execute("UPDATE users SET blocked = TRUE WHERE user_id = %s", (target_user_id,))
        await message.reply(f"User ID {target_user_id} blocked!")
    except ValueError:
        await message.reply("Please provide a numeric User ID!")

@app.on_message(filters.command("unblock") & filters.user(ADMIN_ID))
async def unblock_user(client, message):
    if len(message.command) < 2:
        await message.reply("Usage: /unblock <user_id>")
        return
    try:
        target_user_id = int(message.command[1])
        result = await db_fetchone("SELECT blocked FROM users WHERE user_id = %s", (target_user_id,))
        if not result:
            await message.reply(f"User ID {target_user_id} not found!")
            return
        if not result[0]:
            await message.reply(f"User ID {target_user_id} is not blocked!")
            return
        await db_execute("UPDATE users SET blocked = FALSE WHERE user_id = %s", (target_user_id,))
        await message.reply(f"User ID {target_user_id} unblocked!")
    except ValueError:
        await message.reply("Please provide a numeric User ID!")

@app.on_message(filters.command("au") & filters.user(ADMIN_ID))
async def add_user_start(client, message):
    if len(message.command) < 2:
        await message.reply("Usage: /au <user_id>")
        return
    try:
        target_user_id = int(message.command[1])
        result = await db_fetchone("SELECT subscription FROM users WHERE user_id = %s", (target_user_id,))
        if not result:
            await db_execute("INSERT INTO users (user_id, subscription) VALUES (%s, 0)", (target_user_id,))
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("7 Days", callback_data=f"sub_{target_user_id}_7")],
            [InlineKeyboardButton("15 Days", callback_data=f"sub_{target_user_id}_15")],
            [InlineKeyboardButton("30 Days", callback_data=f"sub_{target_user_id}_30")]
        ])
        await message.reply(f"Choose subscription period for User ID {target_user_id}:", reply_markup=reply_markup)
    except ValueError:
        await message.reply("Please provide a numeric User ID!")

@app.on_callback_query(filters.regex(r"sub_(\d+)_(\d+)"))
async def set_subscription_period(client, callback_query):
    target_user_id = int(callback_query.data.split("_")[1])
    days = int(callback_query.data.split("_")[2])
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    await db_execute(
        "UPDATE users SET subscription = 1, subscription_expires = %s WHERE user_id = %s",
        (expires_at, target_user_id)
    )
    await callback_query.answer(f"Subscription set for {days} days!")
    await callback_query.message.reply(
        f"✅ **Subscription Activated** ✅\n\nUser ID: {target_user_id}\nDuration: {days} days\nExpires: {expires_at}"
    )

@app.on_message(filters.command("ru") & filters.user(ADMIN_ID))
async def remove_user(client, message):
    try:
        user_id = int(message.text.split()[1])
        await db_execute("UPDATE users SET subscription = 0 WHERE user_id = %s", (user_id,))
        await message.reply(f"✅ User ID **{user_id}** subscription deactivated!")
    except Exception as e:
        await message.reply(f"📋 **Usage:** `/ru <user_id>`\n❌ **Error:** {str(e)}")

@app.on_message(filters.command("announce") & filters.user(ADMIN_ID))
async def announce_to_all_users(client, message):
    announcement = message.text[len("/announce"):].strip()
    if not announcement:
        await message.reply("📢 **Oops!** Please provide a message to announce.")
        return
    users = await db_fetchall("SELECT user_id FROM users")
    if not users:
        await message.reply("📢 **No Users Found** No one to announce to yet!")
        return
    await message.reply("📢 **Starting Announcement** Sending to all users...")
    success_count = 0
    fail_count = 0
    for user in users:
        user_id = user[0]
        try:
            await client.send_message(user_id, announcement)
            success_count += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await client.send_message(user_id, announcement)
                success_count += 1
            except Exception as e:
                print(f"Failed to send message to {user_id} after flood wait: {e}")
                fail_count += 1
        except Exception as e:
            print(f"Failed to send message to {user_id}: {e}")
            fail_count += 1
        await asyncio.sleep(0.1)
    await message.reply(
        f"📢 **Announcement Complete!** \n"
        f"Sent to **{success_count} users** successfully.\n"
        f"Failed to send to **{fail_count} users**."
    )

# Run the Bot
def run_bot():
    init_db()
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    app.run()
    logger.info("Bot started successfully!")
    idle()

if __name__ == "__main__":
    run_bot()
