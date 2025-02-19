import os
import datetime
import asyncio
import pytz
from telethon import TelegramClient, events
from pymongo import MongoClient

# ایجاد دایرکتوری برای ذخیره عکس‌های پروفایل (در صورت عدم وجود)
os.makedirs("profile_photos", exist_ok=True)

# اتصال به MongoDB
# mongo_client = MongoClient("mongodb://localhost:27017/")

mongo_client = MongoClient("mongodb://admin:Momgodbpass0200Yashar@mongo:27017/telegram_data?authSource=admin")
db = mongo_client["telegram_data"]
messages_collection = db["messages"]
chats_collection = db["chats"]

# ایجاد ایندکس‌ها
messages_collection.create_index([("message_id", 1)], unique=True)
chats_collection.create_index([("chat_id", 1)], unique=True)

# تنظیمات API تلگرام
API_ID = "22435091"
API_HASH = "da17125c6dd25732caad68a778f69568"
PHONE_NUMBER = "+989336531403"

client = TelegramClient('session_name', API_ID, API_HASH)
tehran_tz = pytz.timezone("Asia/Tehran")


async def update_chat_details(chat):
    """
    اطلاعات کاربر (مانند username و عکس پروفایل) را دریافت و در دیتابیس ذخیره می‌کند.
    """
    chat_id = chat.id if hasattr(chat, 'id') else None
    if not chat_id:
        return

    chat_username = getattr(chat, 'username', None)
    profile_photo_path = None
    if hasattr(chat, 'photo') and chat.photo:
        try:
            profile_photo_path = await client.download_profile_photo(chat, file=f"profile_photos/{chat_id}.jpg")
        except Exception as e:
            print(f"Error downloading profile photo for chat {chat_id}: {e}")

    chat_update_data = {
        "username": chat_username,
        "profile_photo": profile_photo_path
    }
    chats_collection.update_one({"chat_id": chat_id}, {"$set": chat_update_data}, upsert=True)


def save_messages(chat_name, chat_id, messages):
    """
    ذخیره پیام‌های دریافت شده و آپدیت اطلاعات چت (مانند آخرین پیام و تاریخش).
    """
    # پیدا کردن جدیدترین پیام جهت تعیین آخرین پیام
    last_msg = None
    for msg in messages:
        if msg.date:
            if last_msg is None or msg.date > last_msg.date:
                last_msg = msg

    if last_msg and last_msg.date:
        last_message_date = last_msg.date.astimezone(tehran_tz)
        last_message_text = last_msg.text if last_msg.text else ""
    else:
        last_message_date = None
        last_message_text = ""

    chat_data = {
        "chat_id": chat_id,
        "chat_name": chat_name,
        "last_message_date": last_message_date.strftime("%Y-%m-%d %H:%M:%S") if last_message_date else None,
        "last_message_text": last_message_text
    }
    try:
        chats_collection.update_one({"chat_id": chat_id}, {"$set": chat_data}, upsert=True)
        print(f"Updated chat: {chat_name} - Last message at: {last_message_date}")
    except Exception as e:
        print(f"Chat update error: {e}")

    # ذخیره هر پیام دریافت شده
    for msg in messages:
        if msg.text:
            update_message_data(msg, chat_id, chat_name)


def update_message_data(msg, chat_id, chat_name):
    existing = messages_collection.find_one({"message_id": msg.id})
    if existing:
        if msg.edit_date:
            handle_edited_message(existing, msg)
        return
    try:
        message_data = build_message_object(msg, chat_id, chat_name)
        messages_collection.insert_one(message_data)
    except Exception as e:
        print("Error inserting message:", e)


def handle_edited_message(existing, msg):
    text_list = existing["text"]
    if isinstance(text_list, str):
        text_list = [text_list]
    if msg.text not in text_list:
        text_list.append(msg.text)
        messages_collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "text": text_list,
                "is_edited": True,
                "edit_date": msg.edit_date.strftime("%Y-%m-%d %H:%M:%S")
            }}
        )


def build_message_object(msg, chat_id, chat_name):
    msg_date = msg.date.astimezone(tehran_tz) if msg.date else None
    edit_date = msg.edit_date.astimezone(tehran_tz) if msg.edit_date else None

    return {
        "chat_id": chat_id,
        "chat_name": chat_name,
        "message_id": msg.id,
        "sender_id": msg.sender_id,
        "username": [],  # در صورت نیاز اطلاعات اضافی اضافه شود
        "sender_username": getattr(msg.sender, 'username', None),
        "is_outgoing": msg.out,
        "text": [msg.text],
        "date": msg_date.strftime("%Y-%m-%d %H:%M:%S") if msg_date else None,
        "reply_to_msg_id": msg.reply_to_msg_id,
        "is_edited": bool(msg.edit_date),
        "edit_date": edit_date.strftime("%Y-%m-%d %H:%M:%S") if edit_date else None,
        "redFlag": False,
        "mantegh": [],
    }


async def initial_data_load():
    """
    در اولین اجرا، تمام چت‌ها و پیام‌های موجود (قدیمی) از تلگرام دریافت و ذخیره می‌شوند.
    """
    dialogs = await client.get_dialogs()
    for dialog in dialogs:
        chat = dialog.entity
        chat_id = chat.id
        chat_name = getattr(chat, "title", getattr(chat, "first_name", "Private Chat"))

        # ذخیره اطلاعات پروفایل و سایر جزئیات چت
        await update_chat_details(chat)
        # دریافت پیام‌های این چت (limit قابل تغییر است)
        messages = await client.get_messages(chat_id, limit=100)
        if messages:
            save_messages(chat_name, chat_id, messages)
    print("Initial data load completed.")


@client.on(events.NewMessage)
async def new_message_handler(event):
    """ ذخیره پیام جدید و افزایش unread_count به همراه آپدیت آخرین پیام """
    msg = event.message
    chat = await event.get_chat()
    chat_id = chat.id
    chat_name = getattr(chat, "title", getattr(chat, "first_name", "Private Chat"))

    # ذخیره پیام جدید در دیتابیس
    messages_collection.insert_one(build_message_object(msg, chat_id, chat_name))

    update_data = {
        "$set": {
            "last_message_text": msg.text if msg.text else "",
            "last_message_date": msg.date.astimezone(tehran_tz).strftime("%Y-%m-%d %H:%M:%S") if msg.date else None
        }
    }

    # اگر پیام outgoing باشد unread_count را 0 کن، وگرنه 1 اضافه کن
    if msg.out:
        update_data["$set"]["unread_count"] = 0  # چون خودمان پیام را ارسال کردیم
    else:
        update_data["$inc"] = {"unread_count": 1}  # پیام از طرف دیگر دریافت شده

    chats_collection.update_one({"chat_id": chat_id}, update_data, upsert=True)
    print(f"🔵 New message in {chat_name} saved. (Outgoing: {msg.out})")


@client.on(events.MessageEdited)
async def message_edited_handler(event):
    msg = event.message
    try:
        chat = await event.get_chat()
        chat_name = getattr(chat, "title", getattr(chat, "first_name", "Private Chat"))
        chat_id = chat.id
    except Exception as e:
        print("Error fetching chat info:", e)
        chat_name = "Unknown Chat"
        chat_id = event.chat_id
        chat = None

    if chat:
        await update_chat_details(chat)

    update_message_data(msg, chat_id, chat_name)


@client.on(events.MessageDeleted)
async def message_deleted_handler(event):
    for msg_id in event.deleted_ids:
        messages_collection.update_one(
            {"message_id": msg_id},
            {"$set": {"redFlag": True}}
        )
        print(f"Message {msg_id} flagged as deleted.")


@client.on(events.MessageRead)
async def message_read_handler(event):
    """
    زمانی که کاربر پیام‌های یک چت را می‌خواند (مثلاً با باز کردن چت)،
    تعداد unread_count آن چت به 0 تنظیم می‌شود.
    """
    chat_id = getattr(event, 'chat_id', None)
    if chat_id is None:
        chat_id = getattr(event, 'peer_id', None)
    if chat_id is None:
        print("Unable to determine chat_id for MessageRead event")
        return

    chats_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"unread_count": 0}},
        upsert=True
    )
    print(f"Reset unread_count for chat {chat_id} due to read event.")


async def main():
    await client.start(PHONE_NUMBER)
    print("Connected as user. Starting initial data load...")
    await initial_data_load()
    print("Initial data load completed. Waiting for new messages and read events...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())