import logg
from urllib.parse import urlparse
import json, os
import math
from datetime import datetime
import requests
from pywebpush import webpush, WebPushException
import db
from settings import VAPID_CLAIMS_SUB_MAILTO, MESSAGE_FOR_DAY

DER_BASE64_ENCODED_PRIVATE_KEY_FILE_PATH = os.path.join(os.getcwd(),"private_key.txt")
DER_BASE64_ENCODED_PUBLIC_KEY_FILE_PATH = os.path.join(os.getcwd(),"public_key.txt")

VAPID_PRIVATE_KEY = open(DER_BASE64_ENCODED_PRIVATE_KEY_FILE_PATH, "r+").readline().strip("\n")
VAPID_PUBLIC_KEY = open(DER_BASE64_ENCODED_PUBLIC_KEY_FILE_PATH, "r+").read().strip("\n")

VAPID_CLAIMS = {
"sub": f"mailto:{VAPID_CLAIMS_SUB_MAILTO}"
}

def send_web_push(subscription_information, title, opts_str):
    data = {"title": title}
    if opts_str:
        options = json.loads(opts_str)
        # Visual
        if "body" in options: data["body"] = options["body"] # <String>
        if "icon" in options: data["icon"] = options["icon"] # <URL String>
        if "image" in options: data["image"] = options["image"] # <URL String>
        if "badge" in options: data["badge"] = options["badge"] # <URL String>
        if "dir" in options: data["dir"] = options["dir"] # <String of 'auto' | 'ltr' | 'rtl'  >

        # Visual & Behavioral
        if "timestamp" in options: data["timestamp"] = options["timestamp"] # <Long>
        if "actions" in options: data["actions"] = options["actions"] # <Array of String>
        if "data" in options: data["data"] = options["data"] # <Anything>

        # Beahvioral Options
        if "tag" in options: data["tag"] = options["tag"] # <String>
        if "requireInteraction" in options: data["requireInteraction"] = options["requireInteraction"] # <boolean>
        if "renotify" in options: data["renotify"] = options["renotify"] # <Boolean>
        if "vibrate" in options: data["vibrate"] = options["vibrate"] # <Array of Integers>
        if "sound" in options: data["sound"] = options["sound"] # <URL String>
        if "silent" in options: data["silent"] = options["silent"] # <Boolean>

    # Add aud if is google
    parsed_url = urlparse(subscription_information.get("endpoint"))
    VAPID_CLAIMS["aud"] = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    return webpush(
        subscription_info=subscription_information,
        data=json.dumps(data),
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims=VAPID_CLAIMS
    )    

def push_last_message(logger, schedule):
    logger.info("Check new message")
    message = db.get_last_message()

    if message:
        logger.info(f"Found one message in queue, ready for push: {message}")
        log = {
            "title": message["title"],
            "options": message["options"],
            "total_subscribers": 0,
            "total_pushes": 0,
            "start_time": datetime.now(),
            "end_time": ""
        }
        
        # split schedules in pages by hour:minute (already ordered from the query)
        pages = len(db.get_schedule_by_day(schedule['day']))
        logger.debug(f"There are {pages} pages from schedules")
        tmp_index = 0
        page_index = 1
        for single_shedule in db.get_schedule_by_day(schedule['day']):
            tmp_index = tmp_index + 1
            if single_shedule['hour'] == schedule['hour'] and single_shedule['minute'] == schedule['minute']:
                page_index = tmp_index
        
        # take the total size of all subscribers
        total = db.get_total_subscribers()
        logger.debug(f"Total subscribers {total}")

        page_index = math.ceil(page_index / MESSAGE_FOR_DAY)
        logger.debug(f"Page index: {page_index}")

        # calculate page size by total and page
        page_size = math.ceil(total / pages) * MESSAGE_FOR_DAY
        logger.debug(f"Page size: {page_size}")

        subscribers = db.get_subscribers_paged(page_index, page_size)
        logger.info(f"There are {len(subscribers)}, {subscribers}")

        for subscriber in subscribers:
            log["total_subscribers"] += 1    
            token = {
                "endpoint": subscriber["endpoint"],
                "expirationTime": subscriber["expiration_time"],
                "keys": {
                    "p256dh": subscriber["keys_p256dh"],
                    "auth": subscriber["keys_auth"]
                }}
            
            try:
                logger.info(f"Send push request: {token}, {message}")
                push_response = send_web_push(token, message["title"], message["options"])
                logger.info(f"Received push response: {push_response}")

                if push_response.status_code in (200, 201):
                    log["total_pushes"] += 1
                    
            except WebPushException as e:
                logger.error(e)
                # remove subscriber
                logger.warning(f"Remove invalid subscriber {subscriber}")
                db.remove_subscriber(subscriber)
            
        # set queue.pushed = 1
        logger.debug(f"Set message with id={message['id']} as pushed")
        db.set_message_pushed(message["id"])

        # add a row in log_messagges
        log["end_time"] = datetime.now()
        logger.debug(f"Log pushing details: {log}")
        db.add_log_message(log)
        logger.info("Push Job end.")

    else:
        logger.info("The queue is empty")
